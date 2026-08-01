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
        # 사용자 전역 훅(예: 세션 종료 시 위키 캡처)이 돌면 JSON result가
        # 실제 답 대신 훅 응답으로 덮인다(2026-07-25 실측) → 훅 전체 비활성.
        #
        # --permission-mode 가 없으면 Write/Edit/Bash 가 전부 auto-deny 되어,
        # 파일을 하나도 못 고쳐 놓고 exit 0 + subtype:"success" 로 끝난다
        # (2026-08-01 실측: permission_denials=[Bash, Write],
        #  result="...needs your permission approval"). codex 는
        # --dangerously-bypass-approvals-and-sandbox 로 도는데 claude 만 사실상
        # 읽기 전용이라, route_auto 가 그날 잔량으로 고르면 같은 작업이 성공하기도
        # 조용히 실패하기도 했다.
        #
        # acceptEdits 는 Read/Edit/Write 만 자동 승인하고 Bash 는 여전히 거부한다
        # (2026-08-01 실측: permission_denials=[Bash, Bash] — 파일은 고쳐 놓고
        #  검증 명령을 못 돌려 "승인이 필요하다"로 끝났다). 테스트 실행·빌드가
        # 필요한 작업이 절반만 되므로 Bash 를 사전 허용에 함께 넣는다.
        # bypassPermissions 도 통하지만 모든 도구를 여는 것이라 쓰지 않는다.
        cmd = ["claude", "-p", "--output-format", "json",
               "--permission-mode", "acceptEdits",
               "--settings", '{"disableAllHooks": true}']
        if model:
            cmd += ["--model", model]
        if session_id:
            cmd += ["--resume", session_id]
        cmd += ["--allowedTools", "WebSearch", "WebFetch", "Bash", "--", prompt]
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
    # 세션 재개 미지원 CLI — worker가 CONTINUE_PROMPT 대신 원 프롬프트를 재전송해야 한다.
    supports_resume = False
    _limit_re = re.compile(r"\b429\b|RESOURCE_EXHAUSTED|quota|rate.?limit", re.I)

    def build_command(self, prompt, session_id=None, model=None):
        # agy는 헤드리스(-p)로 세션 ID를 얻거나 지정할 방법이 없다(JSON 출력·
        # 자가 발급 옵션 부재, -p 출력에 conversation ID 미포함). `-c`(전역 최신
        # 대화 이어가기)는 다른 대화가 끼면 오염되므로 이어가기를 지원하지 않는다
        # → 항상 단발 실행. session_id를 받아도 무시한다.
        cmd = ["agy", "-p", prompt]
        if model:
            cmd += ["--model", model]
        return cmd

    def parse_output(self, stdout, stderr, exit_code):
        # 세션 ID를 남기지 않는다 → 이어가기 대상으로 표시되지 않는다.
        return ParseResult(text=stdout or stderr, session_id=None)

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


class CodexProvider:
    # OpenAI Codex CLI (`codex exec`) — ChatGPT/Codex 구독 로그인 세션 사용.
    # 헤드리스: `codex exec --json` → JSONL 이벤트 스트림.
    # 재개: `codex exec resume <session_id>`.
    name = "codex"
    _limit_re = re.compile(
        r"rate.?limit|\b429\b|usage limit|quota|too many requests|"
        r"you've hit your limit|limit reached",
        re.I,
    )

    def build_command(self, prompt, session_id=None, model=None):
        # 무인 실행: 승인 프롬프트·샌드박스 대기로 행(hang) 나지 않게 한다.
        # 작업 위치(cwd)는 worker가 잡 workdir로 잡는다.
        if session_id:
            cmd = ["codex", "exec", "resume", session_id]
        else:
            cmd = ["codex", "exec"]
        cmd += [
            "--json",
            "--skip-git-repo-check",
            "--dangerously-bypass-approvals-and-sandbox",
        ]
        if model:
            cmd += ["--model", model]
        cmd.append(prompt)
        return cmd

    def parse_output(self, stdout, stderr, exit_code):
        session_id = None
        messages = []
        for line in (stdout or "").splitlines():
            line = line.strip()
            if not line or not line.startswith("{"):
                continue
            try:
                ev = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(ev, dict):
                continue
            if ev.get("type") == "thread.started" and ev.get("thread_id"):
                session_id = ev["thread_id"]
            item = ev.get("item") if ev.get("type") == "item.completed" else None
            if isinstance(item, dict) and item.get("type") == "agent_message":
                text = item.get("text")
                if text:
                    messages.append(text)
        if messages:
            return ParseResult(text=messages[-1], session_id=session_id)
        # --json 이 아니면 최종 답만 stdout에 온다
        return ParseResult(text=stdout or stderr, session_id=session_id)

    def detect_rate_limit(self, output, exit_code, now=None):
        if exit_code == 0 or not self._limit_re.search(output):
            return None
        return _default_resume_at(now)


