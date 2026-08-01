"""워크스페이스별 MCP 서버 설정.

지금은 claude 만 지원한다. claude 는 `--mcp-config <file>` 로 그 실행 한 번
에만 적용되는(전역 상태를 안 건드리는) MCP 서버 목록을 줄 수 있다(`claude
--help` 실측 확인, 2026-08-01). codex(`codex mcp add`)와 gemini(`gemini mcp
add`)는 전역 설정 파일(~/.codex/config.toml, 사용자 settings.json)에 영구히
쓰는 방식뿐이고 그 실행 한 번에만 먹는 옵션이 없다 — 워크스페이스 스코프
모델과 맞지 않는다. 지원하는 척 하지 않고 provider.supports_mcp 로 명시한다.

data/mcp_servers.json 에 저장한다(workspaces.json 과 같은 패턴):
  {"id": str, "workspace_id": str, "name": str, "command": str,
   "args": [str], "env": {str: str}}

MCP 서버 프로세스 자체(각 서버가 실제로 띄우는 하위 프로세스)의 수명은
claude CLI 가 관리한다 — claude 프로세스가 끝나면 그 자식들도 함께 정리되는
것이 정상 동작이라고 가정한다(별도 프로세스 그룹 종료를 이 앱이 구현하지는
않는다 — worker.terminate_job_procs 는 claude 프로세스 자체만 죽인다. 취소·
타임아웃 시 MCP 서버 하위 프로세스가 잠깐 고아가 될 수 있는 것은 알려진
한계이며, 이 파일의 책임 범위 밖이다).
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from app import config, workspace


def _load():
    path = Path(config.MCP_SERVERS_PATH)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _save(items):
    path = Path(config.MCP_SERVERS_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def list_all():
    return _load()


def list_for_workspace(workspace_id):
    if not workspace_id:
        return []
    return [s for s in _load() if s["workspace_id"] == workspace_id]


def add(workspace_id, name, command, args=None, env=None):
    name = (name or "").strip()
    command = (command or "").strip()
    if not workspace_id or not workspace.get(workspace_id):
        raise ValueError("등록된 작업 위치가 아닙니다")
    if not name:
        raise ValueError("서버 이름을 입력하세요")
    if not command:
        raise ValueError("실행 명령을 입력하세요")
    items = _load()
    if any(s["workspace_id"] == workspace_id and s["name"] == name for s in items):
        raise ValueError("같은 이름의 서버가 이미 있습니다")
    server = {
        "id": str(uuid.uuid4()), "workspace_id": workspace_id, "name": name,
        "command": command, "args": list(args or []), "env": dict(env or {}),
    }
    items.append(server)
    _save(items)
    return server


def remove(server_id):
    items = _load()
    kept = [s for s in items if s["id"] != server_id]
    if len(kept) == len(items):
        return False
    _save(kept)
    return True


def config_for_workdir(workdir):
    """workdir(절대경로) 에 매칭되는 등록 워크스페이스의 MCP 서버 설정을 claude
    --mcp-config 형식({"mcpServers": {...}})으로 돌려준다. 없으면 None."""
    if not workdir:
        return None
    ws_id = None
    for w in workspace.list_workspaces():
        if config.paths_equivalent(w["path"], workdir):
            ws_id = w["id"]
            break
    servers = list_for_workspace(ws_id)
    if not servers:
        return None
    return {"mcpServers": {
        s["name"]: {"command": s["command"], "args": s["args"], "env": s["env"]}
        for s in servers
    }}


def write_config_file(workdir):
    """config_for_workdir 결과를 파일로 써서 경로를 돌려준다. 없으면 None.

    잡마다 새로 계산해 안정적인 경로(워크스페이스 id 기준)에 덮어쓴다 — 서버
    목록이 바뀌어도 다음 실행부터 바로 반영되고, 별도 무효화·정리 로직이
    필요 없다(임시 파일이 아니라 워크스페이스별로 하나만 유지되는 캐시다)."""
    data = config_for_workdir(workdir)
    if data is None:
        return None
    ws_id = None
    for w in workspace.list_workspaces():
        if config.paths_equivalent(w["path"], workdir):
            ws_id = w["id"]
            break
    out_dir = Path(config.DATA_DIR) / "mcp_configs"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{ws_id}.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path
