#!/bin/sh
# Agentic OS 원스톱 부트스트랩 — 클론 직후 이 스크립트 하나로 첫 실행까지.
#   ./bootstrap.sh          # 의존성 설치 + 서버 실행
#   ./bootstrap.sh --no-run # 설치만 (서버는 나중에 직접 실행)
#
# 하는 일: (1) 가상환경(.venv) 생성 — uv 있으면 uv, 없으면 python -m venv
#          (2) 의존성 설치  (3) aos.env 없으면 예시에서 시드
#          (4) 포트 사용 여부 안내  (5) 브라우저 열고 서버 실행
# 서비스 자동 시작(로그인 시 백그라운드)은 별도: macOS는 ./install.sh(launchd),
# Linux는 deploy/agentic-os.service(systemd) 참고.
set -e

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$APP_DIR"
VENV="$APP_DIR/.venv"
PY="$VENV/bin/python3"
RUN=1
[ "$1" = "--no-run" ] && RUN=0

# --- 1) 가상환경 ---
if [ ! -x "$PY" ]; then
  if command -v uv >/dev/null 2>&1; then
    echo "→ uv로 가상환경 생성"
    uv venv "$VENV"
  elif command -v python3 >/dev/null 2>&1; then
    echo "→ python3 -m venv로 가상환경 생성"
    python3 -m venv "$VENV"
  else
    echo "오류: python3(또는 uv)가 필요합니다." >&2
    exit 1
  fi
fi

# --- 2) 의존성 ---
echo "→ 의존성 설치"
if command -v uv >/dev/null 2>&1; then
  uv pip install --python "$PY" -r requirements.txt
else
  "$PY" -m pip install --upgrade pip >/dev/null
  "$PY" -m pip install -r requirements.txt
fi

# --- 3) aos.env 시드 ---
if [ ! -f "$APP_DIR/aos.env" ] && [ -f "$APP_DIR/aos.env.example" ]; then
  cp "$APP_DIR/aos.env.example" "$APP_DIR/aos.env"
  echo "→ aos.env 생성 (예시에서 복사 — 필요하면 편집)"
fi

# --- 포트 읽기 (aos.env 또는 기본 8899) ---
PORT="$(sed -n 's/^AOS_PORT=//p' "$APP_DIR/aos.env" 2>/dev/null | tail -1 | tr -d ' \"'"'"'')"
PORT="${AOS_PORT:-${PORT:-8899}}"
HOST="$(sed -n 's/^AOS_HOST=//p' "$APP_DIR/aos.env" 2>/dev/null | tail -1 | tr -d ' \"'"'"'')"
HOST="${AOS_HOST:-${HOST:-127.0.0.1}}"

# --- 4) 포트 점유 안내 ---
if command -v lsof >/dev/null 2>&1 && lsof -ti ":$PORT" >/dev/null 2>&1; then
  echo "⚠️  포트 $PORT 를 이미 사용 중입니다. 기존 서버를 끄거나 AOS_PORT를 바꾸세요."
fi

echo ""
echo "✅ 준비 완료.  진단: $PY -m app doctor"

[ "$RUN" = "0" ] && { echo "실행: $PY -m app  (또는 $PY -m uvicorn app.main:app --host $HOST --port $PORT)"; exit 0; }

# --- 5) 브라우저 열고 실행 ---
URL="http://$HOST:$PORT"
echo "→ $URL 에서 서버 실행 (Ctrl+C로 종료)"
( sleep 2
  if command -v open >/dev/null 2>&1; then open "$URL"        # macOS
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL" >/dev/null 2>&1  # Linux
  fi ) &
exec "$PY" -m app
