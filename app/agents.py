"""커스텀 에이전트 — 사용자가 만든 "역할"과 그들 사이의 호출(@멘션).

provider(벤더 CLI)와는 다른 층이다. 에이전트는 **무엇을 하는 사람인가**(persona)를
고정하고, 어느 CLI 로 실행할지는 provider 로 고르거나 "auto" 로 두어 잔여 쿼터
라우팅(providers.route_auto)에 맡긴다. "auto" 가 이 앱만의 이점이다 — 특정 CLI 가
사용량을 소진해도 그 역할은 다른 CLI 위에서 계속 살아 있다.

## 설계

- **페르소나는 프롬프트 프리픽스로 주입한다(벤더 중립).** `claude --agents` 같은
  CLI 전용 기능에 기대면 라우팅이 다른 CLI 로 튀는 순간 역할이 통째로 사라진다.
  worker.run_job 이 워크스페이스 지침(instructions.build_context)을 붙이는 자리
  바로 앞에 얹는다 — 순서는 `페르소나 → 프로젝트 지침 → 실제 프롬프트`.
- **컨텍스트는 세션이 아니라 텍스트로 넘긴다.** council.py·orchestrator.py 가
  이미 이 원칙으로 서 있다(벤더가 섞이면 세션 공유가 성립하지 않는다).

## read_only 의 한계 (중요)

`read_only=1` 은 claude 에서만 **강제**된다 — `--permission-mode plan` 으로
띄우고 쓰기 도구를 사전 허용에서 뺀다(providers.ClaudeProvider.build_command).
codex/gemini/grok 등 다른 CLI 는 실행 단위로 권한을 좁힐 방법이 없어
페르소나 텍스트의 금지 문구로만 방어한다 — 즉 **강제가 아니라 지시다.**
파일을 정말로 못 건드리게 해야 하는 역할이라면 provider 를 claude 로 고정하라.
(무엇이 바뀌었는지는 어느 CLI 든 gitcheckpoint 가 diff 로 남기고 되돌릴 수 있다.)
"""
from __future__ import annotations

import re

from app import config, db, settings
from app.providers import PROVIDERS, route_auto

# @멘션 — 슬러그는 소문자·숫자·하이픈. 메일주소(foo@bar)나 파이썬 데코레이터가
# 걸리지 않도록 앞이 단어문자가 아닐 때만 인식한다.
MENTION_RE = re.compile(r"(?<![\w@])@([a-z0-9][a-z0-9-]{0,59})\b")

PERSONA_PREFIX = """당신은 이 작업을 맡은 에이전트 "{name}"(@{slug})입니다.

## 당신의 역할

{persona}
"""

READ_ONLY_NOTE = """
## 제약 (반드시 지킬 것)

당신은 읽기 전용 에이전트입니다. 파일을 만들거나 고치거나 지우지 말고,
상태를 바꾸는 명령을 실행하지 마세요. 조사·분석·검토 결과만 글로 제출하세요.
"""

MENTION_NOTE = """
## 다른 에이전트 부르기

다른 역할이 이어받아야 하면 답변 안에서 `@슬러그` 로 부르세요. 한 답변에서
처음 부른 한 명만 실행되고, 이어받을 사람이 없으면 부르지 마세요.

이 채널에서 부를 수 있는 에이전트: {roster}
"""

HANDOFF_SECTION = """
## 다른 에이전트에게 넘기기 (선택)

당신이 직접 하기 어렵거나 다른 역할이 맡아야 하는 후속 작업이 있으면, 결과 끝에
아래 형식의 JSON 코드블록을 **하나만** 덧붙이세요. 필요 없으면 붙이지 마세요.

```json
{{"handoff": {{"to": "슬러그", "title": "후속 태스크 제목", "description": "구체적 작업 지시", "reason": "왜 넘기는지"}}}}
```

부를 수 있는 에이전트: {roster}
"""

THREAD_PROMPT = """{mention_note}
## 지금까지의 대화 (#{channel})

{history}

## 당신에게 온 요청

{caller}님이 당신을 불렀습니다:

{body}

## 지시

위 요청에 답하세요. 대화 맥락을 참고하되, 당신의 역할 안에서 답하세요.
"""


class AgentError(ValueError):
    """에이전트를 찾을 수 없거나 지금 실행할 수 없음."""


# --- 생성 -------------------------------------------------------------------

