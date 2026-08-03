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

--- 설치된 claude MCP 서버 조회 (list_installed_claude_servers) ---

수동 입력이 번거롭다는 사용자 요청으로, 이미 claude CLI에 등록된 MCP 서버
목록을 읽어 드롭다운 후보로 쓸 수 있게 한다. 소스는 `claude mcp add -s`의
3-scope 모델(local/user/project, `claude mcp add --help` 실측, 2026-08-03)
과 그대로 대응한다:
  - user    : ~/.claude.json 최상위 "mcpServers" — 모든 프로젝트에서 사용.
  - local   : ~/.claude.json "projects"[workdir]["mcpServers"] — 이 PC에서
              해당 workdir 에서만 쓰는 개인 설정(git 공유 안 됨).
  - project : <workdir>/.mcp.json — 프로젝트에 커밋해 팀과 공유하는 설정.
  - account : claude.ai 웹 계정에 연결된 원격 커넥터(Canva·Gmail 등). 위
              세 소스 어디에도 정의가 없다(~/.claude.json 전체를 문자열
              검색해 확인 — "higgsfield"/"Canva"는 claudeAiMcpEverConnected
              라는 이력 배열에만 이름으로 남고, command/url 은 로컬에 없다).
              유일하게 이름과 URL을 노출하는 소스는 `claude mcp list` 실행
              결과뿐이라 이것만 CLI 출력을 파싱한다(`--json` 옵션 없음,
              `claude mcp --help` 실측). 이 호출은 각 서버에 실제 헬스체크를
              하므로(출력에 "Checking MCP server health…") 느릴 수 있다 —
              동기 웹 요청에 그대로 물리면 안 되고, 캐싱/비동기화는 이 함수를
              호출하는 쪽(향후 UI 라우트)의 책임으로 남긴다.

~/.claude/settings.json 은 실측 결과 비어 있고(권한·훅 설정용) MCP 정의가
없어 소스로 쓰지 않는다.

--- 설치된 서버를 워크스페이스에 연결 (add_from_installed) ---

