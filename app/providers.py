from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app import config

CONTINUE_PROMPT = (
    "이전 작업이 사용 제한으로 중단되었습니다. "
    "중단 지점부터 이어서 작업을 끝까지 완료해주세요."
)


@dataclass
class ParseResult:
    text: str
    session_id: str | None = None


def _default_resume_at(now=None):
    now = now or datetime.now(timezone.utc)
    return now + timedelta(minutes=config.DEFAULT_RESUME_DELAY_MIN)


class ClaudeProvider:
    name = "claude"
    _limit_re = re.compile(r"usage limit reached|rate.?limit", re.I)
    _epoch_re = re.compile(r"limit reached\|(\d{9,})")

    def build_command(self, prompt, session_id=None, model=None):
        # 헤드리스(-p)는 권한 프롬프트를 못 띄우므로 웹 도구를 사전 허용.
        # --allowedTools 는 <tools...> 가변 옵션이라, 뒤에 오는 비옵션 인자를
        # 전부 도구 이름으로 삼킨다. 프롬프트가 거기 끼면
        # "Input must be provided ... when using --print" 로 실패한다.
        # 도구를 먼저 두고 `--` 로 옵션 파싱을 끝낸 뒤 프롬프트를 넣는다.
        cmd = ["claude", "-p", "--output-format", "json"]
        if model:
            cmd += ["--model", model]
        if session_id:
            cmd += ["--resume", session_id]
        cmd += ["--allowedTools", "WebSearch", "WebFetch", "--", prompt]
        return cmd

    def parse_output(self, stdout, stderr, exit_code):
        try:
            data = json.loads(stdout)
            if not isinstance(data, dict):
                return ParseResult(text=stdout or stderr)
            return ParseResult(
                text=data.get("result", stdout), session_id=data.get("session_id")
            )
        except (json.JSONDecodeError, TypeError):
            return ParseResult(text=stdout or stderr)

    def detect_rate_limit(self, output, exit_code, now=None):
        if exit_code == 0 or not self._limit_re.search(output):
            return None
        m = self._epoch_re.search(output)
        if m:
            return datetime.fromtimestamp(int(m.group(1)), tz=timezone.utc)
        return _default_resume_at(now)


class AntigravityProvider:
    # Google Antigravity CLI(`agy`) — 구글 OAuth 로그인 지원(시스템 키체인).
    name = "antigravity"
    _limit_re = re.compile(r"\b429\b|RESOURCE_EXHAUSTED|quota|rate.?limit", re.I)

    def build_command(self, prompt, session_id=None, model=None):
        cmd = ["agy", "-p", prompt]
        if model:
            cmd += ["--model", model]
        if session_id:
            cmd += ["-c"]  # 최근 대화 이어가기
        return cmd

    def parse_output(self, stdout, stderr, exit_code):
        return ParseResult(text=stdout or stderr, session_id="latest")

    def detect_rate_limit(self, output, exit_code, now=None):
        if exit_code == 0 or not self._limit_re.search(output):
            return None
        return _default_resume_at(now)


class GrokProvider:
    # grok은 세션 ID 기반 재개를 지원한다:
    #   --session-id <uuid>  새 대화에 우리가 정한 UUID를 부여
    #   --resume <uuid>      그 UUID로 정확히 그 대화만 재개
    # → 우리가 UUID를 발급·통제하므로 "가장 최근 대화 이어가기"(-c)의 오염
    #   (다른 대화가 끼면 엉뚱한 세션으로 이어짐)을 원천 차단한다.
    name = "grok"
    _limit_re = re.compile(r"rate.?limit|\b429\b|too many requests", re.I)

    def __init__(self):
        # 방금 발급/재개한 세션 ID를 parse_output으로 넘기기 위한 임시 보관.
        # grok은 provider 직렬화(같은 CLI 동시 실행 금지)로 자기 자신과 절대
        # 겹치지 않으므로, build_command→parse_output 사이 이 값은 안전하다.
        self._pending_session = None

    def build_command(self, prompt, session_id=None, model=None):
        cmd = ["grok"]
        if session_id:
            cmd += ["--resume", session_id]  # 그 세션을 정확히 재개
            self._pending_session = session_id
        else:
            new_id = str(uuid.uuid4())       # 새 대화 — 우리가 UUID 부여
            cmd += ["--session-id", new_id]
            self._pending_session = new_id
        if model:
            cmd += ["--model", model]
        cmd += ["-p", prompt]
        return cmd

    def parse_output(self, stdout, stderr, exit_code):
        return ParseResult(text=stdout or stderr, session_id=self._pending_session)

    def detect_rate_limit(self, output, exit_code, now=None):
        if exit_code == 0 or not self._limit_re.search(output):
            return None
        return _default_resume_at(now)


