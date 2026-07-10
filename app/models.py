"""프로바이더별 선택 가능 모델을 CLI에서 동적으로 수집한다.

최신 모델 출시 시 하드코딩 없이 자동 반영:
  - claude: 패밀리 별칭(fable/opus/sonnet/haiku) — CLI가 항상 최신으로 해석
  - antigravity: `agy models` 출력(표시명이 --model 값)
  - grok: `grok models` 출력
  - hermes: 로컬 기본값(선택 없음)

`codexbar` 사용량 캐시와 같이 백그라운드로 주기 갱신하고 JSON 캐시를 읽는다.
조회 실패 시 config.FALLBACK_PROVIDER_MODELS 로 폴백.
"""
from __future__ import annotations

import asyncio
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from app import config


def _now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _default_entry(label="기본값"):
    return {"label": label, "model": None, "default": True}


def _entry(label, model, default=False):
    return {"label": label, "model": model, "default": default}


# --- parsers (순수 함수, 테스트 용이) ---------------------------------------

_GROK_MODEL_RE = re.compile(
    r"^\s*[\*\-]\s+([^\s(]+)"  # * id  or  - id
    r"(?:\s*\((default)\))?",
    re.I,
)
_GROK_DEFAULT_RE = re.compile(r"Default model:\s*(\S+)", re.I)


def parse_grok_models(text):
    """`grok models` stdout → entry list."""
    default_id = None
    m = _GROK_DEFAULT_RE.search(text or "")
    if m:
        default_id = m.group(1)
    entries = []
    seen = set()
    for line in (text or "").splitlines():
        mm = _GROK_MODEL_RE.match(line)
        if not mm:
            continue
        mid = mm.group(1).strip()
        if not mid or mid in seen:
            continue
        seen.add(mid)
        is_default = bool(mm.group(2)) or mid == default_id
        label = mid
        if is_default:
            label = f"{mid} (기본)"
        entries.append(_entry(label, mid, default=is_default))
    if not entries:
        return []
    # 기본값이 목록에 없으면 CLI 기본(플래그 생략)을 맨 앞에
    if not any(e.get("default") for e in entries):
        entries.insert(0, _default_entry())
    else:
        # 기본 모델은 --model 생략 옵션도 제공
        entries.insert(0, _default_entry())
        # default 플래그는 '기본값' 행에만 유지
        for e in entries[1:]:
            e["default"] = False
    return entries


def parse_agy_models(text):
    """`agy models` stdout → entry list. 표시명이 곧 --model 값."""
    entries = [_default_entry()]
    seen = set()
    for line in (text or "").splitlines():
        label = line.strip()
        if not label or label.lower().startswith("usage:") or label.lower().startswith("error"):
            continue
        if label in seen:
            continue
        seen.add(label)
        entries.append(_entry(label, label))
    return entries if len(entries) > 1 else []


_CLAUDE_ALIAS_DEFAULT_RE = re.compile(
    r'aliases:\s*\{[^}]*?'
    r'opus:\s*\{[^}]*?default:\s*"([^"]+)"[^}]*?\}[^}]*?'
    r'sonnet:\s*(?:"([^"]+)"|\{[^}]*?default:\s*"([^"]+)")',
    re.I | re.S,
)
# 바이너리 minify 형태: aliases:{opus:{default:"claude-opus-4-8",...},sonnet:...
_CLAUDE_ALIAS_PAIR_RE = re.compile(
    r'\b(opus|sonnet|haiku|fable)\s*:\s*(?:\{[^}]{0,200}?default:\s*)?"(claude-[a-z0-9][a-z0-9.\-]*)"',
    re.I,
)


def parse_claude_alias_map(blob):
    """claude 바이너리/텍스트에서 패밀리 별칭 → 현재 기본 full id 맵."""
    if not blob:
        return {}
    if isinstance(blob, bytes):
        try:
            text = blob.decode("utf-8", errors="ignore")
        except Exception:
            text = blob.decode("latin-1", errors="ignore")
    else:
        text = blob
    out = {}
    for m in _CLAUDE_ALIAS_PAIR_RE.finditer(text):
        alias = m.group(1).lower()
        full = m.group(2)
        # 더 긴/최신 버전이 나중에 올 수 있어 덮어씀. 날짜 접미사 없는 별칭 id 선호
        prev = out.get(alias)
        if prev is None or (len(full) <= len(prev) and "-" in full):
            out[alias] = full
    return out


def claude_models_from_aliases(alias_map=None):
    """패밀리 별칭 목록. model id는 항상 별칭 → CLI가 최신 full id로 해석."""
    alias_map = alias_map or {}
    families = [
        ("fable", "Fable"),
        ("opus", "Opus"),
        ("sonnet", "Sonnet"),
        ("haiku", "Haiku"),
    ]
    entries = [_default_entry()]
    for alias, pretty in families:
        full = alias_map.get(alias)
        label = f"{pretty} · {full}" if full else f"{pretty} (최신)"
        entries.append(_entry(label, alias))
    return entries


_HERMES_MODEL_RE = re.compile(r"Model:\s*(\S+)", re.I)


