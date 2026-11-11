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
python transcribe.py memo.m4a
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
- `--prompt-file`: optional (a generic everyday-Hinglish default is built in; without *any* Hinglish text
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

## Handing the result to an LLM

When both modes run, `out/<name>.for-llm.md` is also written: clean-up instructions, your glossary,
and for every segment both the Hinglish and the English transcript. The two fail in different
places, so an LLM reading both plus context repairs far more than any find/replace list could.

Put your own names and terms in `~/.voice-memo-glossary.txt` (or `--glossary-file`). It is context
for the LLM, not replacement rules, and it lives outside the repo. The instructions tell the LLM
to keep the speaker's wording, never invent text, and mark doubtful words `[?]`.

## Fixing names, then handing off

`python clean.py out/<name>.for-llm.md` applies your glossary's `(often heard as "...")` entries as
exact whole-word replacements to both transcripts and writes `<name>.for-llm.names-fixed.md`:
the file to give to a stronger LLM (raw Hinglish + English side by side, names already right).
Raw transcripts are never modified.

Optional: `--llm gemma3:12b` also runs a local Ollama model over the text in chunks (a segment is
kept only if its length stays within 0.75x-1.35x of the original). **Off by default, not
recommended** when a stronger LLM will read the result: on a 49-minute meeting it changed only 3 % of
words (221 of 7,374; 315 segments accepted, 17 rejected), fixed easy spelling (me -> mein,
mai -> main) but made guesses (verb forms, dropped "pe"/"ki") that hide the evidence a stronger
model could use. Qwen3 8B was worse: it rewrote meanings and claimed fixes it never made.

## Automatic: iPhone voice memo -> transcript

```
iPhone: Voice Memos -> Share -> Save to Files -> iCloud Drive / VoiceInbox
Mac:    launchd agent -> inbox_worker.py -> transcribe.py -> clean.py
        -> iCloud Drive / VoiceOut / <name>_<date> /   (+ optional WhatsApp ping, no transcript text in it)
```

Setup: `python3 -m venv venv && venv/bin/pip install -r requirements.txt && bash scripts/download_models.sh models`,
then `bash scripts/install_agent.sh [whatsapp-number]` (uses launchd `WatchPaths` on the inbox plus a
5-minute fallback; log: `~/Library/Logs/voice-memo-worker.log`). Only new files are processed (state in
`~/.voice-memo-state.json`); a lock keeps it to one file at a time. The Mac must be awake and online.
Run `venv/bin/python inbox_worker.py` by hand to process the inbox once.

## MeetingNotes (macOS app)

Drop audio files or record from the mic; each recording is transcribed locally, summarised by ChatGPT
and filed as `~/Meetings/YYYY/MM/YYYY-MM-DD HHMM - <Title>/` (audio, `summary.md`, transcripts,
`meta.json`) with a line in `~/Meetings/INDEX.md`. Spec: `docs/meeting-app-plan.md`.

- The app lives in its own repo, [meetingnotes-mac](https://github.com/anish5256/meetingnotes-mac), with a setup guide and a one-command installer. This repo is its transcription engine.
- Backend CLI (also usable without the app): `venv/bin/python -I summarize.py AUDIO [--backend codex|proxy|fake]`.
- Summaries: `codex` backend (default) uses your ChatGPT login via `codex exec --ignore-user-config`
  (no MCP/connector tools); `proxy` uses an OpenAI-compatible endpoint such as CLIProxyAPI
  (`CLIPROXY_API_KEY` env var). The transcript text leaves the Mac for the summary step only.
- Tests: `venv/bin/python -m unittest tests/test_summarize.py`.

### Live recording
Record in MeetingNotes and the audio is saved in 30 s segments that `live_transcribe.py watch` transcribes
while you talk (~18 s of compute per 30 s on an M3 Pro), so after Stop only the last segment, speaker
clustering, correction and the summary remain. Pick the microphone next to the record button; pause/resume
any time. Interrupted sessions in `~/Meetings/_live/` are finished automatically on the next launch.
Tests: `venv/bin/python -m unittest tests/test_live.py tests/test_summarize.py`.

### Calendar
MeetingNotes reads macOS Calendar (add Google under System Settings ▸ Internet Accounts). The "Up next" card records
a meeting linked to its event: the folder is named after the event and attendee names are given to the correction and
summary models as context. Optional reminders at meeting start never record on their own. Backend: `summarize.py --event-json`.
