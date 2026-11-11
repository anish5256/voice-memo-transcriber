# MeetingNotes: tiny macOS app (plan / spec)

Drop audio files or record from the Mac mic -> local transcription (existing pipeline) -> ChatGPT
summary -> one organized folder per meeting. Two parts with a fixed contract so they can be built
independently.

## Part A: `summarize.py` (repo root, Python, stdlib only)

```
venv/bin/python -I summarize.py AUDIO
    [--meetings-dir ~/Meetings]
    [--backend codex|proxy|fake]            default codex
    [--codex-model MODEL]                   optional, passed as -m
    [--proxy-url http://127.0.0.1:8317/v1]  OpenAI-compatible (CLIProxyAPI)
    [--proxy-model gpt-5]
    (proxy API key is read from env var CLIPROXY_API_KEY, never from argv)
```

Steps (stdout is ONLY JSON lines, one per event; child process output goes to `<work>/pipeline.log`):
1. `{"stage":"transcribing"}`: run `sys.executable -I transcribe.py AUDIO --models-dir <repo>/models --out-dir <work>`
   where `<work>` = `<meetings-dir>/_work/<timestamp>-<stem>`. Parse "detected N speakers" from its output.
2. `{"stage":"cleaning"}`: run `sys.executable -I clean.py <work>/<stem>.for-llm.md` -> `<stem>.for-llm.names-fixed.md`.
3. `{"stage":"summarizing"}`: prompt = SUMMARY_PROMPT + recording date + names-fixed bundle text. Backends:
   - `codex`: `codex exec --ignore-user-config --skip-git-repo-check --ephemeral -s read-only -c approval_policy="never" [-m MODEL] -o <work>/summary.md -` with the prompt on stdin, cwd=`<work>`, timeout 20 min.
   - `proxy`: POST `{proxy-url}/chat/completions` (urllib), `Authorization: Bearer $CLIPROXY_API_KEY`, temperature 0.2.
   - `fake`: canned markdown (for tests and UI work), no network.
   The summary must start with `# <Title>`; the title names the folder (fallback: audio file stem).
4. `{"stage":"organizing"}`: final folder `<meetings-dir>/YYYY/MM/YYYY-MM-DD HHMM - <Title>/` (date = audio file mtime,
   local time; title sanitised for filesystem, max 60 chars; add " (2)" etc. if it exists) containing:
   `audio.<ext>` (copy of input), `summary.md`, `transcript.hinglish.txt` (glossary-fixed), `transcript.english.txt`,
   `transcript.for-llm.md` (names-fixed bundle), `meta.json` {title, recorded_at, duration_s, speakers, source_file,
   backend, model, created_at}. Duration via `ffprobe`. Delete `<work>` afterwards (keep it on failure).
   Append `- YYYY-MM-DD HH:MM — [Title](YYYY/MM/<folder>/summary.md) — N min, S speakers` to `<meetings-dir>/INDEX.md`.
5. `{"stage":"done","folder":"<abs path>","title":"..."}`, exit 0. Any failure: `{"stage":"error","message":"...","log":"<work>/pipeline.log"}`, exit 1.

SUMMARY_PROMPT (English output): summarise a Hindi/Hinglish/English meeting from two noisy transcripts per segment
(Hinglish + English) and a glossary. Sections: `# <short title, max 8 words>`, `**Date:**`, `## TL;DR` (2-3 sentences),
`## Key points`, `## Decisions`, `## Action items` (`- [ ] Owner: task (due if said)`), `## Open questions / risks`,
`## Speakers` (role of Speaker 1/2/... only if clear). Rules: only facts in the transcript, keep numbers exact,
never invent owners or dates, mark unsure names/words with [?].

## Part B: `mac-app/` (SwiftUI, Swift Package, macOS 14+, no third-party deps)

- `Package.swift`, `Sources/MeetingNotes/*.swift`, `Info.plist`, `build_app.sh` -> `mac-app/build/MeetingNotes.app`
  (bundle id `com.anish.meetingnotes`, `NSMicrophoneUsageDescription`, `LSMinimumSystemVersion` 14.0, ad-hoc `codesign -s -`).
- One window: a large drop zone (accepts m4a mp3 wav aac caf ogg opus flac aiff mp4 mov), a Record/Stop button with elapsed
  time (AVAudioRecorder, AAC m4a 48 kHz mono, saved to `<meetings-dir>/_recordings/Recording YYYY-MM-DD HHmm.m4a`, then queued),
  and a job list: file name, current stage, spinner; on done "Open folder" + "Open summary"; on error the message + "Show log".
- Dropped files are first COPIED by the app into `<meetings-dir>/_inbox/` (the app holds the user's file access), then processed.
- Jobs run one at a time. Runner: `Process` with `<repo>/venv/bin/python -I <repo>/summarize.py <file> --meetings-dir … --backend …`,
  reads stdout JSON lines to update UI on the main actor; PATH for the child must include `/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin`
  (codex, ffmpeg); proxy key passed via env `CLIPROXY_API_KEY`.
- Settings window (@AppStorage): repo path (default `~/voice-memo-transcriber`), meetings folder (default `~/Meetings`),
  backend: “ChatGPT (your account)” (codex, default), “Custom OpenAI-compatible endpoint” (proxy), or “Offline test” (fake).
  Optional ChatGPT model; proxy URL/model/key live in a collapsed Advanced disclosure, visible only for the custom endpoint.
- Shared ChatGPT account state in the header and Settings Account section. Connect runs `codex login` off the main actor,
  with null stdin and a ten-minute timeout; users finish sign-in in their browser. Settings also offers Sign out (`codex logout`).
  Refresh with `codex login status` at launch and window activation, and after account changes. Resolve Codex from Homebrew,
  `/usr/local/bin`, or PATH, and include standard executable directories in child PATH. Never display or log account command output.
  Missing Codex shows an installation hint; checking/connecting show a spinner and connection shows green status.
- Before starting a codex job, require connected account state; otherwise fail with “Connect ChatGPT first (button at the top)”
  and preserve the input file.

## Not in scope
Live transcription, editing summaries, cloud sync, sandboxing/notarisation, auto-starting CLIProxyAPI.
