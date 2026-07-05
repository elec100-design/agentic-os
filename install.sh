#!/bin/sh
# Agentic OS launchd 서비스 설치/재설치
set -e
PLIST_SRC="$(cd "$(dirname "$0")" && pwd)/launchd/com.elec100.agentic-os.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.elec100.agentic-os.plist"
mkdir -p "$HOME/Library/LaunchAgents" "$(dirname "$PLIST_SRC")/../data"
launchctl unload "$PLIST_DST" 2>/dev/null || true
# 8899 포트를 점유한 잔여 프로세스(예: 개발용 uvicorn) 정리
lsof -ti :8899 | xargs kill 2>/dev/null || true
cp "$PLIST_SRC" "$PLIST_DST"
launchctl load "$PLIST_DST"
# 주의: plist의 실행 인자가 바뀐 직후 첫 기동은 Gatekeeper 스캔으로 60-90초 걸릴 수 있음
echo "설치 완료 — http://localhost:8899"
