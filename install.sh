#!/bin/sh
# Agentic OS launchd 서비스 설치/재설치
set -e
PLIST_SRC="$(cd "$(dirname "$0")" && pwd)/launchd/com.elec100.agentic-os.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.elec100.agentic-os.plist"
mkdir -p "$HOME/Library/LaunchAgents" "$(dirname "$PLIST_SRC")/../data"
launchctl unload "$PLIST_DST" 2>/dev/null || true
cp "$PLIST_SRC" "$PLIST_DST"
launchctl load "$PLIST_DST"
echo "설치 완료 — http://localhost:8899"
