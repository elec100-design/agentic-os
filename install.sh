#!/bin/sh
# Agentic OS launchd 서비스 설치/재설치.
# 템플릿(launchd/agentic-os.plist.template)을 현재 사용자 환경으로 치환해
# ~/Library/LaunchAgents/ 에 plist 를 생성하고 서비스를 로드합니다.
#
# 앱 설정(볼트 경로·tailnet origin·업로드 한도 등)은 plist 가 아니라 저장소
# 루트의 aos.env 에 둡니다 — config.py 가 기동 시 읽으므로 재설치·수동실행에도
# 유지됩니다(aos.env.example 참고). 아래에서는 서비스 레이블·포트·호스트만
# aos.env 또는 환경변수에서 참고합니다.
set -e

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$APP_DIR/.venv/bin/python3"
TEMPLATE="$APP_DIR/launchd/agentic-os.plist.template"
ENV_FILE="${AOS_ENV_FILE:-$APP_DIR/aos.env}"

# aos.env 에서 스칼라 키(공백 없는 값) 하나를 읽는다. 없으면 빈 문자열.
env_get() {
  [ -f "$ENV_FILE" ] || return 0
  sed -n "s/^$1=//p" "$ENV_FILE" | tail -1 \
    | sed 's/^[[:space:]]*//; s/[[:space:]]*$//; s/^"//; s/"$//; s/^'\''//; s/'\''$//'
}

# 우선순위: 실제 환경변수 > aos.env > 기본값
LABEL="${AOS_SERVICE_LABEL:-$(env_get AOS_SERVICE_LABEL)}"; LABEL="${LABEL:-com.agentic-os.dashboard}"
HOST="${AOS_HOST:-$(env_get AOS_HOST)}"; HOST="${HOST:-127.0.0.1}"
PORT="${AOS_PORT:-$(env_get AOS_PORT)}"; PORT="${PORT:-8899}"
# CLI 들이 설치되는 흔한 위치 + 시스템 PATH
RUN_PATH="$HOME/.local/bin:$HOME/.grok/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

PLIST_DST="$HOME/Library/LaunchAgents/$LABEL.plist"
LEGACY_LABEL="com.elec100.agentic-os"
LEGACY_DST="$HOME/Library/LaunchAgents/$LEGACY_LABEL.plist"

if [ ! -x "$PYTHON" ]; then
  echo "오류: $PYTHON 가 없습니다. 먼저 가상환경을 만드세요:" >&2
  echo "  uv venv .venv && uv pip install -r requirements.txt --python .venv/bin/python3" >&2
  exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents" "$APP_DIR/data"

# 템플릿을 현재 환경으로 치환 (공백·특수문자 안전하게 python 으로 처리)
AOS_TPL="$TEMPLATE" AOS_LABEL="$LABEL" AOS_PY="$PYTHON" AOS_DIR="$APP_DIR" \
AOS_HOST_V="$HOST" AOS_PORT_V="$PORT" AOS_PATH_V="$RUN_PATH" AOS_HOME_V="$HOME" \
"$PYTHON" - "$PLIST_DST" <<'PY'
import os, sys
from xml.sax.saxutils import escape

tpl = open(os.environ["AOS_TPL"], encoding="utf-8").read()
out = (tpl
       .replace("{{LABEL}}", escape(os.environ["AOS_LABEL"]))
       .replace("{{PYTHON}}", escape(os.environ["AOS_PY"]))
       .replace("{{APP_DIR}}", escape(os.environ["AOS_DIR"]))
       .replace("{{HOST}}", escape(os.environ["AOS_HOST_V"]))
       .replace("{{PORT}}", escape(os.environ["AOS_PORT_V"]))
       .replace("{{PATH}}", escape(os.environ["AOS_PATH_V"]))
       .replace("{{HOME}}", escape(os.environ["AOS_HOME_V"])))
open(sys.argv[1], "w", encoding="utf-8").write(out)
PY

# 기존 서비스(신규/구 레이블) 정리
launchctl unload "$PLIST_DST" 2>/dev/null || true
[ -f "$LEGACY_DST" ] && launchctl unload "$LEGACY_DST" 2>/dev/null || true
# 지정 포트를 점유한 잔여 프로세스(예: 개발용 uvicorn) 정리
lsof -ti ":$PORT" | xargs kill 2>/dev/null || true

launchctl load "$PLIST_DST"
# 주의: plist 실행 인자가 바뀐 직후 첫 기동은 Gatekeeper 스캔으로 60-90초 걸릴 수 있음
echo "설치 완료 — http://localhost:$PORT  (label: $LABEL)"