def slugify(text):
    """이름 → @멘션용 슬러그. 한글 등 ASCII 밖 문자는 남지 않으므로,
    남는 게 없으면 "agent" 로 떨어뜨리고 호출자가 유일성을 붙인다."""
    base = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (base or "agent")[:60]


def unique_slug(conn, text):
    base = slugify(text)
    candidate, i = base, 2
    while db.get_agent_by_slug(conn, candidate, status=None) is not None:
        candidate = f"{base}-{i}"
        i += 1
    return candidate


PRESETS = [
    {
        "slug": "researcher", "name": "Researcher", "provider": "auto",
        "read_only": 1,
        "persona": "당신은 조사 담당입니다. 질문에 답하기 위해 필요한 근거를 모으고, "
                   "출처와 확신도를 함께 밝힙니다. 추측과 확인된 사실을 반드시 구분하고, "
                   "모르는 것은 모른다고 말합니다. 결론을 먼저, 근거를 뒤에 씁니다.",
    },
    {
        "slug": "builder", "name": "Builder", "provider": "claude",
        "read_only": 0,
        "persona": "당신은 구현 담당입니다. 작업 위치의 기존 코드 스타일과 구조를 먼저 읽고, "
                   "그 관습에 맞춰 실제로 코드를 고칩니다. 설명보다 동작하는 변경을 우선하고, "
                   "무엇을 왜 바꿨는지 마지막에 짧게 요약합니다.",
    },
    {
        "slug": "reviewer", "name": "Reviewer", "provider": "auto",
        "read_only": 1,
        "persona": "당신은 검토 담당입니다. 결과물의 오류·누락·리스크를 찾아 지적하고, "
                   "고칠 방법을 구체적으로 제시합니다. 잘된 점은 짧게, 문제는 명확하게. "
                   "직접 고치지 말고 무엇을 어떻게 고쳐야 하는지만 쓰세요.",
    },
]


def seed_presets(conn):
    """프리셋 에이전트를 첫 실행에 한 번만 넣는다.

    settings.json 의 플래그로 "이미 한 번 넣었다"를 기억한다 — 사용자가 지운
    프리셋이 서버를 재시작할 때마다 되살아나면 안 된다. 같은 슬러그가 이미
    있으면(사용자가 직접 만든 경우) 건드리지 않는다.
    """
    if settings.load().get("agents_seeded"):
        return []
    created = []
    for preset in PRESETS:
        if db.get_agent_by_slug(conn, preset["slug"], status=None) is not None:
            continue
        created.append(db.create_agent(
            conn, preset["slug"], preset["name"], persona=preset["persona"],
            provider=preset["provider"], read_only=preset["read_only"]))
    settings.mark_agents_seeded()
    return created


# --- 실행 -------------------------------------------------------------------

def persona_prefix(agent):
    """잡 프롬프트 앞에 붙일 역할 블록. 페르소나가 비어 있어도 정체성은 준다."""
    if agent is None:
        return ""
    persona = (agent["persona"] or "").strip() or f"'{agent['name']}' 역할을 맡고 있습니다."
    prefix = PERSONA_PREFIX.format(name=agent["name"], slug=agent["slug"],
                                   persona=persona)
    if agent["read_only"]:
        prefix += READ_ONLY_NOTE
    return prefix + "\n---\n\n"


def resolve_provider(conn, agent, prompt, usage_state=None, enabled=None):
    """에이전트를 실제로 돌릴 provider 를 정한다. (provider, 라우팅 사유).

    provider == "auto" 면 잔여 쿼터 라우팅에 맡긴다 — 역할은 고정되고 실행
    CLI 만 그때그때 여유 있는 곳으로 간다.
    """
    enabled = enabled if enabled is not None else settings.enabled_providers()
    name = agent["provider"] or "auto"
    if name == "auto":
        return route_auto(prompt, usage_state=usage_state, enabled=enabled)
    if name not in PROVIDERS:
        raise AgentError(f"@{agent['slug']}: 알 수 없는 에이전트 {name!r}")
    if name not in enabled:
        raise AgentError(
            f"@{agent['slug']} 가 쓰는 {name} 이(가) 비활성 상태입니다. "
            "/setup 에서 활성화하세요")
    return name, f"@{agent['slug']}"


def model_for(agent, provider):
    """에이전트가 지정한 모델 — 실제로 그 provider 의 모델일 때만 쓴다."""
    from app import models
    model = (agent["model"] or "") if agent is not None else ""
    return model if models.is_valid_model(provider, model) else ""


