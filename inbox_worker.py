#!/usr/bin/env python3
"""Process new audio from an iCloud Drive inbox, then put results where the iPhone can read them.

  iPhone: Voice Memos -> Share -> Save to Files -> iCloud Drive/VoiceInbox
  Mac:    this script (run by launchd, see scripts/install_agent.sh)
            transcribe.py (speakers, English + Hinglish)  ->  clean.py (glossary, names-fixed bundle)
            -> iCloud Drive/VoiceOut/<name>_<date>/   and an optional WhatsApp ping (no content in it)

One file is processed at a time; a lock makes concurrent launches harmless. Files already
processed (same name, size and modified time) are skipped, so only new audio is ever touched.
"""
import argparse
import fcntl
import json
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

HOME = Path.home()
ICLOUD = HOME / "Library/Mobile Documents/com~apple~CloudDocs"
REPO = Path(__file__).resolve().parent
PY = REPO / "venv/bin/python"
AUDIO = {".m4a", ".mp3", ".wav", ".ogg", ".opus", ".aac", ".caf", ".mp4", ".flac", ".aiff", ".amr"}
STATE = HOME / ".voice-memo-state.json"
LOCK = HOME / ".voice-memo-worker.lock"


def log(msg):
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}", flush=True)


def key(p):
    st = p.stat()
    return f"{p.name}|{st.st_size}|{int(st.st_mtime)}"


def stable(p, wait=6):
    """True once the file stops growing, i.e. iCloud has finished downloading it."""
    try:
        a = p.stat().st_size
        time.sleep(wait)
        return a > 0 and p.stat().st_size == a
    except FileNotFoundError:
        return False


def notify(url, number, text):
    if not number:
        return
    try:
        req = urllib.request.Request(f"{url}/api/send", headers={"Content-Type": "application/json"},
                                     data=json.dumps({"recipient": number, "message": text}).encode())
        urllib.request.urlopen(req, timeout=30).read()
    except Exception as e:  # a failed ping must never fail the job
        log(f"notify failed: {e}")


def process(p, outbox, a):
    out = outbox / f"{p.stem}_{datetime.now():%Y%m%d-%H%M}"
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    run = lambda *cmd: subprocess.run([str(PY), "-I", *map(str, cmd)], check=True)
    run(REPO / "transcribe.py", p, "--models-dir", REPO / "models", "--out-dir", out)
    bundle = out / f"{p.stem}.for-llm.md"
    if bundle.exists():
        run(REPO / "clean.py", bundle)
    (out / "audio16k.wav").unlink(missing_ok=True)      # large temp file, not worth syncing to iCloud
    return out, time.time() - t0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--inbox", type=Path, default=ICLOUD / "VoiceInbox")
    ap.add_argument("--outbox", type=Path, default=ICLOUD / "VoiceOut")
    ap.add_argument("--notify", metavar="NUMBER", help="WhatsApp number to ping (optional)")
    ap.add_argument("--bridge", default="http://localhost:8080", help="WhatsApp bridge URL")
    a = ap.parse_args()

    lock = open(LOCK, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return                                           # another run is already working
    state = set(json.loads(STATE.read_text())) if STATE.exists() else set()

    while True:
        todo = sorted((p for p in a.inbox.iterdir() if p.suffix.lower() in AUDIO and not p.name.startswith(".")
                       and key(p) not in state), key=lambda p: p.stat().st_mtime) if a.inbox.exists() else []
        todo = [p for p in todo if stable(p)]
        if not todo:
            return
        p = todo[0]
        log(f"processing {p.name}")
        try:
            out, secs = process(p, a.outbox, a)
            notify(a.bridge, a.notify, f"Transcript ready for '{p.name}' (took {secs / 60:.0f} min). "
                                       f"Open Files > iCloud Drive > VoiceOut > {out.name}")
            log(f"done in {secs / 60:.1f} min -> {out}")
        except Exception as e:
            log(f"FAILED {p.name}: {e}")
            notify(a.bridge, a.notify, f"Transcription FAILED for '{p.name}'. Check the Mac log.")
        state.add(key(p))                                # also on failure: never loop on a bad file
        STATE.write_text(json.dumps(sorted(state)))


if __name__ == "__main__":
    sys.exit(main())
