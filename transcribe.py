#!/usr/bin/env python3
"""Two-speaker Hindi/Hinglish voice-memo transcriber for Apple Silicon (MLX).

Pipeline
  1. ffmpeg  -> 16 kHz mono wav (no denoising / loudness normalisation: both hurt accuracy)
  2. speech mask from the pyannote segmentation model (only used for "is anyone talking")
  3. speaker labels: 1 s windows (0.5 s hop) scored against two voice profiles built from
     time ranges YOU confirm by listening (--speaker1 / --speaker2), using two embedding models
  4. cut at every speaker change, transcribe each piece with whisper-large-v3 on the GPU:
       English  = Hindi -> English translation (most robust on accented Hinglish)
       Hinglish = Roman-script transcription steered by a domain prompt (--prompt-file)
  5. loop/hallucination guards (no per-word find/replace: that only overfits one recording)

Usage
  python transcribe.py memo.m4a --speaker1 1.6-11.9 --speaker1 16.1-30.5 --speaker2 12.8-14.9 \
      --prompt-file examples/prompt.example.txt
"""
import argparse
import json
import re
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf
import sherpa_onnx
import mlx_whisper

WHISPER = "mlx-community/whisper-large-v3-mlx"
SR = 16000
LOOP = re.compile(r"(\w+(?:\s+\w+){0,3}?)(?:\s+\1){3,}")  # same 1-4 word phrase repeated 4+ times


def parse_range(s):
    a, b = s.split("-")
    return float(a), float(b)


def loopy(t):
    return bool(LOOP.search(re.sub(r"[^\w\s']", " ", t.lower())))


def unit(v):
    return v / np.linalg.norm(v)


def windows(a, b, w=1.0, h=0.5):
    out, t = [], a
    while t + w <= b + 1e-6:
        out.append((round(t, 2), round(t + w, 2)))
        t += h
    return out


class Embedder:
    def __init__(self, path):
        cfg = sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(path), num_threads=4)
        self.ex = sherpa_onnx.SpeakerEmbeddingExtractor(cfg)

    def __call__(self, x, a, b):
        s = self.ex.create_stream()
        s.accept_waveform(sample_rate=SR, waveform=x[int(a * SR):int(b * SR)])
        s.input_finished()
        return unit(np.array(self.ex.compute(s)))


def speech_mask(x, models):
    """Seconds-resolution boolean mask of where speech happens (speaker ids are ignored)."""
    cfg = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                model=str(models / "sherpa-onnx-pyannote-segmentation-3-0" / "model.onnx")),
            num_threads=4),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=str(models / "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"), num_threads=4),
        clustering=sherpa_onnx.FastClusteringConfig(num_clusters=2, threshold=0.5),
        min_duration_on=0.3, min_duration_off=0.5)
    sd = sherpa_onnx.OfflineSpeakerDiarization(cfg)
    mask = np.zeros(int(len(x) / SR * 10) + 2, bool)
    for s in sd.process(x).sort_by_start_time():
        mask[int(s.start * 10):int(s.end * 10)] = True
    return mask