수동 입력(add)은 고급 옵션으로 남기고, 기본 경로는 list_installed_claude_
servers() 로 조회한 항목을 그대로 복사 저장하는 add_from_installed(workspace_
id, server_key) 다. 레코드 스키마를 "type"(기본 stdio)과 "url" 필드로 확장해
stdio 뿐 아니라 http/sse(계정 커넥터 등)도 저장할 수 있게 했다. 저장 규칙은
add()와 동일: 미등록 워크스페이스/존재하지 않는 키는 ValueError, 같은
워크스페이스에 같은 이름이 있으면 중복 거부. config_for_workdir()의
--mcp-config 직렬화도 type 에 따라 분기한다(stdio → command/args/env,
http/sse → type/url).
"""
from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path

from app import config, workspace

CLAUDE_USER_CONFIG_PATH = Path.home() / ".claude.json"


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


def _require_workspace(workspace_id):
    if not workspace_id or not workspace.get(workspace_id):
        raise ValueError("등록된 작업 위치가 아닙니다")


def _require_unique_name(items, workspace_id, name):
    if any(s["workspace_id"] == workspace_id and s["name"] == name for s in items):
        raise ValueError("같은 이름의 서버가 이미 있습니다")


def add(workspace_id, name, command, args=None, env=None):
    name = (name or "").strip()
    command = (command or "").strip()
    _require_workspace(workspace_id)
    if not name:
        raise ValueError("서버 이름을 입력하세요")
    if not command:
        raise ValueError("실행 명령을 입력하세요")
    items = _load()
    _require_unique_name(items, workspace_id, name)
    server = {
        "id": str(uuid.uuid4()), "workspace_id": workspace_id, "name": name,
        "command": command, "args": list(args or []), "env": dict(env or {}),
        "type": "stdio", "url": None,
    }
    items.append(server)
    _save(items)
    return server


def add_from_installed(workspace_id, server_key):
    """드롭다운에서 고른, 이미 claude에 설치된 서버(server_key)를 그대로 복사해
    현재 워크스페이스에 연결한다. 수동 입력(add) 대신 쓰는 경로 — command/args/
    env 를 직접 채울 필요가 없다. http/sse 타입(계정 커넥터 등)도 그대로 저장
    된다(command 없이 type/url만 있음)."""
    _require_workspace(workspace_id)
    ws = workspace.get(workspace_id)
    installed = list_installed_claude_servers(ws["path"])
    match = next((s for s in installed if s["key"] == server_key), None)
    if match is None:
        raise ValueError("설치된 서버 목록에 없습니다")
    items = _load()
    _require_unique_name(items, workspace_id, match["name"])
    server = {
        "id": str(uuid.uuid4()), "workspace_id": workspace_id, "name": match["name"],
        "command": match["command"], "args": list(match["args"] or []),
        "env": dict(match["env"] or {}), "type": match["type"], "url": match["url"],
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
    return {"mcpServers": {s["name"]: _serialize_server(s) for s in servers}}


def _serialize_server(s):
    """claude --mcp-config 형식으로 서버 하나를 직렬화한다. stdio 는 기존
    command/args/env 그대로(type 필드 없이 — 기존 형식과 호환), http/sse 는
    claude 가 요구하는 {"type": ..., "url": ...} 형태로 만든다(`claude mcp
    add --transport http|sse` 로 등록된 서버의 실제 저장 형식과 동일, 실측)."""
    server_type = s.get("type") or "stdio"
    if server_type == "stdio":
        return {"command": s.get("command"), "args": s.get("args") or [], "env": s.get("env") or {}}
    return {"type": server_type, "url": s.get("url")}


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


def _load_claude_user_config():
    if not CLAUDE_USER_CONFIG_PATH.exists():
        return {}
    try:
        data = json.loads(CLAUDE_USER_CONFIG_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _normalize_entry(name, raw, scope, source):
    raw = raw if isinstance(raw, dict) else {}
    url = raw.get("url")
    server_type = raw.get("type") or ("http" if url else "stdio")
    return {
        "key": f"{scope}:{name}",
        "name": name,
        "scope": scope,
        "source": source,
        "command": raw.get("command"),
        "args": list(raw.get("args") or []),
        "env": dict(raw.get("env") or {}),
        "type": server_type,
        "url": url,
    }


def _parse_claude_mcp_list_output(output):
    """`claude mcp list` 텍스트에서 claude.ai 계정 커넥터 줄만 뽑는다.
    형식: "claude.ai <표시이름>: <url> - <상태>" (실측, 2026-08-03)."""
    servers = []
    for line in output.splitlines():
        line = line.strip()
        if not line.startswith("claude.ai "):
            continue
        name, sep, rest = line.partition(":")
        if not sep:
            continue
        url = rest.strip().split(" - ")[0].strip()
        if not url.startswith("http"):
            continue
        servers.append({
            "key": f"account:{name.strip()}", "name": name.strip(),
            "scope": "account", "source": "claude_cli",
            "command": None, "args": [], "env": {}, "type": "http", "url": url,
        })
    return servers


def list_installed_claude_servers(workdir=None):
    """claude CLI에 이미 등록된 MCP 서버를 정규화한 리스트로 돌려준다.
    스코프별 소스와 스키마 연결 설계는 모듈 상단 주석 참고."""
    servers = []
    user_config = _load_claude_user_config()

    for name, raw in (user_config.get("mcpServers") or {}).items():
        servers.append(_normalize_entry(name, raw, "user", "~/.claude.json"))

    if workdir:
        resolved = str(config.resolve_path(workdir))
        project_entry = {}
        for path_key, entry in (user_config.get("projects") or {}).items():
            if config.paths_equivalent(path_key, resolved):
                project_entry = entry or {}
                break
        for name, raw in (project_entry.get("mcpServers") or {}).items():
            servers.append(_normalize_entry(name, raw, "local", "~/.claude.json"))

        mcp_json_path = Path(resolved) / ".mcp.json"
        if mcp_json_path.exists():
            try:
                mcp_json = json.loads(mcp_json_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                mcp_json = {}
            if isinstance(mcp_json, dict):
                for name, raw in (mcp_json.get("mcpServers") or {}).items():
                    servers.append(_normalize_entry(name, raw, "project", ".mcp.json"))

    try:
        result = subprocess.run(
            ["claude", "mcp", "list"], capture_output=True, text=True, timeout=15,
        )
        servers.extend(_parse_claude_mcp_list_output(result.stdout))
    except (OSError, subprocess.SubprocessError):
        pass

    return servers