# --- @멘션 ------------------------------------------------------------------

def parse_mentions(text):
    """본문에서 멘션 슬러그를 등장 순서대로. 중복은 제거한다."""
    seen, out = set(), []
    for slug in MENTION_RE.findall(text or ""):
        if slug not in seen:
            seen.add(slug)
            out.append(slug)
    return out


def first_callable_mention(conn, channel_id, text, exclude_agent_id=None):
    """본문의 첫 멘션 중 이 채널에서 실제로 부를 수 있는 에이전트 하나.

    한 답변에서 **하나만** 처리한다 — 여러 명을 동시에 깨우면 연쇄가 지수로
    퍼져 쿼터가 순식간에 녹는다. 없으면 None.
    """
    for slug in parse_mentions(text):
        agent = db.get_agent_by_slug(conn, slug)
        if agent is None:
            continue
        if exclude_agent_id is not None and agent["id"] == exclude_agent_id:
            continue  # 자기 자신 호출은 무시(A→A 금지)
        if not db.is_channel_member(conn, channel_id, agent["id"]):
            continue
        return agent
    return None


def mention_for_channel(conn, channel_id, text):
    """사람이 쓴 본문의 첫 멘션 → 이 채널에서 실행할 에이전트. 없으면 None.

    슬러그가 아무 에이전트에도 해당하지 않으면 그냥 텍스트로 본다(None).
    실재하는 에이전트인데 이 채널 멤버가 아니면 AgentError — 사용자는 오타가
    아니라 "왜 대답을 안 하지?"로 받아들이므로 조용히 무시하면 안 된다.
    """
    for slug in parse_mentions(text):
        agent = db.get_agent_by_slug(conn, slug)
        if agent is None:
            continue
        if not db.is_channel_member(conn, channel_id, agent["id"]):
            raise AgentError(
                f"@{slug} 는 이 채널의 멤버가 아닙니다. 채널에 먼저 추가하세요.")
        return agent
    return None


def _clip(text):
    limit = config.AGENT_THREAD_CLIP_CHARS
    text = text or ""
    return text if len(text) <= limit else text[:limit] + "\n…(생략)"


def _speaker(conn, message):
    if message["author_agent_id"]:
        agent = db.get_agent(conn, message["author_agent_id"])
        if agent is not None:
            return f"@{agent['slug']}"
    return "사용자" if message["role"] == "user" else (message["author"] or "agent")


def build_thread_prompt(conn, channel, thread, trigger, roster=()):
    """다음 에이전트에게 보낼 프롬프트 — 쓰레드 히스토리 + 자신을 부른 발화.

    세션이 아니라 텍스트로 맥락을 넘긴다(모듈 상단 설계 참고).
    """
    lines = []
    for msg in thread:
        if msg["id"] == trigger["id"] or not (msg["body"] or "").strip():
            continue
        lines.append(f"### {_speaker(conn, msg)}\n\n{_clip(msg['body'])}")
    history = "\n\n".join(lines) or "(이전 대화 없음)"
    # 부를 상대를 실제로 알려 주지 않으면 @멘션 규약은 무용지물이다.
    mention_note = MENTION_NOTE.format(
        roster=", ".join(f"@{a['slug']}({a['name']})" for a in roster)
    ) if roster else ""
    return THREAD_PROMPT.format(
        mention_note=mention_note, channel=channel["title"], history=history,
        caller=_speaker(conn, trigger), body=_clip(trigger["body"]))


# --- 연쇄 한도 --------------------------------------------------------------

def _pingpong_count(conn, thread, a_id, b_id):
    """쓰레드에서 두 에이전트가 번갈아 말한 횟수(A→B 전환 수)."""
    speakers = [m["author_agent_id"] for m in thread if m["author_agent_id"]]
    count = 0
    for prev, cur in zip(speakers, speakers[1:]):
        if {prev, cur} == {a_id, b_id}:
            count += 1
    return count


