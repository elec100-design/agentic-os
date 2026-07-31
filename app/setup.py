"""첫 실행 셋업 — CLI 설치 감지와 provider별 안내 메타데이터.

설치 여부는 `shutil.which`로 binary 존재만 확인한다. 로그인 여부는 각 CLI가
제공하는 **비대화형 상태 명령**만 짧은 타임아웃으로 조회한다
(로그인 프롬프트를 띄우지 않음). 상태 명령이 없거나 실패하면 authStatus는
"unknown"으로 두고, 로그인 절차 안내(authCmd/authHint)만 보여 준다.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

# UI 노출 순서 = providers.PROVIDERS 정식 순서와 동일
CLI_META = {
    "claude": {
        "binary": "claude",
        "label": "Claude Code",
        "vendor": "Anthropic",
        "desc": "코딩·분석·글쓰기에 강한 범용 에이전트",
        "auth_cmd": "claude auth login",
        "auth_hint": ("터미널에서 claude auth login 으로 Anthropic/Claude 구독 "
                      "계정 OAuth를 완료하세요. 로그인 후 재확인을 누르세요."),
        "install_hint": "npm install -g @anthropic-ai/claude-code",
    },
    "codex": {
        "binary": "codex",
        "label": "Codex",
        "vendor": "OpenAI",
        "desc": "ChatGPT/Codex 구독 CLI — 코딩·자동화에 강함",
        "auth_cmd": "codex login",
        "auth_hint": ("터미널에서 codex login 으로 ChatGPT 계정 OAuth 로그인을 "
                      "완료하세요. (API 키가 아니라 구독 세션을 씁니다. "
                      "상태 확인: codex login status)"),
        "install_hint": "npm install -g @openai/codex",
    },
    "antigravity": {
        "binary": "agy",
        "label": "Antigravity",
        "vendor": "Google",
        "desc": "Gemini 기반 에이전트 — Google 계정으로 로그인",
        "auth_cmd": "agy",
        "auth_hint": ("agy 첫 실행 시 Google 계정 OAuth 로그인이 진행되고 "
                      "시스템 키체인에 저장됩니다."),
        "install_hint": "https://antigravity.google 설치 안내 참고",
    },
    "gemini": {
        "binary": "gemini",
        "label": "Gemini CLI",
        "vendor": "Google",
        "desc": "공식 Gemini 터미널 CLI — OAuth 또는 Vertex AI",
        "auth_cmd": "gemini",
        "auth_hint": (
            "권장(업무/Workspace·Vertex): "
            "1) gcloud auth application-default login  "
            "2) ~/.gemini/.env 에 GOOGLE_CLOUD_PROJECT·GOOGLE_CLOUD_LOCATION "
            "(예: global)·GOOGLE_GENAI_USE_VERTEXAI=true  "
            "3) ~/.gemini/settings.json 의 security.auth.selectedType 을 "
            "vertex-ai 로. "
            "또는 터미널에서 gemini 실행 후 Sign in with Google "
            "(oauth-personal) — Workspace는 GOOGLE_CLOUD_PROJECT 필요. "
            "API 키 모드는 워커가 키를 제거해 동작하지 않습니다. "
            "(Antigravity와 별도 CLI)"
        ),
        "install_hint": "npm install -g @google/gemini-cli  # 또는 brew install gemini-cli",
    },
    "grok": {
        "binary": "grok",
        "label": "Grok",
        "vendor": "xAI",
        "desc": "xAI Grok — SuperGrok 구독 CLI",
        "auth_cmd": "grok",
        "auth_hint": "grok CLI 자체 로그인 절차(브라우저 인증)를 완료하세요.",
        "install_hint": "npm install -g @vibe-kit/grok-cli",
    },
    "openclaw": {
        "binary": "openclaw",
        "label": "OpenClaw",
        "vendor": "OpenClaw",
        "desc": "로컬 Gateway 에이전트 — 여러 클라우드 모델 프로필 연결",
        "auth_cmd": "openclaw models auth login",
        "auth_hint": ("터미널에서 openclaw models auth login 으로 모델 "
                      "프로바이더(OAuth/키)를 연결하세요. 프로필이 없으면 "
                      "작업이 실패합니다. Gateway가 필요하면 "
                      "openclaw gateway 를 실행해 두세요."),
        "install_hint": "npm install -g openclaw",
    },
    "hermes": {
        "binary": "hermes",
        "label": "Hermes",
        "vendor": "로컬",
        "desc": "로컬 실행 모델 — 무제한·개인 데이터에 적합",
        "auth_cmd": None,
        "auth_hint": "로컬 실행 — 로그인 불필요, 사용량 무제한입니다.",
        "install_hint": "hermes CLI 설치 안내 참고",
    },
}

OPTIONAL_TOOLS = {
    "codexbar": ("사용량 실측 표시 "
                 "(claude·codex·gemini·grok 등 잔여 사용량 기반 자동 라우팅)"),
    "gh": "GitHub 리포를 작업 위치로 연동",
    "rg": "노트(메모리) 전문 검색",
}

# 로그인 상태 조회 타임아웃(초). 재확인 버튼에서 호출되므로 짧게 유지.
AUTH_PROBE_TIMEOUT_SEC = 4


def _run_probe(argv, timeout=None):
    """비대화형 상태 명령 실행. (returncode, stdout+stderr) 또는 실패 시 None."""
    timeout = timeout if timeout is not None else AUTH_PROBE_TIMEOUT_SEC
    try:
        r = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def _auth_ok(detail=None):
    return {"authStatus": "ok", "authDetail": detail}


def _auth_needed(detail=None):
    return {"authStatus": "needed", "authDetail": detail}


def _auth_unknown(detail=None):
    return {"authStatus": "unknown", "authDetail": detail}


def _auth_na(detail=None):
    return {"authStatus": "na", "authDetail": detail}


def probe_claude_auth():
    r = _run_probe(["claude", "auth", "status"])
    if r is None:
        return _auth_unknown()
    code, text = r
    try:
        data = json.loads(text.strip().splitlines()[0] if text.strip() else "{}")
    except (json.JSONDecodeError, IndexError, TypeError):
        data = None
    if isinstance(data, dict) and "loggedIn" in data:
        if data.get("loggedIn"):
            method = data.get("authMethod") or data.get("apiKeySource") or ""
            return _auth_ok(str(method) if method else "logged in")
        return _auth_needed("not logged in")
    low = text.lower()
    if "logged in" in low and "not logged" not in low:
        return _auth_ok()
    if "not logged" in low or "not authenticated" in low:
        return _auth_needed()
    return _auth_unknown(text.strip()[:120] or None)


def probe_codex_auth():
    r = _run_probe(["codex", "login", "status"])
    if r is None:
        return _auth_unknown()
    _, text = r
    low = text.lower()
    if "not logged in" in low:
        return _auth_needed("not logged in")
    if "logged in" in low:
        # e.g. "Logged in using ChatGPT"
        detail = text.strip().splitlines()[0][:120] if text.strip() else "logged in"
        return _auth_ok(detail)
    return _auth_unknown(text.strip()[:120] or None)


def probe_openclaw_auth():
    r = _run_probe(["openclaw", "models", "auth", "list", "--json"])
    if r is None:
        return _auth_unknown()
    code, text = r
    # JSON may be preceded by warnings on stderr mixed in — find first object
    start = text.find("{")
    if start < 0:
        if "profiles: (none)" in text.lower() or "Profiles: (none)" in text:
            return _auth_needed("no auth profiles")
        return _auth_unknown()
    try:
        data = json.loads(text[start:])
    except json.JSONDecodeError:
        return _auth_unknown()
    profiles = data.get("profiles") if isinstance(data, dict) else None
    if isinstance(profiles, list):
        if profiles:
            n = len(profiles)
            return _auth_ok(f"{n} profile(s)")
        return _auth_needed("no auth profiles")
    return _auth_unknown()


def _gemini_env_value(key):
    """os.environ → ~/.gemini/.env 순으로 값 조회."""
    val = (os.environ.get(key) or "").strip()
    if val:
        return val
    env_path = Path.home() / ".gemini" / ".env"
    if not env_path.is_file():
        return ""
    try:
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k.strip() == key:
                return v.strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def _has_valid_adc():
    """Application Default Credentials(JSON)가 유효한지 확인.

    GOOGLE_APPLICATION_CREDENTIALS 가 OAuth client_secret 등 잘못된
    파일을 가리키면 기본 ADC 경로를 본다.
    """
    candidates = []
    gac = (os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") or "").strip()
    if gac:
        candidates.append(Path(gac))
    candidates.append(
        Path.home() / ".config" / "gcloud" / "application_default_credentials.json"
    )
    valid_types = {
        "authorized_user",
        "service_account",
        "external_account",
        "external_account_authorized_user",
        "impersonated_service_account",
        "gdch_service_account",
    }
    for path in candidates:
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and data.get("type") in valid_types:
            return True, str(path)
    return False, None


def probe_gemini_auth():
    """Gemini CLI: settings + credential 파일로 판단(CLI 실행 없음).

    Agentic OS 워커는 API 키 환경변수를 제거하므로 gemini-api-key 모드는
    needed로 본다. 허용 모드:
      - oauth-personal / login-with-google + oauth 자격 파일
      - vertex-ai + 유효 ADC + GOOGLE_CLOUD_PROJECT
    """
    home = Path.home()
    settings_path = home / ".gemini" / "settings.json"
    selected = None
    if settings_path.is_file():
        try:
            data = json.loads(settings_path.read_text(encoding="utf-8"))
            selected = (
                (data.get("security") or {}).get("auth") or {}
            ).get("selectedType")
        except (OSError, json.JSONDecodeError):
            selected = None
    oauth_paths = [
        home / ".gemini" / "oauth_creds.json",
        home / ".gemini" / "google_accounts.json",
        home / ".config" / "gemini" / "oauth_creds.json",
    ]
    has_oauth = any(p.is_file() for p in oauth_paths)
    if selected in ("oauth-personal", "login-with-google") or has_oauth:
        if has_oauth:
            return _auth_ok(selected or "oauth")
        if selected in ("oauth-personal", "login-with-google"):
            return _auth_needed("oauth selected but no credentials file")
    if selected == "gemini-api-key":
        return _auth_needed(
            "API key mode — use Vertex AI (ADC) or Sign in with Google instead"
        )
    if selected == "vertex-ai":
        project = (
            _gemini_env_value("GOOGLE_CLOUD_PROJECT")
            or _gemini_env_value("GOOGLE_CLOUD_PROJECT_ID")
        )
        ok_adc, adc_path = _has_valid_adc()
        if not project:
            return _auth_needed(
                "vertex-ai — set GOOGLE_CLOUD_PROJECT "
                "(aos.env or ~/.gemini/.env)"
            )
        if not ok_adc:
            return _auth_needed(
                "vertex-ai — run: gcloud auth application-default login"
            )
        loc = _gemini_env_value("GOOGLE_CLOUD_LOCATION") or "global"
        return _auth_ok(f"vertex-ai project={project} loc={loc}")
    # 설정 없음 / 알 수 없음
    if not settings_path.exists():
        return _auth_needed(
            "not configured — vertex-ai (ADC) or Sign in with Google"
        )
    return _auth_unknown(selected)


def probe_hermes_auth():
    return _auth_na("local")


# 이름 → probe 함수. 없는 프로바이더는 authStatus unknown.
AUTH_PROBES = {
    "claude": probe_claude_auth,
    "codex": probe_codex_auth,
    "gemini": probe_gemini_auth,
    "openclaw": probe_openclaw_auth,
    "hermes": probe_hermes_auth,
    # antigravity / grok: 안전한 비대화형 status 명령이 없어 unknown
}


def detect(which=None, probe_auth=True):
    """provider·보조 도구별 설치 상태(+선택적 로그인 상태).

    which / probe_auth 는 테스트에서 주입·비활성 가능.
    """
    if which is None:
        which = shutil.which
    providers = {}
    for name, meta in CLI_META.items():
        path = which(meta["binary"])
        entry = {
            "installed": bool(path),
            "path": path,
            "label": meta["label"],
            "vendor": meta["vendor"],
            "desc": meta["desc"],
            "authCmd": meta["auth_cmd"],
            "authHint": meta["auth_hint"],
            "installHint": meta["install_hint"],
            "authStatus": "unknown",
            "authDetail": None,
        }
        if not path:
            entry["authStatus"] = "needed" if meta["auth_cmd"] else "na"
            entry["authDetail"] = "not installed"
        elif probe_auth:
            probe = AUTH_PROBES.get(name)
            if probe is not None:
                try:
                    result = probe()
                    entry.update(result)
                except Exception:
                    entry["authStatus"] = "unknown"
            # probe 없는 CLI는 unknown 유지
        providers[name] = entry
    tools = {}
    for name, desc in OPTIONAL_TOOLS.items():
        tools[name] = {"installed": bool(which(name)), "desc": desc}
    return {"providers": providers, "tools": tools}