def label_pieces(x, models, spk1, spk2):
    dur = len(x) / SR
    mask = speech_mask(x, models)
    W = [w for w in windows(0, dur) if mask[int(w[0] * 10):int(w[1] * 10)].mean() > 0.6]
    embs = {
        "eres2net": Embedder(models / "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"),
        "titanet": Embedder(models / "nemo_en_titanet_small.onnx"),
    }
    seed = lambda ranges, n: [embs[n](x, *w) for r in ranges for w in windows(*r)]
    S1 = {n: seed(spk1, n) for n in embs}
    S2 = {n: seed(spk2, n) for n in embs}
    WE = {n: [embs[n](x, *w) for w in W] for n in embs}
    c1 = {n: unit(np.mean(S1[n], 0)) for n in embs}
    c2 = {n: unit(np.mean(S2[n], 0)) for n in embs}

    def margins():  # > 0 means closer to speaker 1
        return np.mean([[v @ c1[n] - v @ c2[n] for v in WE[n]] for n in embs], axis=0)

    # one bounded refinement: grow ONLY the speaker-2 profile with windows that are clearly
    # not speaker 1 (self-training both profiles drifted toward the louder voice)
    m0 = margins()
    sure = [i for i, m in enumerate(m0)
            if m < -0.15 and all(WE[n][i] @ c1[n] - WE[n][i] @ c2[n] < 0 for n in embs)]
    for n in embs:
        c2[n] = unit(np.mean(S2[n] + [WE[n][i] for i in sure], 0))
    lab = (margins() > 0).astype(int)  # 1 = speaker 1, 0 = speaker 2
    lab = np.array([int(round(np.median(lab[max(0, i - 1):i + 2]))) for i in range(len(lab))])

    runs = []
    for (a, b), l in zip(W, lab):
        if runs and runs[-1][2] == l and a - runs[-1][1] <= 0.5:
            runs[-1][1] = b
        else:
            runs.append([a, b, int(l)])
    pieces = [r for r in runs if r[1] - r[0] >= 0.9]
    for r in pieces:
        r[0] += 0.15 if r[0] > 0 else 0
        r[1] -= 0.15
    return [(float(a), float(b), 1 if l == 1 else 2) for a, b, l in pieces]


def transcribe_piece(clip, mode, prompt):
    base = dict(verbose=None, condition_on_previous_text=False,
                compression_ratio_threshold=2.4, logprob_threshold=-1.0)
    kw = (dict(language="hi", task="translate") if mode == "en"
          else dict(language="en", task="transcribe", **({"initial_prompt": prompt} if prompt else {})))
    for temp in ((0.0, 0.2, 0.4, 0.6), (0.4,), (0.8,)):  # retry looped decodes hotter
        t = mlx_whisper.transcribe(clip, path_or_hf_repo=WHISPER, temperature=temp, **base, **kw)["text"].strip()
        if not loopy(t):
            break
    else:
        return "[unclear - overlapping speech]"
    # whisper invents long text on sub-1.3 s clips; drop it
    return "" if (len(clip) / SR < 1.3 and len(t.split()) > 6) else t


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("audio")
    ap.add_argument("--speaker1", action="append", required=True, type=parse_range,
                    help="START-END seconds you confirmed are speaker 1 (repeatable, ~20 s total is plenty)")
    ap.add_argument("--speaker2", action="append", required=True, type=parse_range,
                    help="START-END seconds you confirmed are speaker 2 (repeatable; even 2 s works)")
    ap.add_argument("--models-dir", default="models", type=Path)
    ap.add_argument("--out-dir", default="out", type=Path)
    ap.add_argument("--modes", default="en,hing", help="comma list of: en, hing")
    ap.add_argument("--prompt-file", type=Path, help="text file: Hinglish sentence(s) with your domain terms")
    a = ap.parse_args()

    audio = Path(a.audio).expanduser().resolve()
    a.out_dir.mkdir(parents=True, exist_ok=True)
    wav = a.out_dir / "audio16k.wav"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(audio), "-ac", "1", "-ar", str(SR), str(wav)],
                   check=True)
    x = sf.read(wav, dtype="float32")[0]

    pieces = label_pieces(x, a.models_dir, a.speaker1, a.speaker2)
    json.dump(pieces, open(a.out_dir / "pieces.json", "w"))
    print(f"{len(pieces)} pieces | speaker1 {sum(b - s for s, b, l in pieces if l == 1):.0f}s | "
          f"speaker2 {sum(b - s for s, b, l in pieces if l == 2):.0f}s", flush=True)

    prompt = a.prompt_file.read_text().strip() if a.prompt_file else None
    for mode in a.modes.split(","):
        lines = []
        for s, b, l in pieces:
            clip = x[max(0, int((s - 0.2) * SR)):int((b + 0.2) * SR)]
            t = transcribe_piece(clip, mode, prompt)
            if t:
                lines.append(f"[{int(s // 60)}:{s % 60:04.1f}] Speaker {l}: {t}")
        name = {"en": "English", "hing": "Hinglish"}[mode]
        out = a.out_dir / f"{audio.stem}.{name}.txt"
        out.write_text("\n".join(lines), encoding="utf-8")
        print("wrote", out, flush=True)


if __name__ == "__main__":
    main()
