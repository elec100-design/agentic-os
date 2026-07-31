"""프로바이더별 선택 가능 모델을 CLI에서 동적으로 수집한다.

최신 모델 출시 시 하드코딩 없이 자동 반영:
  - claude: 패밀리 별칭(fable/opus/sonnet/haiku) — CLI가 항상 최신으로 해석
  - codex: `codex debug models` JSON 카탈로그
  - gemini: auto/pro/flash 별칭 + 알려진 full id
  - antigravity: `agy models` 출력(표시명이 --model 값)
  - grok: `grok models` 출력
  - openclaw: `openclaw models list --json` (설정된 모델)
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

# openclaw models list: "openai/gpt-5.5  text  195k  no  no  default"
_OPENCLAW_MODEL_RE = re.compile(
    r"^([a-zA-Z0-9][\w./+\-]+)\s+\S+",
)


def parse_hermes_status(text):
    """`hermes status`에서 현재 모델 라벨만 표시(선택은 로컬 기본값 유지)."""
    m = _HERMES_MODEL_RE.search(text or "")
    if m:
        return [_default_entry(f"기본값 ({m.group(1)})")]
    return [_default_entry()]


def parse_openclaw_models(text):
    """`openclaw models list` 테이블 → entry list."""
    entries = []
    seen = set()
    default_id = None
    for line in (text or "").splitlines():
        raw = line.strip()
        if not raw or raw.lower().startswith("model"):
            continue
        m = _OPENCLAW_MODEL_RE.match(raw)
        if not m:
            continue
        mid = m.group(1).strip()
        if not mid or mid in seen:
            continue
        # 헤더/구분선 스킵
        if mid.lower() in ("model", "models", "---", "input"):
            continue
        seen.add(mid)
        is_default = bool(re.search(r"\bdefault\b", raw, re.I))
        if is_default:
            default_id = mid
        label = f"{mid} (기본)" if is_default else mid
        entries.append(_entry(label, mid, default=is_default))
    if not entries:
        return []
    entries.insert(0, _default_entry())
    for e in entries[1:]:
        e["default"] = False
    if default_id:
        # 기본 모델 라벨만 유지
        for e in entries[1:]:
            if e["model"] == default_id:
                e["label"] = f"{default_id} (기본)"
    return entries


def parse_openclaw_models_json(text):
    """`openclaw models list --json` → entry list.

    설정된(기본 목록) 모델만 쓴다. --all 카탈로그(수천 개)는 UI에 부적합.
    available=false 여도 키는 선택 가능하게 남긴다(로그인 후 사용).
    """
    start = (text or "").find("{")
    if start < 0:
        return []
    try:
        data = json.loads(text[start:])
    except (json.JSONDecodeError, TypeError):
        return []
    rows = data.get("models") if isinstance(data, dict) else None
    if not isinstance(rows, list) or not rows:
        return []
    entries = [_default_entry()]
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        mid = (row.get("key") or row.get("id") or row.get("name") or "").strip()
        if not mid or mid in seen:
            continue
        seen.add(mid)
        tags = row.get("tags") or []
        is_default = isinstance(tags, list) and "default" in tags
        name = (row.get("name") or mid).strip()
        label = f"{name} (기본)" if is_default else name
        if name != mid and not is_default:
            label = f"{name} · {mid}" if name else mid
        elif is_default and name != mid:
            label = f"{name} · {mid} (기본)"
        entries.append(_entry(label, mid, default=False))
    return entries if len(entries) > 1 else []


def parse_codex_models(text):
    """`codex debug models` JSON → entry list.

    visibility=="list" 를 우선하고, 없으면 전체 slug 사용. hide 는 제외.
    """
    start = (text or "").find("{")
    if start < 0:
        return []
    try:
        data = json.loads(text[start:])
    except (json.JSONDecodeError, TypeError):
        return []
    rows = data.get("models") if isinstance(data, dict) else None
    if not isinstance(rows, list) or not rows:
        return []
    listed = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        slug = (row.get("slug") or row.get("id") or "").strip()
        if not slug:
            continue
        vis = (row.get("visibility") or "list").lower()
        if vis == "hide":
            continue
        listed.append(row)
    # list 우선, 없으면 hide 아닌 전체
    preferred = [r for r in listed
                 if (r.get("visibility") or "list").lower() == "list"]
    use = preferred or listed
    if not use:
        return []
    # priority 오름차순(낮을수록 상위) — 없으면 원래 순서
    use = sorted(
        use,
        key=lambda r: (r.get("priority") is None, r.get("priority") or 0),
    )
    entries = [_default_entry()]
    seen = set()
    for row in use:
        slug = (row.get("slug") or row.get("id") or "").strip()
        if not slug or slug in seen:
            continue
        seen.add(slug)
        display = (row.get("display_name") or row.get("name") or slug).strip()
        label = f"{display} · {slug}" if display and display != slug else slug
        entries.append(_entry(label, slug, default=False))
    return entries if len(entries) > 1 else []


# Gemini 패밀리 별칭 — CLI resolveModel이 최신 full id로 해석
_GEMINI_ALIAS_MODELS = [
    ("auto", "Auto"),
    ("pro", "Pro (최신)"),
    ("flash", "Flash (최신)"),
    ("flash-lite", "Flash Lite (최신)"),
]


def gemini_models_fallback():
    """네트워크/CLI 없이 선택 가능한 gemini 모델 목록."""
    entries = [_default_entry()]
    for mid, label in _GEMINI_ALIAS_MODELS:
        entries.append(_entry(label, mid))
    for mid in (
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "gemini-3.5-flash",
        "gemini-3.1-flash-lite",
    ):
        entries.append(_entry(mid, mid))
    return entries


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


async def discover_codex(timeout=None):
    # `codex debug models` — 네트워크/로그인 없이도 카탈로그 JSON 출력.
    timeout = timeout or config.MODELS_DISCOVER_TIMEOUT_SEC
    # 카탈로그가 커서 기본 15초보다 여유를 둔다.
    text, code = await _run_cmd(
        ["codex", "debug", "models"], max(timeout, 25)
    )
    if code != 0 and not text.strip():
        return []
    return parse_codex_models(text)


async def discover_gemini(timeout=None):
    # 공개 `gemini models` 서브커맨드가 없어 별칭+알려진 id 목록 사용.
    # (CLI가 auto/pro/flash 별칭을 최신 full id로 해석)
    return gemini_models_fallback()


async def discover_openclaw(timeout=None):
    timeout = timeout or config.MODELS_DISCOVER_TIMEOUT_SEC
    text, code = await _run_cmd(
        ["openclaw", "models", "list", "--json"], timeout
    )
    if code == 0 and text.strip():
        entries = parse_openclaw_models_json(text)
        if entries:
            return entries
    # JSON 실패 시 테이블 출력 폴백
    text, code = await _run_cmd(["openclaw", "models", "list"], timeout)
    if code != 0:
        return []
    return parse_openclaw_models(text)


_DISCOVERERS = {
    "claude": discover_claude,
    "codex": discover_codex,
    "antigravity": discover_antigravity,
    "gemini": discover_gemini,
    "grok": discover_grok,
    "openclaw": discover_openclaw,
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
    """UI/검증용 모델 목록. 캐시 → 폴백.

    캐시에 '기본값' 한 줄만 있는 경우(신규 프로바이더·조회 실패 잔재)는
    폴백이 더 풍부하면 폴백을 쓴다 → 모델 칩이 바로 노출된다.
    """
    cache = read_cache()
    providers = cache.get("providers") or {}
    fallback = _fallback()
    if not providers:
        return fallback
    out = {}
    for name, fb in fallback.items():
        entries = providers.get(name)
        if not entries:
            out[name] = fb
        elif len(entries) <= 1 and len(fb) > 1:
            out[name] = fb
        else:
            out[name] = entries
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
