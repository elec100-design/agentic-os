"""CodexBar(`codexbar usage`)에서 각 에이전트의 실제 사용량을 읽어온다.

`codexbar usage --provider <p> --format json`은 네트워크 호출이라 느리므로
백그라운드 태스크가 주기적으로 조회해 JSON 파일에 캐시하고, 웹 요청은 캐시만 읽는다.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from app import config


def _now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize(raw_items):
    """`codexbar usage --format json` 결과(list) → {provider: state} 정규화.

    state = {used(%)|None, remaining(%)|None, resetsAt(iso|None),
             windows[{title,used,resetsAt,desc}], available(bool|None),
             source, error(str|None)}
    provider 이름은 CodexBar id를 우리 이름으로 되돌린 값(claude/gemini/grok).
    """
    id_to_name = {v: k for k, v in config.CODEXBAR_PROVIDERS.items()}
    out = {}
    for item in raw_items or []:
        name = id_to_name.get(item.get("provider"))
        if name is None:
            continue
        err = item.get("error")
        if err:
            msg = err.get("message") if isinstance(err, dict) else str(err)
            out[name] = _error_state(item.get("source"), msg)
            continue
        usage = item.get("usage") or {}
        # 계정 단위 창(primary/secondary/tertiary)이 실제 가용성을 결정한다.
        # extraRateWindows는 모델·루틴 단위라 표시만 하고 가용성 계산엔 넣지 않는다
        # (예: "Fable only" 주간 캡이 100%여도 Opus/Sonnet는 남아 있을 수 있음).
        core = []
        windows = []
        for key in ("primary", "secondary", "tertiary"):
            w = usage.get(key)
            if w and w.get("usedPercent") is not None:
                entry = {"title": w.get("title") or _WINDOW_LABEL.get(key, key),
                         "used": w["usedPercent"], "resetsAt": w.get("resetsAt")}
                core.append(entry)
                windows.append(entry)
        for e in usage.get("extraRateWindows") or []:
            w = e.get("window") or {}
            if w.get("usedPercent") is not None:
                windows.append({"title": e.get("title") or e.get("id"),
                                "used": w["usedPercent"],
                                "resetsAt": w.get("resetsAt")})
        if not core:
            out[name] = _error_state(item.get("source"), "사용량 창 없음")
            continue
        binding = max(core, key=lambda w: w["used"])
        used = binding["used"]
        out[name] = {
            "used": used,
            "remaining": max(0, 100 - used),
            "resetsAt": binding["resetsAt"],
            "windows": windows,
            "available": used < 100,
            "source": item.get("source"),
            "error": None,
        }
    return out


_WINDOW_LABEL = {"primary": "현재 세션", "secondary": "주간", "tertiary": "월간"}


def _error_state(source, message):
    return {"used": None, "remaining": None, "resetsAt": None, "windows": [],
            "available": None, "source": source, "error": message}


async def _fetch_one(name, cb_id, timeout):
    try:
        proc = await asyncio.create_subprocess_exec(
            "codexbar", "usage", "--provider", cb_id, "--format", "json",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except (FileNotFoundError, OSError):
        return []  # codexbar 미설치
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return []
    try:
        return json.loads(out.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return []


async def fetch(providers=None, timeout=None):
    """대상 프로바이더를 동시 조회해 정규화된 dict를 반환."""
    providers = providers or config.CODEXBAR_PROVIDERS
    timeout = timeout or config.CODEXBAR_TIMEOUT_SEC
    results = await asyncio.gather(
        *[_fetch_one(name, cb_id, timeout) for name, cb_id in providers.items()]
    )
    merged = [item for r in results for item in r]
    return normalize(merged)


def read_cache():
    path = config.USAGE_CACHE_PATH
    if not path.exists():
        return {"updatedAt": None, "providers": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"updatedAt": None, "providers": {}}


def write_cache(providers):
    path = config.USAGE_CACHE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"updatedAt": _now_iso(), "providers": providers},
                   ensure_ascii=False),
        encoding="utf-8",
    )


async def refresh_loop(stop_event, interval=None, fetcher=None):
    """백그라운드에서 주기적으로 사용량을 갱신해 캐시에 기록."""
    interval = interval or config.USAGE_REFRESH_SEC
    fetcher = fetcher or fetch
    while not stop_event.is_set():
        try:
            write_cache(await fetcher())
        except Exception:  # 갱신 실패가 루프를 죽이면 안 됨
            pass
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass
