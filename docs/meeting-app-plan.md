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
- Voice Memos-style native `NavigationSplitView`, minimum 820×520, standard toolbar with Import Audio (+),
  system colors/materials and SF Symbols. The whole window accepts m4a mp3 wav aac caf ogg opus flac aiff mp4 mov,
  with a subtle dashed “Drop to transcribe” overlay while targeted.
- “All Meetings” sidebar scans `<meetings>/YYYY/MM/*/meta.json` on launch, activation, folder changes and
  job completion; title search, newest first, date/time/duration subtitles, most recent selected on launch.
  Processing/queued/failed jobs occupy a separate top section with progress, stage/chunk messages, errors and Show Log.
  Successful jobs leave that section and their meeting is selected. Meeting context menus offer Show in Finder,
  Open Summary in Default App and reversible Move to Trash via `NSWorkspace.recycle`.
- Centered sidebar recording control: round red record button, red stop square inside a ring while recording,
  elapsed time and 12 live meter bars (`AVAudioRecorder.averagePower`). AAC m4a 48 kHz mono recordings go to
  `<meetings-dir>/_recordings/Recording YYYY-MM-DD HHmm.m4a`, then queue with settings captured at recording start.
- Detail shows the title, date/time/duration/speaker count, `AVAudioPlayer` controls for `audio.*`, scrubber and
  elapsed/remaining times. Summary/Transcript segmented picker; corrected transcript preferred over Hinglish,
  optional Show English. Tiny selectable Markdown renderer hides the top-level title, styles section headings,
  unchecked action items, bullets and inline bold; transcript rows separate timestamp, speaker and text.
  No selection uses the native “No Meeting Selected” empty state.
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
  on first launch. Settings apply to newly queued jobs. Settings uses a 520pt grouped Form TabView:
  Models (existing provider/model choices), Accounts (status dots, Connect/Sign Out), General (meetings folder
  with directory chooser and repository path), Advanced (custom endpoint URL/model/key and Anthropic key).
  Endpoint model controls reuse existing stage model keys; no settings keys or migration contracts change.
- Separate ChatGPT and Claude account rows live in Settings Accounts, each with its own spinner/messages.
  Claude uses `claude auth status` (exit 0 and JSON `loggedIn: true`), `claude auth login --claudeai`, and `claude auth logout`.
  Missing Claude shows “Claude Code is not installed”. Refresh both on launch and activation with the same output safety and timeout rules.
- Shared ChatGPT account state in the detail notice and Settings Accounts tab. Connect runs `codex login` off the main actor,
  with null stdin and a ten-minute timeout; users finish sign-in in their browser. Settings also offers Sign out (`codex logout`).
  Refresh with `codex login status` at launch and window activation, and after account changes. Resolve Codex from Homebrew,
  `/usr/local/bin`, or PATH, and include standard executable directories in child PATH. Never display or log account command output.
  Missing Codex shows an installation hint; checking/connecting show a spinner and connection shows green status.
- Main window shows only a compact detail-area notice when current settings require a disconnected account
  or a missing Anthropic key; Connect… opens Settings, where all account controls live.
- Check both selected providers before starting a job. For codex, require connected account state; otherwise fail with “Connect ChatGPT first (button at the top)”
  and preserve the input file. Either step using Claude requires “Connect Claude first (button at the top)”; either step using anthropic with an empty key
  fail with “Add your Anthropic API key in Settings”.

## Not in scope
Live transcription, editing summaries, cloud sync, sandboxing/notarisation, auto-starting CLIProxyAPI.

## Part C: live recording (mic choice, pause, transcribe-while-recording)

Goal: when a recording stops, only the last segment, speaker clustering, correction and summary remain.

