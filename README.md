# voice-memo-transcriber

Transcribes two-person Hindi / Hinglish voice memos on Apple Silicon (MLX, GPU) into
**English** and **Roman-script Hinglish**, with `Speaker 1` / `Speaker 2` labels.

Built for accented, code-switched Indian speech recorded on one phone mic, where plain Whisper
loops ("no no no no…"), invents text, and where blind speaker diarization is unreliable.

## Setup

Requires macOS on Apple Silicon, Python 3.10+, `ffmpeg`.

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
bash scripts/download_models.sh        # ~90 MB speaker models -> ./models
# whisper-large-v3 (~5.8 GB) downloads from Hugging Face on first run
```

## Usage

Speakers are detected automatically; there is nothing to label by hand:

```bash
python transcribe.py memo.m4a --prompt-file examples/prompt.example.txt
```

Outputs go to `out/`: `<name>.English.txt`, `<name>.Hinglish.txt`, `pieces.json`.

```
[0:12.7] Speaker 2: What time does the design review start tomorrow?
[0:15.7] Speaker 1: It starts at ten; I'll send everyone the invite...
```

- `--num-speakers N`: override the auto-detected count. By default the count comes from the
  eigengap, and if the clusters aren't clearly different voices (separation < 0.10) everything is
  labelled one speaker. That cutoff is **provisional**: calibrated on two recordings (2 speakers ->
  0.143, one speaker split in two -> 0.068). Pass `--num-speakers 1` or `2` if it guesses wrong.
- `--labels-only`: detect speakers and write `pieces.json` without transcribing (fast sanity check).
- `--prompt-file`: optional (a neutral Hinglish default is used; without *any* Hinglish text
  Whisper writes English instead of Roman-script Hindi). A few Hinglish sentences containing your domain vocabulary. Whisper copies
  the style and spells those words correctly. This is deliberately the only vocabulary
  mechanism: hand-written find/replace lists (e.g. "sabri" -> "summary") overfit one recording
  and corrupt real words elsewhere, so there is no post-correction step.
- `--modes en` or `--modes hing` to run just one output.

## How it works

1. ffmpeg to 16 kHz mono.
2. Speech regions from the pyannote segmentation model (used only for "is someone talking").
3. 1-second windows (0.5 s hop) are embedded with two models (3D-Speaker ERes2Net + NeMo
   TitaNet-small); their averaged cosine similarity feeds spectral clustering, with the speaker
   count chosen by the largest eigengap. Labels are smoothed, numbered by first appearance, and
   the audio is cut at every speaker change.
4. Each piece is transcribed separately with `whisper-large-v3` (MLX / Apple GPU):
   English = Hindi→English translation; Hinglish = Roman-script transcription steered by the prompt.
5. Loop detection retries hotter, then marks `[unclear - overlapping speech]`; invented text on
   sub-1.3 s clips is dropped. Nothing is rewritten after the fact.

## What did not work (so you don't repeat it)

| Tried | Result |
|---|---|
| Whisper large-v3-turbo, Hindi/Devanagari decoding | Repetition loops ("नहीं नहीं नहीं…"), garbage multilingual text |
| `condition_on_previous_text=False` alone | Necessary, not sufficient |
| Loudness normalisation (`loudnorm`) | Amplified noise, worse output |
| Spectral-gating denoise (`noisereduce`) | Fewer words, blanked a quiet turn; raw audio was better |
| Fixed 15-20 s chunks | Cut sentences; my first repetition filter threw away good Hindi |
| sherpa-onnx's built-in diarization clustering (`num_clusters=2`) | 4 embedding models disagreed on 9-25 % of speech; its segmentation merged a 2 s interjection into the other speaker's 18 s segment |
| Hand-labelled voice profiles + self-training both | Drifted toward the louder speaker; also needed manual input |
| Hand-written find/replace word fixes | Overfit one recording |

Spectral clustering over 1 s windows (what `transcribe.py` does) fixed the interjection problem:
on the test memo it found 2 speakers unaided and matched the ~85 windows I had confirmed by ear
(98 % / 100 % purity).
| English-trained voice models (wespeaker / CAM++) | Separated these two speakers worse than ERes2Net / TitaNet |

Hindi→English *translation* with the full large-v3 model was the one setting with zero loops on the
hardest stretches.

## Limitations

- Speaker detection was validated on ONE two-person memo. 3+ speakers should work (the count is
  auto-detected) but is untested; very similar voices on one mic will degrade it, and short
  interjections ("ok", "yes") are the most likely to be mislabeled. Separate tracks per person
  beat any of this.
- Overlapping speech stays `[unclear]`; proper nouns and accents still produce some misspellings.
- Only the first ~50 s were validated against ground truth in the original run.
- The consolidated `transcribe.py` was assembled from tested experiment scripts; if something
  breaks, check model paths in `--models-dir` first.