class GeminiProvider:
    # Google Gemini CLI (`gemini`) — OAuth 또는 Vertex AI(ADC). Antigravity와 별개.
    # 헤드리스: `gemini -p … -y -o json --skip-trust`.
    # 세션 재개 플래그는 latest/index 전용이라 UUID 이어가기는 지원하지 않는다.
    name = "gemini"
    # 세션 재개 미지원 CLI — worker가 CONTINUE_PROMPT 대신 원 프롬프트를 재전송해야 한다.
    supports_resume = False
    _limit_re = re.compile(
        r"\b429\b|RESOURCE_EXHAUSTED|quota|rate.?limit|usage limit", re.I
    )

    def build_command(self, prompt, session_id=None, model=None):
        # session_id는 CLI가 UUID 재개를 지원하지 않아 무시(오염 방지).
        # --skip-trust: 헤드리스에서 trusted-folder 프롬프트를 피한다.
        cmd = ["gemini", "-p", prompt, "-y", "-o", "json", "--skip-trust"]
        if model:
            cmd += ["-m", model]
        return cmd

    def parse_output(self, stdout, stderr, exit_code):
        try:
            data = json.loads(stdout)
            if not isinstance(data, dict):
                return ParseResult(text=stdout or stderr, session_id=None)
            text = data.get("response")
            if text is None:
                text = stdout or stderr
            # session_id가 있어도 이어가기 UI는 main에서 막아 둔다.
            return ParseResult(text=text, session_id=data.get("session_id"))
        except (json.JSONDecodeError, TypeError):
            return ParseResult(text=stdout or stderr, session_id=None)

    def detect_rate_limit(self, output, exit_code, now=None):
        if exit_code == 0 or not self._limit_re.search(output):
            return None
        return _default_resume_at(now)


class OpenClawProvider:
    # OpenClaw — 로컬 Gateway/임베디드 에이전트. 모델은 구독 CLI·OAuth 프로필
    # 또는 사용자 설정 프로바이더를 쓴다. API 키 환경변수에 의존하지 않는다.
    # 헤드리스: `openclaw agent --session-id … --message … --json`.
    name = "openclaw"
    _limit_re = re.compile(
        r"rate.?limit|\b429\b|quota|usage limit|too many requests|"
        r"RESOURCE_EXHAUSTED",
        re.I,
    )

    def __init__(self):
        self._pending_session = None

    def build_command(self, prompt, session_id=None, model=None):
        sid = session_id or str(uuid.uuid4())
        self._pending_session = sid
        cmd = [
            "openclaw", "agent",
            "--session-id", sid,
            "--message", prompt,
            "--json",
        ]
        if model:
            cmd += ["--model", model]
        return cmd

    def parse_output(self, stdout, stderr, exit_code):
        try:
            data = json.loads(stdout)
            if not isinstance(data, dict):
                return ParseResult(
                    text=stdout or stderr, session_id=self._pending_session
                )
            result = data.get("result")
            if not isinstance(result, dict):
                result = data
            texts = []
            payloads = result.get("payloads") if isinstance(result, dict) else None
            if isinstance(payloads, list):
                for p in payloads:
                    if isinstance(p, dict) and p.get("text"):
                        texts.append(str(p["text"]))
            text = "\n\n".join(texts) if texts else (stdout or stderr)
            sid = (
                result.get("sessionId")
                if isinstance(result, dict) else None
            ) or data.get("sessionId") or self._pending_session
            return ParseResult(text=text, session_id=sid)
        except (json.JSONDecodeError, TypeError):
            return ParseResult(
                text=stdout or stderr, session_id=self._pending_session
            )

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
    for p in [
        ClaudeProvider(),
        CodexProvider(),
        AntigravityProvider(),
        GeminiProvider(),
        GrokProvider(),
        OpenClawProvider(),
        HermesProvider(),
    ]
}

