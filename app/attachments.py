"""발화에 딸린 첨부 파일 — 저장은 data/uploads/, 전달은 로컬 경로.

파일을 CLI 에이전트에게 넘기는 방법은 '로컬 절대경로를 알려 주고 읽게 한다'
하나뿐이다(헤드리스 CLI 에 바이너리를 실어 보낼 통로가 없다). 그래서 형식이
화면마다 달라지지 않도록 프롬프트 블록을 여기 한 곳에서 만든다.

main.py(잡·채널 메시지)와 agents.py(에이전트 실행)가 함께 쓴다 — agents 가
main 을 import 하면 순환이라 별도 모듈로 둔다.
"""
import json
from pathlib import Path

from app import config

HEADER = "첨부 파일 (로컬 경로에서 읽을 것):"


def block(paths):
    """프롬프트 끝에 붙일 첨부 안내 한 덩이. 없으면 빈 문자열."""
    if not paths:
        return ""
    return f"\n\n{HEADER}\n" + "\n".join(f"- {p}" for p in paths)


def valid_paths(paths):
    """업로드 폴더 안에 실제로 있는 파일만 남긴다.

    경로는 클라이언트가 되돌려 보내는 값이므로 그대로 믿으면 임의 파일을
    에이전트에게 읽히는 통로가 된다(workspace.valid_path 와 같은 취지).
    """
    root = config.UPLOAD_DIR.resolve()
    ok = []
    for raw in paths or []:
        if not raw:
            continue
        try:
            p = Path(str(raw)).resolve()
        except OSError:
            continue
        if p.is_file() and p.is_relative_to(root):
            ok.append(str(p))
    return ok


def of_message(message):
    """메시지 행(messages.attachments JSON)에서 경로 목록을 꺼낸다."""
    if message is None:
        return []
    try:
        raw = message["attachments"]
    except (KeyError, IndexError):
        return []
    if not raw:
        return []
    try:
        loaded = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return [str(p) for p in loaded] if isinstance(loaded, list) else []