### Session folder (contract between app and Python)
`<meetings>/_live/<session-id>/` (session-id = `YYYYmmdd-HHMMSS`):
- `session.json` {started_at (ISO, local tz), input_device_name, segment_seconds: 30}
- `segments/seg-00001.m4a`, `seg-00002.m4a`, … written by the app (AAC mono 48 kHz). A segment is complete only when the app has
  written the empty marker `segments/seg-NNNNN.done` (written in the file-output "did finish" callback). Pausing does not create gaps:
  the session timeline is the concatenation of recorded audio (offset of segment n = sum of durations of segments < n, via ffprobe).
- `STOP` empty marker written by the app after the last segment's `.done`.
- Written by Python: `segments/seg-NNNNN.json` (per-segment transcript: whisper segments with start/end relative to the segment,
  Hinglish text and English translation), `segments/seg-NNNNN.npz` (1 s / 0.5 s-hop speaker-embedding windows: times relative to the
  segment + both embedding models' vectors), `live.txt` (one line per transcribed whisper segment, `[m:ss] text`, appended, for the UI).

### Python
- `venv/bin/python -I live_transcribe.py watch <session>`: loads whisper large-v3 (MLX) and the two embedding models ONCE; loops over
  complete segments in order that have no `.json` yet; writes `.json` + `.npz` atomically (write tmp, rename); appends `live.txt`;
  prints JSON lines `{"event":"segment","index":n,"text":"…"}`; when `STOP` exists and every complete segment is processed prints
  `{"event":"stopped"}` and exits 0. Never crashes on one bad segment (log + `{"event":"segment_error",…}`, continue).
- `summarize.py --live-session <session> [all existing flags]` replaces the transcription stage with `{"stage":"finalizing"}`:
  waits up to 10 min for a still-running watcher (no `STOP`+all done → proceed anyway after timeout), processes any complete segment
  lacking `.json`/`.npz` itself (crash recovery), skips unreadable segments (truncated m4a) with a log line, concatenates readable
  segments to `<session>/recording.m4a` (ffmpeg concat, `-c copy`), clusters speakers over all windows with the same spectral method +
  single-speaker separation cutoff as transcribe.py, assigns each whisper segment to the speaker with most overlapping windows, and
  writes the same work files transcribe.py writes (`<stem>.Hinglish.txt`, `<stem>.English.txt`, `<stem>.for-llm.md`, `pieces.json`),
  then continues with cleaning → correcting → summarizing → organizing exactly as today (audio = recording.m4a,
  recorded_at = session started_at). Zero readable segments → `{"stage":"error"}`. On success the session folder is deleted.
- Reuse transcribe.py's functions (import it) instead of copying code.

### App
- Recorder: AVCaptureSession + AVCaptureDeviceInput(selected mic) + AVCaptureAudioFileOutput; 30 s segments (switch files while
  recording); Pause / Resume (pauseRecording/resumeRecording, timer excludes paused time); Stop → finish current segment, write
  `.done` + `STOP`, enqueue a job with `--live-session`. Starts the watcher at Record, shows live transcript (live.txt / stdout events)
  in the detail pane while recording.
- Mic picker (menu next to the record button; default = system default; remembers uniqueID; falls back to default if missing) and
  playback output picker (Settings ▸ General; AVPlayer.audioOutputDeviceUniqueID; CoreAudio device list).
- On launch: any `_live/<session>` with segments but no queued job → enqueue `--live-session` (crash recovery).
- After a job finishes, a source file inside `_recordings/` is renamed to `<summary title>.m4a` (unique name).
- Swift test target `MeetingNotesTests` for pure logic (recorder state machine, segment naming/ordering, recovery detection, rename).

## Part D: system audio (calls) + mic as two tracks

### Session contract additions
- `session.json` adds `"system_audio": true|false`.
- When system audio is on, every segment has a second file `segments/seg-NNNNN.sys.m4a` (AAC mono 48 kHz, the system output
  mix with MeetingNotes itself excluded). `seg-NNNNN.done` is written only after BOTH files of that segment are closed. If system
  capture fails mid-session the app keeps recording mic-only; segments without a `.sys.m4a` are treated as mic-only.

### Python
- Per segment with a `.sys.m4a`: transcribe the MIX (ffmpeg `amix=inputs=2:duration=longest:normalize=0`, then the existing piece
  splitting / both whisper passes), so speech heard on both tracks (speaker echo) is transcribed once.
- Source per whisper segment: compare mic and sys RMS (dBFS, 16 kHz mono) over the segment's time range. `sys` when the sys track is
  active (>= -45 dBFS) and mic is not clearly louder (mic < sys + 10 dB); otherwise `mic`. Constants in one place. Store `source`
  in the segment json.
- Speaker embeddings for those segments are computed on the SYS audio only (remote voices).
- Finalize (session with system_audio): Speaker 1 = the person recording (all `mic` segments). `sys` segments get remote speakers
  from the existing spectral clustering + separation cutoff run over sys windows only, numbered from Speaker 2 by first appearance
  (no separated clusters -> all Speaker 2). Mixed segments where both tracks are absent fall back to nearest labelled neighbour.
  The bundle header notes "Speaker 1 is the person who recorded (microphone); other speakers came through the call audio."
  `recording.m4a` = per-segment mixes concatenated. Mic-only sessions behave exactly as today.

### App
- Core Audio process tap (macOS 14.2+): `CATapDescription(stereoGlobalTapButExcludeProcesses:)` excluding MeetingNotes, a private
  aggregate device containing the tap, an IO proc writing PCM to the current `.sys.m4a` (AVAudioFile, AAC mono 48 kHz, downmixed),
  rotated at the same moment as the mic segment; paused together with the mic. Info.plist `NSAudioCaptureUsageDescription`.
- UI: "Include system audio (calls)" toggle next to the mic picker (persisted). If the tap can't be created/started (permission denied,
  older macOS) show a notice and record mic-only. Tear down tap + aggregate device on stop, on error and on quit.