# 협의(Council) 모드 — 실제 CLI가 아니라 app.council 오케스트레이터가 처리하는
# 가상 프로바이더 값. PROVIDERS에는 넣지 않는다(3메서드 인터페이스 미구현).
COUNCIL = "council"

# 미디어 생성 — app.media가 처리하는 가상 프로바이더 값(비전 보드 태스크 전용).
# 잡의 model 컬럼에 미디어 종류(image|video|audio)를 실어 전달한다.
MEDIA = "media"

# 복잡한 작업 판별 키워드 — 매칭되거나 프롬프트가 길면 복잡한 작업으로 간주
_COMPLEX_KW = [
    "구현", "분석", "설계", "리팩터", "작성", "코드", "디버그", "테스트",
    "보고서", "계획", "조사", "검토", "수정", "버그", "개발", "번역", "요약",
    "implement", "analyz", "design", "refactor", "code", "debug", "test",
    "report", "plan", "research", "review", "fix", "bug", "write", "build",
]

# 자동 라우팅에 쓰는 에이전트 프로필.
#
# ``max_difficulty``는 해당 CLI에 맡길 수 있는 최대 작업 난이도다. 현재 등록된
# 구독 CLI는 모두 complex 작업을 처리할 수 있다. Hermes도 cheap/balanced/heavy
# 하위 프로필(heavy는 고사양 추론 모델)이 생기면서 complex 작업까지 처리할 수
# 있게 되어 simple 전용 제한을 해제한다. ``affinities``는 *동일한 잔여 사용량*
# 일 때만 쓰는 타이브레이커다. 따라서 특정 에이전트의 강점이 사용량이 더 많이
# 남은 다른 에이전트를 제치지 않는다.
#
# 새 provider를 추가할 때 이 표에도 항목을 넣으면 자동 추천이 해당 에이전트의
# 난이도와 작업 성격을 함께 고려한다.
AGENT_PROFILES = {
    "claude": {"max_difficulty": "complex", "affinities": {"code", "analysis", "writing"}},
    "codex": {"max_difficulty": "complex", "affinities": {"code", "automation", "debug"}},
    "antigravity": {"max_difficulty": "complex", "affinities": {"document", "multimodal", "analysis"}},
    "gemini": {"max_difficulty": "complex", "affinities": {"document", "multimodal", "analysis"}},
    "grok": {"max_difficulty": "complex", "affinities": {"research", "current", "analysis"}},
    "openclaw": {"max_difficulty": "complex", "affinities": {"automation", "analysis", "writing"}},
    "hermes": {"max_difficulty": "complex", "affinities": {"local", "private"}},
}
_DEFAULT_CLOUD_PROFILE = {"max_difficulty": "complex", "affinities": set()}

# 클라우드(구독) 에이전트 — 잔여 사용량 기반 자동 라우팅 대상.
# 등록된 모든 비로컬 provider를 자동 후보로 삼는다. PROVIDERS의 등록 순서가
# 사용량·작업 성격까지 같은 경우의 최종 우선순위다. 따라서 새 클라우드
# 에이전트를 등록하면 별도 라우팅 목록을 고치지 않아도 자동 추천에 포함된다.
_CLOUD_ROUTED = tuple(name for name in PROVIDERS if name != "hermes")
# 잔여 사용량을 알 수 없는(CodexBar 미연동) 프로바이더의 기본 순위값.
# 잔여를 아는 프로바이더가 이보다 많이 남으면 그쪽을 우선한다.
_UNKNOWN_REMAINING = 50

_DIFFICULTY_RANK = {"simple": 0, "complex": 1}

_TASK_KIND_KW = {
    "code": ("구현", "리팩터", "코드", "디버그", "버그", "개발", "implement",
             "refactor", "code", "debug", "bug", "build"),
    "automation": ("자동화", "스크립트", "워크플로", "automation", "script", "workflow"),
    "research": ("조사", "검색", "최신", "뉴스", "research", "search", "latest", "news"),
    "current": ("오늘", "현재", "실시간", "today", "current", "real-time"),
    "document": ("문서", "보고서", "번역", "요약", "document", "report", "translate", "summar"),
    "multimodal": ("이미지", "pdf", "영상", "image", "video", "pdf"),
    "analysis": ("분석", "검토", "설계", "analysis", "review", "design", "plan"),
    "writing": ("작성", "글", "write", "draft"),
}


def is_complex(prompt):
    if len(prompt) >= 120:
        return True
    low = prompt.lower()
    return any(kw in low for kw in _COMPLEX_KW)


