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

You tell it where each speaker is talking, by listening to a few seconds of each:

```bash
python transcribe.py memo.m4a \
  --speaker1 1.6-11.9 --speaker1 16.1-30.5 \
  --speaker2 12.8-14.9 \
  --prompt-file examples/prompt.example.txt
```

Outputs go to `out/`: `<name>.English.txt`, `<name>.Hinglish.txt`, `pieces.json`.

```
[0:12.7] Speaker 2: What time does the design review start tomorrow?
[0:15.7] Speaker 1: It starts at ten; I'll send everyone the invite...
```

- `--speaker1/--speaker2`: confirmed time ranges (repeatable). ~20 s for one speaker and even
  ~2 s for the other worked. More examples = better labels.
- `--prompt-file`: a few Hinglish sentences containing your domain vocabulary. Whisper copies
  the style and spells those words correctly. This is deliberately the only vocabulary
  mechanism: hand-written find/replace lists (e.g. "sabri" -> "summary") overfit one recording
  and corrupt real words elsewhere, so there is no post-correction step.
- `--modes en` or `--modes hing` to run just one output.

## How it works

1. ffmpeg to 16 kHz mono.
2. Speech regions from the pyannote segmentation model (used only for "is someone talking").
3. 1-second windows (0.5 s hop) are scored against the two voice profiles you supplied, using two
   embedding models (3D-Speaker ERes2Net + NeMo TitaNet-small), smoothed, and cut at every
   speaker change.
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
| Unsupervised diarization (`num_clusters=2`) | 4 embedding models disagreed on 9-25 % of speech; the segmentation model merged a 2 s interjection into the other speaker's 18 s segment |
| Self-training *both* voice profiles | Drifted toward the louder speaker |
| English-trained voice models (wespeaker / CAM++) | Separated these two speakers worse than ERes2Net / TitaNet |

Hindi→English *translation* with the full large-v3 model was the one setting with zero loops on the
hardest stretches.

## Limitations

- Exactly two speakers. Labels come from your examples, so verify a few lines.
- Overlapping speech stays `[unclear]`; proper nouns and accents still produce some misspellings.
- Only the first ~50 s were validated against ground truth in the original run.
- The consolidated `transcribe.py` was assembled from tested experiment scripts; if something
  breaks, check model paths in `--models-dir` first.