def chain_blocker(conn, channel, thread, source, target):
    """연쇄를 멈춰야 할 이유. 계속해도 되면 None.

    반환값은 그대로 사람이 읽는 system 메시지가 된다.
    """
    if not channel["agent_chat"]:
        return None  # 채널이 opt-in 하지 않음 — 조용히 무시(정지 안내도 남기지 않는다)
    hop = (source["hop_depth"] or 0) + 1
    if hop > config.AGENT_HOP_MAX:
        return (f"에이전트 연쇄가 최대 깊이({config.AGENT_HOP_MAX})에 도달해 "
                f"@{target['slug']} 호출을 멈췄습니다. 이어가려면 직접 말을 거세요.")
    agent_turns = sum(1 for m in thread if m["author_agent_id"])
    if agent_turns >= config.AGENT_CHAIN_MAX:
        return (f"이 대화에서 에이전트가 이미 {agent_turns}번 응답해 "
                f"(상한 {config.AGENT_CHAIN_MAX}) 연쇄를 멈췄습니다.")
    if source["author_agent_id"] and _pingpong_count(
            conn, thread, source["author_agent_id"],
            target["id"]) >= config.AGENT_PINGPONG_MAX * 2:
        return (f"두 에이전트가 주고받기만 반복해 연쇄를 멈췄습니다 "
                f"(왕복 상한 {config.AGENT_PINGPONG_MAX}회).")
    return None


def dispatch_mentions(conn, message_id):
    """완료된 에이전트 응답에서 다음 에이전트를 깨운다. 잡 하나를 만들 뿐이고
    실행은 worker_loop 이 가져간다.

    worker._sync_message 에서 호출된다 — 잡의 모든 종료 경로가 지나는 유일한
    지점이라 여기가 안전하다. 조건이 하나라도 안 맞으면 조용히 no-op 이다.
    """
    message = db.get_message(conn, message_id)
    if message is None or message["role"] != "agent" or not message["channel_id"]:
        return None
    channel = db.get_channel(conn, message["channel_id"])
    if channel is None or not channel["agent_chat"]:
        return None  # 채널별 opt-in — 꺼져 있으면 멘션은 그냥 텍스트다
    target = first_callable_mention(conn, channel["id"], message["body"],
                                    exclude_agent_id=message["author_agent_id"])
    if target is None:
        return None

    root_id = message["root_id"] or message["id"]
    thread = db.list_thread(conn, root_id)
    blocker = chain_blocker(conn, channel, thread, message, target)
    if blocker:
        db.create_message(conn, channel["id"], role="system", body=blocker,
                          author="system", parent_id=root_id, status="done")
        return None
    try:
        return spawn(conn, channel, root_id, message, target,
                     hop_depth=(message["hop_depth"] or 0) + 1)
    except AgentError as e:
        db.create_message(conn, channel["id"], role="system", body=str(e),
                          author="system", parent_id=root_id, status="done")
        return None


def spawn(conn, channel, root_id, trigger, agent, hop_depth=0):
    """에이전트를 채널 쓰레드에서 실행한다 — 메시지 자리 + 잡을 만들고
    (message_id, job_id) 를 돌려준다. 사람이 부를 때와 에이전트가 부를 때가
    같은 경로를 쓴다.
    """
    thread = db.list_thread(conn, root_id)
    # 채널이 에이전트 간 대화를 허용하지 않으면 부를 상대를 알려 주지 않는다 —
    # 어차피 무시될 멘션을 하게 만들면 답변만 지저분해진다.
    roster = ([a for a in db.list_channel_members(conn, channel["id"])
               if a["id"] != agent["id"]] if channel["agent_chat"] else [])
    prompt = build_thread_prompt(conn, channel, thread, trigger, roster=roster)
    from app import council
    provider, reason = resolve_provider(
        conn, agent, trigger["body"], usage_state=council.usage_snapshot())
    model = model_for(agent, provider)

    workdir = agent["workdir"] or channel["workdir"]
    from app import workspace
    if not workdir or not workspace.valid_path(workdir):
        workdir = None

    # 세션은 같은 에이전트의 것만 잇는다 — 같은 CLI 를 쓰는 다른 에이전트의
    # 세션을 물려받으면 그쪽 맥락을 통째로 흡수한다(db.latest_thread_session).
    session_id, session_provider = db.latest_thread_session(
        conn, root_id, agent_id=agent["id"])
    if session_provider != provider:
        session_id = None

    message_id = db.create_message(
        conn, channel["id"], role="agent", body="", author=agent["slug"],
        parent_id=root_id, status="queued", provider=provider,
        model=model or None, author_agent_id=agent["id"], hop_depth=hop_depth)
    job_id = db.create_job(
        conn, prompt, provider, session_id=session_id, model=model or None,
        workdir=workdir, route_reason=reason, channel_id=channel["id"],
        message_id=message_id, agent_id=agent["id"])
    db.update_message(conn, message_id, job_id=job_id)
    return message_id, job_id