def task_difficulty(prompt):
    """프롬프트를 자동 라우팅용 simple/complex 난이도로 분류한다."""
    return "complex" if is_complex(prompt) else "simple"


def task_kinds(prompt):
    """프롬프트에서 감지한 작업 성격 집합(동률 정렬용)을 반환한다."""
    low = prompt.lower()
    return {kind for kind, keywords in _TASK_KIND_KW.items()
            if any(keyword in low for keyword in keywords)}


def rank_cloud(usage_state=None, enabled=None):
    """소진되지 않은 클라우드 프로바이더를 잔여 사용량 순으로 정렬해 반환.
    [(name, remaining), ...] — 잔여 많은 순, 동률이면 _CLOUD_ROUTED 우선순위 순.
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
        # 실측값이 있는 후보를 항상 먼저 둔다. OpenClaw처럼 자체 사용량을
        # 직접 측정할 수 없는 Gateway가 50%라는 가정값으로 실제 10% 남은
        # 에이전트를 앞지르는 일을 막는다.
        known = remaining is not None
        rank = _UNKNOWN_REMAINING if not known else remaining
        ranked.append((known, rank, _CLOUD_ROUTED.index(name), name, remaining))
    ranked.sort(key=lambda x: (-x[0], -x[1], x[2]))
    return [(name, remaining) for _, _, _, name, remaining in ranked]


def rank_auto_agents(prompt, usage_state=None, enabled=None):
    """난이도에 맞는 모든 활성 에이전트를 사용량순으로 정렬한다.

    실제 잔여 사용량이 최우선이며, 동률일 때만 프롬프트의 작업 성격과 provider
    프로필을 적용한다. Hermes는 클라우드 사용량 랭킹(``rank_cloud``)에는 안
    잡히지만(무제한 로컬 실행) complex 작업에서도 정식 후보로 포함해, 클라우드
    잔여 사용량이 낮을 때 우선 선택될 수 있게 한다. 반환 형식은 ``rank_cloud``
    와 동일하다.
    """
    kinds = task_kinds(prompt)
    candidates = list(rank_cloud(usage_state, enabled))
    if (enabled is None or "hermes" in enabled) and not any(n == "hermes" for n, _ in candidates):
        candidates.append(("hermes", None))
    ranked = []
    for name, remaining in candidates:
        # 아직 세부 프로필이 없는 새 provider도 기본 complex 후보로 포함한다.
        profile = AGENT_PROFILES.get(name, _DEFAULT_CLOUD_PROFILE)
        if _DIFFICULTY_RANK[profile["max_difficulty"]] < _DIFFICULTY_RANK[task_difficulty(prompt)]:
            continue
        affinity = len(kinds & profile["affinities"])
        idx = _CLOUD_ROUTED.index(name) if name in _CLOUD_ROUTED else len(_CLOUD_ROUTED)
        ranked.append((remaining is not None, remaining if remaining is not None else _UNKNOWN_REMAINING,
                       affinity, idx, name, remaining))
    ranked.sort(key=lambda x: (-x[0], -x[1], -x[2], x[3]))
    return [(name, remaining) for _, _, _, _, name, remaining in ranked]


def route_auto(prompt, usage_state=None, enabled=None):
    """자동 모드 라우팅. (provider, reason) 반환.

    - 단순 작업 → hermes (로컬·무제한, 클라우드 사용량 절약)
    - 복잡 작업 → 클라우드 에이전트와 hermes(heavy 프로필) 중 잔여 사용량이
      가장 많은 곳. 클라우드 잔여 사용량이 낮으면 hermes가 선택될 수 있다.
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
    ranked = rank_auto_agents(prompt, usage_state, enabled)
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
    elif best == "hermes":
        reason = (
            "Complex task, cloud quota low → Hermes (heavy profile)" if en
            else "복잡한 작업이지만 클라우드 잔여 사용량이 낮아 Hermes(heavy 프로필)로 처리합니다")
    elif remaining is None:
        reason = (f"Complex task → {best} (no usage data, picked by priority)" if en
                  else f"복잡한 작업 → {best} (사용량 정보 없음, 우선순위로 선택)")
    else:
        reason = (f"Complex task → {best}, most quota left ({remaining}% left)" if en
                  else f"복잡한 작업 → 잔여 사용량이 가장 많은 {best} ({remaining}% 남음)")
    return best, reason