class HermesProvider:
    name = "hermes"

    def build_command(self, prompt, session_id=None, model=None):
        return ["hermes", "-z", prompt]

    def parse_output(self, stdout, stderr, exit_code):
        return ParseResult(text=stdout or stderr)

    def detect_rate_limit(self, output, exit_code, now=None):
        return None  # 로컬 실행 — 사용 제한 없음


PROVIDERS = {
    p.name: p
    for p in [ClaudeProvider(), AntigravityProvider(), GrokProvider(), HermesProvider()]
}

# 협의(Council) 모드 — 실제 CLI가 아니라 app.council 오케스트레이터가 처리하는
# 가상 프로바이더 값. PROVIDERS에는 넣지 않는다(3메서드 인터페이스 미구현).
COUNCIL = "council"

# 복잡한 작업 판별 키워드 — 매칭되거나 프롬프트가 길면 복잡한 작업으로 간주
_COMPLEX_KW = [
    "구현", "분석", "설계", "리팩터", "작성", "코드", "디버그", "테스트",
    "보고서", "계획", "조사", "검토", "수정", "버그", "개발", "번역", "요약",
    "implement", "analyz", "design", "refactor", "code", "debug", "test",
    "report", "plan", "research", "review", "fix", "bug", "write", "build",
]

_CLOUD_ROUTED = ("claude", "antigravity", "grok")
# 잔여 사용량을 알 수 없는(CodexBar 미연동) 프로바이더의 기본 순위값.
# 잔여를 아는 프로바이더가 이보다 많이 남으면 그쪽을 우선한다.
_UNKNOWN_REMAINING = 50


def is_complex(prompt):
    if len(prompt) >= 120:
        return True
    low = prompt.lower()
    return any(kw in low for kw in _COMPLEX_KW)


def rank_cloud(usage_state=None, enabled=None):
    """소진되지 않은 클라우드 프로바이더를 잔여 사용량 순으로 정렬해 반환.
    [(name, remaining), ...] — 잔여 많은 순, 동률이면 우선순위(claude>antigravity>grok) 순.
    enabled(활성 에이전트 목록)를 주면 그 안에서만 고른다.
    """
    usage_state = usage_state or {}
    names = [n for n in _CLOUD_ROUTED if enabled is None or n in enabled]
    ranked = []
    for name in names:
        st = usage_state.get(name) or {}
        if st.get("available") is False:  # 사용량 소진
            continue
        remaining = st.get("remaining")
        rank = _UNKNOWN_REMAINING if remaining is None else remaining
        ranked.append((rank, _CLOUD_ROUTED.index(name), name, remaining))
    ranked.sort(key=lambda x: (-x[0], x[1]))
    return [(name, remaining) for _, _, name, remaining in ranked]


def route_auto(prompt, usage_state=None, enabled=None):
    """자동 모드 라우팅. (provider, reason) 반환.

    - 단순 작업 → hermes (로컬·무제한, 클라우드 사용량 절약)
    - 복잡 작업 → 소진되지 않은 클라우드 에이전트 중 잔여 사용량이 가장 많은 곳
    - 모두 소진 → hermes 폴백

    usage_state: {provider: {"remaining": int|None, "available": bool|None}}
    (app.codexbar.normalize / 캐시가 제공)
    enabled: 활성 에이전트 목록 — 주면 그 안에서만 라우팅한다(None=전체).
    """
    from app import i18n
    en = i18n.get_lang() == "en"
    hermes_ok = enabled is None or "hermes" in enabled
    if not is_complex(prompt) and hermes_ok:
        return "hermes", (
            "Simple task → local Hermes to save cloud quota" if en
            else "단순 작업이라 로컬 Hermes로 처리해 클라우드 사용량을 아낍니다")
    ranked = rank_cloud(usage_state, enabled)
    if not ranked:
        if hermes_ok:
            return "hermes", (
                "All cloud agents exhausted → Hermes" if en
                else "클라우드 에이전트가 모두 소진되어 Hermes로 처리합니다")
        # 활성 클라우드가 모두 소진됐고 hermes도 비활성 → 활성 첫 에이전트로 시도
        fallback = enabled[0] if enabled else "hermes"
        return fallback, (
            f"All enabled agents exhausted → trying {fallback}" if en
            else f"모든 활성 에이전트가 소진되어 {fallback}로 시도합니다")
    best, remaining = ranked[0]
    if not is_complex(prompt):
        reason = (f"Hermes disabled → {best}" if en
                  else f"Hermes 비활성 → {best}로 처리합니다")
    elif remaining is None:
        reason = (f"Complex task → {best} (no usage data, picked by priority)" if en
                  else f"복잡한 작업 → {best} (사용량 정보 없음, 우선순위로 선택)")
    else:
        reason = (f"Complex task → {best}, most quota left ({remaining}% left)" if en
                  else f"복잡한 작업 → 잔여 사용량이 가장 많은 {best} ({remaining}% 남음)")
    return best, reason
