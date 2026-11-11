#!/usr/bin/env python3
"""Two-speaker Hindi/Hinglish voice-memo transcriber for Apple Silicon (MLX).

Pipeline
  1. ffmpeg  -> 16 kHz mono wav (no denoising / loudness normalisation: both hurt accuracy)
  2. speech mask from the pyannote segmentation model (only used for "is anyone talking")
  3. speakers are auto-detected: 1 s windows (0.5 s hop) -> two voice-embedding models ->
     spectral clustering with the number of speakers chosen by eigengap (override: --num-speakers)
  4. cut at every speaker change, transcribe each piece with whisper-large-v3 on the GPU:
       English  = Hindi -> English translation (most robust on accented Hinglish)
       Hinglish = Roman-script transcription steered by a domain prompt (--prompt-file)
  5. loop/hallucination guards (no per-word find/replace: that only overfits one recording)

Usage
  python transcribe.py memo.m4a
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

# Isolated (-I) launches omit the script directory.
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from tools import ffmpeg
from romanize import romanize

import numpy as np
from scipy.cluster.vq import kmeans2
import soundfile as sf

WHISPER = "mlx-community/whisper-large-v3-mlx"
DEFAULT_GLOSSARY = Path.home() / ".voice-memo-glossary.txt"   # personal; lives outside the repo
LLM_INSTRUCTIONS = """# Voice memo transcript: clean-up task

Two independent machine transcripts of the same Hindi/Hinglish voice memo are given for every
segment. Both contain errors, and usually in different places: the Hinglish one is closer to the
spoken words but misspells and mishears; the English one is a translation and can smooth over or
drop things.

Produce a cleaned transcript:
- Use BOTH versions plus the surrounding segments to work out what was actually said.
- Fix misheard words, spellings and names using context and the glossary.
- Keep the speaker's own wording, tone and Hinglish; do not summarise or polish.
- Never add facts that neither version supports. If a word or phrase is uncertain, keep the closest
  original wording and mark it [?]. If a segment is unusable, write [unclear].
- Keep timestamps and speaker labels. Output the cleaned Hinglish first, then a faithful English
  translation, then a short list of the corrections you were least sure about.
"""
# Provisional: calibrated on two recordings (true 2 speakers -> 0.143, one speaker split in two -> 0.068).
SINGLE_SPEAKER_SEPARATION = 0.10
# Without any Hinglish text in the prompt, Whisper writes English instead of Roman-script Hindi.
DEFAULT_HINGLISH_PROMPT = (
    "Haan toh main bol raha tha ki kal meeting mein hum plan aur timeline discuss karenge. "
    "Theek hai, phir main sabko update bhej deta hoon. Aap bataiye, aapka kya khayal hai? "
    "Nahi nahi, wo ho jayega, bas ek baar check kar lete hain. Okay, chalo phir baad mein baat karte hain."
)
SR = 16000
LOOP = re.compile(r"(\w+(?:\s+\w+){0,7}?)(?:\s+\1){3,}")  # same 1-8 word phrase repeated 4+ times


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
        import sherpa_onnx
        cfg = sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(path), num_threads=4)
        self.ex = sherpa_onnx.SpeakerEmbeddingExtractor(cfg)

    def __call__(self, x, a, b):
        s = self.ex.create_stream()
        s.accept_waveform(sample_rate=SR, waveform=x[int(a * SR):int(b * SR)])
        s.input_finished()
        return unit(np.array(self.ex.compute(s)))


def segmentation_model(models):
    """Construct the reusable speech detector."""
    import sherpa_onnx
    cfg = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                model=str(models / "sherpa-onnx-pyannote-segmentation-3-0" / "model.onnx")),
            num_threads=4),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=str(models / "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"), num_threads=4),
        clustering=sherpa_onnx.FastClusteringConfig(num_clusters=2, threshold=0.5),
        min_duration_on=0.3, min_duration_off=0.5)
    return sherpa_onnx.OfflineSpeakerDiarization(cfg)


def speech_mask(x, models, detector=None):
    """Seconds-resolution boolean mask of where speech happens (speaker ids are ignored)."""
    sd = detector if detector is not None else segmentation_model(models)
    mask = np.zeros(int(len(x) / SR * 10) + 2, bool)
    for s in sd.process(x).sort_by_start_time():
        mask[int(s.start * 10):int(s.end * 10)] = True
    return mask


def spectral_cluster(sim, k_forced=None, max_k=8):
    """Spectral clustering; k from the largest eigengap (Park et al. auto-tuned neighbour count)."""
    n = len(sim)
    best = None
    for f in (0.02, 0.04, 0.06, 0.1, 0.15, 0.2):
        p = min(max(3, int(f * n)), n - 1)
        A = np.zeros_like(sim)
        idx = np.argsort(-sim, axis=1)[:, :p]
        for i in range(n):
            A[i, idx[i]] = sim[i, idx[i]]
        A = np.clip((A + A.T) / 2, 0, None)
        np.fill_diagonal(A, 0)
        d = A.sum(1) + 1e-9
        ev, vec = np.linalg.eigh(np.eye(n) - A / np.sqrt(d[:, None] * d[None, :]))
        gaps = np.diff(ev[:max_k + 1])
        r = (p / n) / max(gaps.max(), 1e-9)
        if best is None or r < best[0]:
            best = (r, int(np.argmax(gaps)) + 1, vec)
    _, k, vec = best
    k = k_forced or max(k, 2)          # a conversation has at least two people
    U = vec[:, :k]
    U = U / np.linalg.norm(U, axis=1, keepdims=True)
    _, lab = kmeans2(U, k, minit="++", seed=0)
    return lab, k


def window_sim(x, models):
    """Speech windows and their pairwise voice similarity (mean cosine over two embedding models)."""
    dur = len(x) / SR
    mask = speech_mask(x, models)
    W = [w for w in windows(0, dur) if mask[int(w[0] * 10):int(w[1] * 10)].mean() > 0.6]
    if len(W) < 8:
        return W, None
    embs = [Embedder(models / "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"),
            Embedder(models / "nemo_en_titanet_small.onnx")]
    E = [np.array([e(x, *w) for w in W]) for e in embs]
    return W, np.mean([m @ m.T for m in E], axis=0)


def separation(sim, lab):
    """Mean within-cluster minus mean cross-cluster similarity: ~0 means 'one voice split in two'."""
    same = lab[:, None] == lab[None, :]
    off = ~np.eye(len(lab), dtype=bool)
    return float(sim[same & off].mean() - sim[~same].mean())


def split_long(pieces, x, max_len=25.0):
    """Whisper decodes ~30 s at a time and silently drops text from longer clips (a monologue is
    one long piece), so cut anything over max_len at the quietest 0.3 s just before the cap."""
    out = []
    for a, b, l in pieces:
        while b - a > max_len:
            best = None
            for t in np.arange(a + max_len - 4.0, a + max_len, 0.1):
                seg = x[int(t * SR):int((t + 0.3) * SR)]
                e = float(np.sqrt(np.mean(seg ** 2))) if len(seg) else 1e9
                if best is None or e < best[0]:
                    best = (e, t + 0.15)
            out.append((a, best[1], l))
            a = best[1]
        out.append((a, b, l))
    return out


def label_pieces(x, models, num_speakers=None):
    W, sim = window_sim(x, models)
    if sim is None:
        return [(w[0], w[1], 1) for w in W]
    lab, k = spectral_cluster(sim, num_speakers)
    if not num_speakers:
        sep = separation(sim, lab)
        print(f"cluster separation {sep:.3f} (single-speaker cutoff {SINGLE_SPEAKER_SEPARATION})", flush=True)
        if sep < SINGLE_SPEAKER_SEPARATION:        # clusters aren't really different voices
            lab, k = np.zeros(len(lab), dtype=int), 1
    # mode filter over 3 windows (keep own label on ties)
    sm = []
    for i in range(len(lab)):
        c = np.bincount(lab[max(0, i - 1):i + 2], minlength=k)
        sm.append(lab[i] if c[lab[i]] == c.max() else int(c.argmax()))
    # number speakers by first appearance
    order = {}
    for l in sm:
        order.setdefault(l, len(order) + 1)
    runs = []
    for (a, b), l in zip(W, sm):
        l = order[l]
        if runs and runs[-1][2] == l and a - runs[-1][1] <= 0.5:
            runs[-1][1] = b
        else:
            runs.append([a, b, l])
    pieces = [r for r in runs if r[1] - r[0] >= 0.9]
    for r in pieces:
        r[0] += 0.15 if r[0] > 0 else 0
        r[1] -= 0.15
    print(f"detected {k} speakers", flush=True)
    return split_long([(float(a), float(b), int(l)) for a, b, l in pieces], x)


def filtered_result(result, prompt=None):
    """Remove silence and (for prompted decodes) four-word prompt echoes."""
    words = lambda text: re.sub(r"[^\w\s]", "", text.lower()).split()
    prompt_words = words(prompt or "")
    runs = {tuple(prompt_words[i:i + 4]) for i in range(len(prompt_words) - 3)}

    def keep(segment):
        if (segment.get('no_speech_prob') is not None and
                segment.get('avg_logprob') is not None and
                segment['no_speech_prob'] > .6 and segment['avg_logprob'] < -1.):
            return False
        tokens = words(segment['text'])
        return not any(tuple(tokens[i:i + 4]) in runs for i in range(len(tokens) - 3))

    segments = result.get('segments', [])
    kept = [segment for segment in segments if keep(segment)]
    if len(kept) == len(segments):
        return result
    return dict(result, segments=kept, text=' '.join(s['text'].strip() for s in kept))


def _decode(clip, kw, temperature, romanized=False, echo_of=None):
    """One guarded decode: hotter retries for loops, then the silence / prompt-echo filter."""
    import mlx_whisper
    base = dict(verbose=None, condition_on_previous_text=False,
                compression_ratio_threshold=2.4, logprob_threshold=-1.0)
    temperatures = ((0.0, 0.2, 0.4, 0.6), (0.4,), (0.8,)) if temperature is None else (temperature,)
    for temp in temperatures:  # retry looped decodes hotter
        result = mlx_whisper.transcribe(clip, path_or_hf_repo=WHISPER, temperature=temp, **base, **kw)
        if romanized:
            result = dict(result, text=romanize(result["text"]),
                          segments=[dict(s, text=romanize(s["text"])) for s in result.get("segments", [])])
        if not loopy(result["text"].strip()):
            return filtered_result(result, echo_of or kw.get('initial_prompt'))
    return None


SPARSE_WORDS_PER_SECOND = 0.5   # a real utterance is 2-3 words/s; below this Whisper stopped early
SPARSE_MIN_SECONDS = 8.0


def transcribe_result(clip, mode, prompt, *, temperature=None):
    """Decode with the shared settings and loop guard, retaining Whisper timestamps.

    Hinglish is decoded in Roman script, steered by a prompt (this keeps English terms such as
    "save and draft" readable). On unclear audio that decode can return only an echo of the prompt
    (filtered to nothing), a loop, or one short sentence for a long clip. In those cases the same
    clip is decoded once more as plain Hindi without a prompt and romanized; the fuller result wins.
    """
    # File paths make mlx_whisper invoke a PATH binary; only decoded arrays are allowed.
    if not isinstance(clip, np.ndarray):
        raise TypeError("Whisper audio must be a numpy array")
    if mode == "en":
        result = _decode(clip, dict(language="hi", task="translate"), temperature)
    else:
        prompt = prompt or DEFAULT_HINGLISH_PROMPT
        result = _decode(clip, dict(language="en", task="transcribe", initial_prompt=prompt), temperature)
        seconds = len(clip) / SR
        count = lambda r: len(r["text"].split()) if r else 0
        if temperature is None and (count(result) == 0 or (
                seconds >= SPARSE_MIN_SECONDS and count(result) < SPARSE_WORDS_PER_SECOND * seconds)):
            fallback = _decode(clip, dict(language="hi", task="transcribe"), None, romanized=True, echo_of=prompt)
            if count(fallback) > count(result):
                result = fallback
    if result is None:
        t = "[unclear - overlapping speech]"
        return dict(text=t, segments=[dict(start=0, end=len(clip) / SR, text=t)])
    return result


def transcribe_piece(clip, mode, prompt):
    return transcribe_result(clip, mode, prompt)["text"].strip()


def bundle_text(rows, glossary):
    """rows contain (start, speaker, Hinglish, English)."""
    segs = "\n\n".join(
        f"[{int(s // 60)}:{s % 60:04.1f}] Speaker {l}\n  Hinglish: {h or '[nothing recognised]'}\n  English:  {e or '[nothing recognised]'}"
        for s, l, h, e in rows)
    return (LLM_INSTRUCTIONS + "\n## Glossary (context, not replacements)\n" + glossary +
            "\n\n## Segments\n\n" + segs + "\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("audio")
    ap.add_argument("--num-speakers", type=int, help="override the auto-detected speaker count")
    ap.add_argument("--labels-only", action="store_true", help="only detect speakers, skip transcription")
    ap.add_argument("--models-dir", default="models", type=Path)
    ap.add_argument("--out-dir", default="out", type=Path)
    ap.add_argument("--modes", default="en,hing", help="comma list of: en, hing")
    ap.add_argument("--glossary-file", type=Path,
                    help=f"names/terms for the LLM bundle (default: {DEFAULT_GLOSSARY} if it exists)")
    ap.add_argument("--prompt-file", type=Path, help="text file: Hinglish sentence(s) with your domain terms")
    a = ap.parse_args()

    audio = Path(a.audio).expanduser().resolve()
    a.out_dir.mkdir(parents=True, exist_ok=True)
    wav = a.out_dir / "audio16k.wav"
    subprocess.run([ffmpeg(), "-loglevel", "error", "-y", "-i", str(audio), "-ac", "1", "-ar", str(SR), str(wav)],
                   check=True)
    x = sf.read(wav, dtype="float32")[0]

    pieces = label_pieces(x, a.models_dir, a.num_speakers)
    json.dump(pieces, open(a.out_dir / "pieces.json", "w"))
    tot = {}
    for s, b, l in pieces:
        tot[l] = tot.get(l, 0) + b - s
    print(f"{len(pieces)} pieces | " + " | ".join(f"speaker {l} {t:.0f}s" for l, t in sorted(tot.items())), flush=True)
    if a.labels_only:
        return

    prompt = a.prompt_file.read_text().strip() if a.prompt_file else None
    stamp = lambda s: f"[{int(s // 60)}:{s % 60:04.1f}]"
    results = {}
    for mode in a.modes.split(","):
        rows = []  # one entry per piece (empty text kept so the two modes stay aligned)
        for s, b, l in pieces:
            clip = x[max(0, int((s - 0.2) * SR)):int((b + 0.2) * SR)]
            rows.append((s, l, transcribe_piece(clip, mode, prompt)))
        results[mode] = rows
        name = {"en": "English", "hing": "Hinglish"}[mode]
        out = a.out_dir / f"{audio.stem}.{name}.txt"
        out.write_text("\n".join(f"{stamp(s)} Speaker {l}: {t}" for s, l, t in rows if t), encoding="utf-8")
        print("wrote", out, flush=True)

    if "en" in results and "hing" in results:
        gpath = a.glossary_file or DEFAULT_GLOSSARY
        glossary = gpath.read_text(encoding="utf-8").strip() if gpath.exists() else "(none provided)"
        bundle = a.out_dir / f"{audio.stem}.for-llm.md"
        bundle.write_text(bundle_text(
            [(s, l, h, e) for (s, l, h), (_, _, e) in zip(results["hing"], results["en"])],
            glossary), encoding="utf-8")
        print("wrote", bundle, flush=True)


if __name__ == "__main__":
    main()
