"""진단(health) 정보 수집 — `/api/health`와 `aos doctor`(CLI)가 공유한다.

서버 자체 상태(파이썬·플랫폼·포트·데이터 디렉터리 쓰기 가능 여부)와, 셋업
감지(설치된 CLI·보조 도구), 활성 에이전트 설정을 한 번에 모아 준다. "왜 안
되지?"를 빠르게 짚기 위한 용도.
"""
from __future__ import annotations

import os
import platform
import sys
from pathlib import Path

from app import config, settings, setup

VERSION = "1.0.0"


def _data_writable():
    """데이터 디렉터리에 실제로 쓸 수 있는지 확인(권한·디스크 문제 조기 발견)."""
    try:
        d = Path(config.DATA_DIR)
        d.mkdir(parents=True, exist_ok=True)
        probe = d / ".health-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def collect():
    """진단 정보 dict. 네트워크 호출 없음(binary 존재만 확인)."""
    detected = setup.detect()
    providers = detected["providers"]
    installed = [n for n, p in providers.items() if p["installed"]]
    enabled = settings.enabled_providers()
    data_writable = _data_writable()
    # 최소 1개 CLI가 설치돼 있고 데이터 저장이 가능하면 "동작 가능" 상태로 본다
    ok = data_writable and bool(installed)
    return {
        "ok": ok,
        "version": VERSION,
        "server": {
            "python": platform.python_version(),
            "python_path": sys.executable,
            "platform": platform.system(),
            "platform_release": platform.release(),
            "host": config.HOST,
            "port": config.PORT,
            "worker_enabled": not os.environ.get("AOS_DISABLE_WORKER"),
            "data_dir": str(config.DATA_DIR),
            "data_writable": data_writable,
            "notes_dir": str(config.MEMORY_DIR),
        },
        "setup": {
            "completed": settings.setup_completed(),
            "enabled_providers": enabled,
        },
        "providers": providers,
        "tools": detected["tools"],
    }
