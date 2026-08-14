"""사용자 설정(활성 에이전트) — `data/settings.json`에 저장한다.

첫 실행 셋업(/setup)에서 사용자가 보유한 CLI만 골라 활성화하면, UI(에이전트
칩·사용량 사이드바)·자동 라우팅·협의 모드가 모두 그 목록으로 좁혀진다.
파일이 없거나 손상되면 전체 활성(기본값)으로 동작해 요청을 깨뜨리지 않는다.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app import config
from app.providers import PROVIDERS

ALL = list(PROVIDERS)  # 정식 순서 = providers.PROVIDERS 등록 순서

_DEFAULTS = {"version": 1, "enabled_providers": ALL,
             "setup_completed": False, "completed_at": None,
             # 프리셋 에이전트를 이미 한 번 넣었는지 — 사용자가 지운 프리셋이
             # 재시작마다 되살아나지 않게 하는 표시(app/agents.py:seed_presets).
             "agents_seeded": False}


def load():
    path = Path(config.SETTINGS_PATH)
    if not path.exists():
        return dict(_DEFAULTS)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return dict(_DEFAULTS)
        return {**_DEFAULTS, **data}
    except (OSError, json.JSONDecodeError):
        return dict(_DEFAULTS)


def _canonical(names):
    """유효 provider만 남기고 정식 순서로 정렬(중복 제거)."""
    picked = {n for n in (names or []) if n in ALL}
    return [n for n in ALL if n in picked]


def save(enabled):
    """활성 에이전트 목록을 저장하고 셋업 완료로 표시한다."""
    enabled = _canonical(enabled)
    if not enabled:
        raise ValueError("에이전트를 최소 1개 선택해야 합니다")
    # 기존 값 위에 덮어쓴다 — 여기서 관리하지 않는 키(agents_seeded 등)가
    # 셋업을 다시 저장할 때마다 지워지면 안 된다.
    data = {**load(), "version": 1, "enabled_providers": enabled,
            "setup_completed": True,
            "completed_at": datetime.now(timezone.utc).isoformat(
                timespec="seconds")}
    _write(data)
    return data


def _write(data):
    path = Path(config.SETTINGS_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def mark_agents_seeded():
    """프리셋 에이전트 시드 완료를 기록한다(멱등)."""
    data = load()
    data["agents_seeded"] = True
    _write(data)
    return data


def enabled_providers():
    """활성 에이전트 목록(정식 순서). 저장값이 비었거나 무효면 전체(방어적)."""
    return _canonical(load().get("enabled_providers")) or list(ALL)


def is_enabled(name):
    return name in enabled_providers()


def setup_completed():
    return bool(load().get("setup_completed"))


def council_available():
    """협의 모드 가능 여부 — 설정된 멤버 중 활성 에이전트가 최소 인원 이상."""
    overlap = set(config.COUNCIL_MEMBERS) & set(enabled_providers())
    return len(overlap) >= config.COUNCIL_MIN_MEMBERS