## Part E: calendar (EventKit, macOS Calendar incl. Google accounts)

### Event context contract
- The app writes an event JSON (`event.json` in a live session folder, or a temp file passed with `--event-json PATH` for dropped
  files): {"title", "start", "end" (ISO local tz), "attendees": [{"name", "email"}], "organizer": {"name","email"} | null,
  "location", "url", "calendar"}. Attendees without a name use the email's local part.
- summarize.py: `--event-json PATH` (or `<session>/event.json` for --live-session). When present:
  - correction prompts and the summary input get a short `## Meeting` section (title, date/time, attendees/organizer names) placed
    before `## Segments`, and the glossary context gains the attendee names (context only, never replacement rules);
  - the meeting folder title = the event title (sanitised as today; the LLM title is ignored);
  - meta.json adds "event" with the JSON as given.
  Without an event everything behaves exactly as today.

### App
- EventKit: `requestFullAccessToEvents` (macOS 14), Info.plist `NSCalendarsFullAccessUsageDescription`. Settings ▸ Calendar tab:
  access status + "Connect Calendar", list of calendars with checkboxes (persisted identifiers; default all), "Remind me to record
  meetings" toggle (default on).
- Pure `UpcomingMeetingPicker`: from events in the next 12 h and the last 2 h: ignore all-day, cancelled and declined-by-me events
  and events without other attendees; pick the in-progress one (started <= now < end) else the soonest upcoming. Refresh every
  60 s and on EKEventStoreChanged.
- Sidebar top: "Up next" card (title, time range or "Now · ends 3:30 PM", first 3 attendee names + "+N"), Record button that
  starts recording linked to that event (writes event.json into the session). While recording a linked meeting, the live view
  shows its title.
- Local notification at event start (UserNotifications, ask permission on first enable) with a "Record" action that opens the app
  and starts a linked recording; never auto-records. Re-schedule when events change; no duplicate notifications.
- Meeting detail shows event title, time and attendees when meta.json has "event".
