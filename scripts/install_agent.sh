#!/usr/bin/env bash
# Installs a launchd agent that runs inbox_worker.py whenever the iCloud inbox changes
# (plus a 5-minute fallback). Usage: bash scripts/install_agent.sh [whatsapp-number]
# Uninstall: launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.voice-memo-worker.plist && rm that file
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
LABEL=com.voice-memo-worker
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
INBOX="$HOME/Library/Mobile Documents/com~apple~CloudDocs/VoiceInbox"
NOTIFY="${1:-}"
[ -x "$REPO/venv/bin/python" ] || { echo "run the setup in README first (venv missing)"; exit 1; }
mkdir -p "$INBOX" "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"

{
cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array>
    <string>$REPO/venv/bin/python</string><string>-I</string><string>$REPO/inbox_worker.py</string>
EOF
[ -n "$NOTIFY" ] && printf '    <string>--notify</string><string>%s</string>\n' "$NOTIFY"
cat <<EOF
  </array>
  <key>WatchPaths</key><array><string>$INBOX</string></array>
  <key>StartInterval</key><integer>300</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$HOME/Library/Logs/voice-memo-worker.log</string>
  <key>StandardErrorPath</key><string>$HOME/Library/Logs/voice-memo-worker.log</string>
</dict></plist>
EOF
} > "$PLIST"

launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "installed $PLIST (log: ~/Library/Logs/voice-memo-worker.log)"