def parse_hermes_status(text):
    """`hermes status`에서 현재 모델 라벨만 표시(선택은 로컬 기본값 유지)."""
    m = _HERMES_MODEL_RE.search(text or "")
    if m:
        return [_default_entry(f"기본값 ({m.group(1)})")]
    return [_default_entry()]


# --- discovery -------------------------------------------------------------

async def _run_cmd(argv, timeout):
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (FileNotFoundError, OSError):
        return "", 127
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return "", 124
    text = (out or b"").decode("utf-8", errors="replace")
    if not text.strip():
        text = (err or b"").decode("utf-8", errors="replace")
    return text, proc.returncode or 0


def _claude_binary_path():
    path = shutil.which("claude")
    if not path:
        return None
    p = Path(path)
    try:
        return p.resolve()
    except OSError:
        return p


def _read_claude_alias_map():
    path = _claude_binary_path()
    if not path or not path.is_file():
        return {}
    try:
        # 바이너리 전체가 크므로 alias 근처 문자열만 스캔
        data = path.read_bytes()
    except OSError:
        return {}
    return parse_claude_alias_map(data)


async def discover_claude(timeout=None):
    timeout = timeout or config.MODELS_DISCOVER_TIMEOUT_SEC
    # 별칭은 네트워크 불필요. 바이너리에서 현재 매핑 라벨만 보강.
    alias_map = await asyncio.to_thread(_read_claude_alias_map)
    return claude_models_from_aliases(alias_map)


async def discover_antigravity(timeout=None):
    timeout = timeout or config.MODELS_DISCOVER_TIMEOUT_SEC
    text, code = await _run_cmd(["agy", "models"], timeout)
    if code != 0:
        return []
    return parse_agy_models(text)


async def discover_grok(timeout=None):
    timeout = timeout or config.MODELS_DISCOVER_TIMEOUT_SEC
    text, code = await _run_cmd(["grok", "models"], timeout)
    if code != 0:
        return []
    return parse_grok_models(text)


async def discover_hermes(timeout=None):
    timeout = timeout or config.MODELS_DISCOVER_TIMEOUT_SEC
    text, code = await _run_cmd(["hermes", "status"], timeout)
    if code != 0:
        return parse_hermes_status("")
    return parse_hermes_status(text)


_DISCOVERERS = {
    "claude": discover_claude,
    "antigravity": discover_antigravity,
    "grok": discover_grok,
    "hermes": discover_hermes,
}


def _fallback():
    """config 폴백 목록을 깊은 복사."""
    raw = getattr(config, "FALLBACK_PROVIDER_MODELS", None) or {}
    return {
        k: [dict(e) for e in v]
        for k, v in raw.items()
    }


async def fetch(timeout=None):
    """모든 프로바이더 모델 목록을 조회. 실패 항목은 폴백."""
    timeout = timeout or config.MODELS_DISCOVER_TIMEOUT_SEC
    fallback = _fallback()
    results = await asyncio.gather(
        *[_DISCOVERERS[name](timeout) for name in _DISCOVERERS],
        return_exceptions=True,
    )
    out = {}
    for name, result in zip(_DISCOVERERS, results):
        if isinstance(result, Exception) or not result:
            out[name] = fallback.get(name, [_default_entry()])
        else:
            out[name] = result
    # config에만 있는 프로바이더 키도 유지
    for name, entries in fallback.items():
        out.setdefault(name, entries)
    return out


def read_cache():
    path = config.MODELS_CACHE_PATH
    if not path.exists():
        return {"updatedAt": None, "providers": {}, "source": "empty"}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"updatedAt": None, "providers": {}, "source": "empty"}
    if not isinstance(data, dict):
        return {"updatedAt": None, "providers": {}, "source": "empty"}
    providers = data.get("providers") or {}
    if not isinstance(providers, dict):
        providers = {}
    return {
        "updatedAt": data.get("updatedAt"),
        "providers": providers,
        "source": "cache",
    }


def write_cache(providers):
    path = config.MODELS_CACHE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"updatedAt": _now_iso(), "providers": providers},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def get_provider_models():
    """UI/검증용 모델 목록. 캐시 → 폴백."""
    cache = read_cache()
    providers = cache.get("providers") or {}
    fallback = _fallback()
    if not providers:
        return fallback
    out = {}
    for name, fb in fallback.items():
        entries = providers.get(name)
        out[name] = entries if entries else fb
    # 캐시에만 있는 추가 프로바이더
    for name, entries in providers.items():
        out.setdefault(name, entries)
    return out


def is_valid_model(provider, model):
    """폼 검증. 빈 값(기본값)은 항상 허용."""
    if not model:
        return True
    return any(
        (m.get("model") or "") == model
        for m in get_provider_models().get(provider, [])
    )


async def refresh_loop(stop_event, interval=None, fetcher=None):
    """백그라운드에서 주기적으로 모델 목록을 갱신해 캐시에 기록."""
    interval = interval or config.MODELS_REFRESH_SEC
    fetcher = fetcher or fetch
    while not stop_event.is_set():
        try:
            write_cache(await fetcher())
        except Exception:
            pass
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass
