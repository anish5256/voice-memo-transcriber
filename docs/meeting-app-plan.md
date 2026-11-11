# MeetingNotes: tiny macOS app (plan / spec)

Drop audio files or record from the Mac mic -> local transcription (existing pipeline) -> ChatGPT or Claude
summary -> one organized folder per meeting. Two parts with a fixed contract so they can be built
independently.

## Part A: `summarize.py` (repo root, Python, Anthropic SDK only for the API backend)

```
venv/bin/python -I summarize.py AUDIO
    [--meetings-dir ~/Meetings]
    [--summary-backend codex|claude|anthropic|proxy|fake]   default codex (--backend alias)
    [--summary-model MODEL]                 default empty: provider default
    [--correct-backend none|codex|claude|anthropic|proxy|fake] default none
    [--correct-model MODEL]                 default empty: provider default
    [--claude-model MODEL]                  legacy summary fallback for claude/anthropic
    [--codex-model MODEL]                   legacy summary fallback for codex
    [--proxy-url http://127.0.0.1:8317/v1]  OpenAI-compatible (CLIProxyAPI)
    [--proxy-model gpt-5]
    (proxy API key is read from env var CLIPROXY_API_KEY, never from argv)
```

Steps (stdout is ONLY JSON lines, one per event; child process output goes to `<work>/pipeline.log`):
1. `{"stage":"transcribing"}`: run `sys.executable -I transcribe.py AUDIO --models-dir <repo>/models --out-dir <work>`
   where `<work>` = `<meetings-dir>/_work/<timestamp>-<stem>`. Parse "detected N speakers" from its output.
2. `{"stage":"cleaning"}`: run `sys.executable -I clean.py <work>/<stem>.for-llm.md` -> `<stem>.for-llm.names-fixed.md`.
   Optional correction follows cleaning: names-fixed segments are chunked at ~12,000 characters of
   Hinglish + English text (whole segments). Each chunk emits `{"stage":"correcting","message":"chunk i/n"}`.
   Every prompt includes the glossary and both transcripts; correction preserves wording and Roman Hinglish.
   Outputs match by timestamp; accept lengths 0.6–1.6 times original Hinglish, otherwise retain originals.
   Missing outputs and failed calls retain originals; failures are logged and subsequent chunks continue.
3. `{"stage":"summarizing"}`: prompt = SUMMARY_PROMPT + recording date + names-fixed bundle text, with corrected Hinglish when correction ran (English retained).
   Both stages share `ask_llm`; empty model means no Codex `-m`, Claude/Anthropic `claude-opus-5-5`, proxy `gpt-5`. Backends:
   - `codex`: `codex exec --ignore-user-config --skip-git-repo-check --ephemeral -s read-only -c approval_policy="never" [-m MODEL] -o <work>/summary.md -` with the prompt on stdin, cwd=`<work>`, timeout 20 min.
   - `claude`: `claude -p --setting-sources local --tools "" --strict-mcp-config --no-session-persistence --model MODEL --output-format text`,
     prompt on stdin, cwd=`<work>`, timeout 20 min; stdout is the summary, stderr goes to pipeline.log.
   - `anthropic`: lazy-import Anthropic SDK; key from `ANTHROPIC_API_KEY` only, never argv. Beta messages API with
     max_tokens=16000, server-side fallback beta and default fallbacks. Both Claude backends use the selected stage model.
   - `proxy`: POST `{proxy-url}/chat/completions` (urllib), `Authorization: Bearer $CLIPROXY_API_KEY`, temperature 0.2.
   - `fake`: canned markdown (for tests and UI work), no network.
   The summary must start with `# <Title>`; the title names the folder (fallback: audio file stem).
4. `{"stage":"organizing"}`: final folder `<meetings-dir>/YYYY/MM/YYYY-MM-DD HHMM - <Title>/` (date = audio file mtime,
   local time; title sanitised for filesystem, max 60 chars; add " (2)" etc. if it exists) containing:
   `audio.<ext>` (copy of input), `summary.md`, `transcript.hinglish.txt` (glossary-fixed), `transcript.english.txt`,
   `transcript.for-llm.md` (names-fixed bundle), `meta.json` {title, recorded_at, duration_s, speakers, source_file,
   backend, model, summary_backend, summary_model, correct_backend, correct_model,
   correction: {accepted, rejected, missing}, created_at}. `backend`/`model` mirror summary values.
   When correction ran, also save `transcript.corrected.txt`; retain the names-fixed bundle unchanged on disk. Duration via `ffprobe`. Delete `<work>` afterwards (keep it on failure).
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
- Jobs run one at a time. Runner: `Process` with `<repo>/venv/bin/python -I <repo>/summarize.py <file>
  --meetings-dir … --summary-backend … --summary-model … --correct-backend … --correct-model … --proxy-url …`.
  Stdout JSON lines update UI on the main actor; show stage messages, e.g. “Correcting – chunk 2/5”.
  PATH includes `/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin`. Keys travel only via env:
  `CLIPROXY_API_KEY` and `ANTHROPIC_API_KEY` (the latter only when either stage uses anthropic).
- Settings (@AppStorage): repo path, meetings folder, separate Transcript correction (Off by default) and
  Summary (ChatGPT by default) provider/model sections. Correction supports the four online providers;
  Summary also supports Offline test. ChatGPT offers Default (Codex chooses) and Custom…;
  Claude/Claude API offers claude-opus-5-5, claude-sonnet-5-5, claude-haiku-5-5, claude-fable-5-2 and Custom…;
  endpoint model is a text field defaulting to gpt-5. Custom… reveals a text field; Off hides models.
  Store correctProvider/correctModel/summaryProvider/summaryModel; migrate old backend and its model
  on first launch. Keep Account and collapsed Advanced credentials/endpoint controls; Advanced appears
  when either stage needs it. Settings apply to newly queued jobs.
- Separate ChatGPT and Claude account rows in the header and Settings Account section, each with its own spinner/messages.
  Claude uses `claude auth status` (exit 0 and JSON `loggedIn: true`), `claude auth login --claudeai`, and `claude auth logout`.
  Missing Claude shows “Claude Code is not installed”. Refresh both on launch and activation with the same output safety and timeout rules.
- Shared ChatGPT account state in the header and Settings Account section. Connect runs `codex login` off the main actor,
  with null stdin and a ten-minute timeout; users finish sign-in in their browser. Settings also offers Sign out (`codex logout`).
  Refresh with `codex login status` at launch and window activation, and after account changes. Resolve Codex from Homebrew,
  `/usr/local/bin`, or PATH, and include standard executable directories in child PATH. Never display or log account command output.
  Missing Codex shows an installation hint; checking/connecting show a spinner and connection shows green status.
- Check both selected providers before starting a job. For codex, require connected account state; otherwise fail with “Connect ChatGPT first (button at the top)”
  and preserve the input file. Either step using Claude requires “Connect Claude first (button at the top)”; either step using anthropic with an empty key
  fail with “Add your Anthropic API key in Settings”.

## Not in scope
Live transcription, editing summaries, cloud sync, sandboxing/notarisation, auto-starting CLIProxyAPI.
