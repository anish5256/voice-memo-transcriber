#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
export CLANG_MODULE_CACHE_PATH="$PWD/.build/mcache"
swift build -c release --cache-path .build/cache --config-path .build/config --security-path .build/security
bin_dir="$(swift build -c release --cache-path .build/cache --config-path .build/config --security-path .build/security --show-bin-path)"
app="$PWD/build/MeetingNotes.app"
mkdir -p "$app/Contents/MacOS"
cp "$bin_dir/MeetingNotes" "$app/Contents/MacOS/MeetingNotes"
cp Info.plist "$app/Contents/Info.plist"
codesign --force --sign - "$app"
printf '%s\n' "$app"
