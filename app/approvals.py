"""승인 게이트 — 되돌릴 수 없는 동작은 사람이 승인한 뒤에 실행한다.

Grok Bot 의 차별점이자 이 앱의 가장 큰 구멍이었다. 워크스페이스 파일 변경은
gitcheckpoint 가 diff 로 남기고 되돌릴 수 있지만, **메일 발송·시트 쓰기·결제·
외부 API 호출은 되돌릴 수 없다.**

## 왜 도구 단위 가로채기가 아닌가

Agent SDK 라면 PreToolUse 훅의 `defer` 로 도구 호출 하나하나를 잡아 세울 수
있다. 이 앱은 SDK 가 아니라 구독 CLI 를 서브프로세스로 띄우므로 그 방법이
없다. claude 는 훅으로 비슷한 것을 할 여지가 있으나(그마저 지금은 전역 훅이
결과를 덮는 문제로 `disableAllHooks: true` 다), codex·gemini·grok 에는 실행
단위로 도구를 가로챌 수단이 아예 없다.

그래서 **벤더 중립 경로**를 택한다: 에이전트에게 "되돌릴 수 없는 일은 하지 말고
제안만 하라"고 지시하고, 제안을 approvals 행으로 만든 뒤, 사람이 승인하면
**앱이 별도 잡으로** 실행한다.

## 무엇이 강제이고 무엇이 지시인가 (중요)

- `approval_policy="ask"` 자체는 **지시**다. 프롬프트로 전달될 뿐이라, 마음먹은
  모델이 그냥 실행해 버리는 것을 이 플래그만으로는 막지 못한다.
- 실제 강제는 **도구를 아예 주지 않는 것**에서 나온다:
  - `read_only=1` + provider=claude → plan 모드 + Bash 미허용이라 애초에
    실행할 수단이 없다. 이 조합이 유일하게 강제되는 승인 게이트다.
  - 워크스페이스 MCP 프로필에서 위험한 서버를 빼면 그 도구 자체가 없다.
- 따라서 UI 는 `ask` 를 켤 때 읽기 전용을 함께 권한다. 이 한계를 숨기지 않는다.

승인 후 실행 잡은 **게이트를 그 동작 하나에만 해제**한다 — 읽기 전용 에이전트도
승인된 실행에서는 쓰기가 열린다. 사람이 그 동작을 명시적으로 허락했기 때문이다.
"""
from __future__ import annotations

import json
import re

from app import config, db

# 제안 블록 — orchestrator 의 위임 블록과 같은 ```json 펜스 규약을 쓴다.
_FENCE_RE = re.compile(r"```json\s*(.*?)```", re.S)

PROPOSAL_SECTION = """
## 되돌릴 수 없는 동작은 실행하지 말 것 (승인 필요)

메일 발송, 외부 서비스에 쓰기, 결제, 파일·데이터 삭제처럼 **되돌릴 수 없거나
바깥에 영향을 주는 동작은 직접 실행하지 마세요.** 대신 결과물 끝에 아래 형식의
JSON 코드블록으로 제안하세요. 사람이 승인하면 그때 실행하게 됩니다.

```json
{{"approval": {{"title": "한 줄 요약", "detail": "무엇을 왜 하려는지", "action": "실행할 구체적 동작", "risk": "되돌릴 수 있는지, 무엇이 바뀌는지"}}}}
```

여러 건이면 블록을 여러 개 쓰세요(최대 {max_items}건). 조사·분석·읽기처럼
되돌릴 수 있는 일은 그냥 하면 됩니다 — 제안으로 만들지 마세요.
"""

EXECUTION_PROMPT = """사용자가 아래 동작을 **승인했습니다.** 이제 실제로 실행하세요.

## 승인된 동작: {title}

{detail}

## 실행할 것

{action}
{note}
## 지시

승인된 이 동작만 실행하세요. 범위를 넘는 다른 동작은 하지 말고, 실행 결과를
간결히 보고하세요. 실행 중 새로 승인이 필요한 일이 생기면 실행하지 말고
그 사실을 보고하세요.
"""

DECIDED_NOTE_SECTION = """
## 사용자가 덧붙인 지시

{note}
"""


def policy_of(agent):
    """에이전트의 승인 정책. 에이전트가 없으면 게이트 없음."""
    if agent is None:
        return "auto"
    try:
        return agent["approval_policy"] or "auto"
    except (KeyError, IndexError):   # 구 DB·테스트용 dict
        return "auto"


def gate_section(agent):
    """프롬프트에 붙일 승인 게이트 지시. 정책이 auto 면 빈 문자열."""
    if policy_of(agent) != "ask":
        return ""
    return PROPOSAL_SECTION.format(max_items=config.APPROVAL_MAX_PER_JOB)


# --- 제안 수확 --------------------------------------------------------------

def parse_proposals(text):
    """출력에서 승인 제안을 뽑는다. (본문, [proposal...]).

    본문에서 제안 블록을 **제거해서** 돌려준다 — 그 출력은 채널 메시지 본문이나
    하류 태스크 프롬프트로 그대로 흘러가므로, JSON 이 남으면 사람도 다음
    에이전트도 그것을 결과물로 읽는다.

    형식이 어긋난 블록은 조용히 건너뛴다 — 파싱 실패로 작업 결과 자체를 버리는
    것이 훨씬 나쁘다.
    """
    text = text or ""
    out, spans = [], []
    for block in _FENCE_RE.finditer(text):
        try:
            data = json.loads(block.group(1).strip())
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(data, dict) or not isinstance(data.get("approval"), dict):
            continue
        item = data["approval"]
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        out.append({
            "title": title[:200],
            "detail": str(item.get("detail") or "").strip(),
            "action": str(item.get("action") or "").strip(),
            "risk": str(item.get("risk") or "").strip(),
        })
        spans.append((block.start(), block.end()))
        if len(out) >= config.APPROVAL_MAX_PER_JOB:
            break
    if not out:
        return text, []
    cleaned = text
    for start, end in reversed(spans):      # 뒤에서부터 지워야 인덱스가 안 밀린다
        cleaned = cleaned[:start] + cleaned[end:]
    return cleaned.strip(), out


def harvest(conn, job_id):
    """끝난 잡의 출력에서 제안을 뽑아 승인 큐에 넣는다. 만든 approval id 목록.

    worker._run_tracked 에서 _sync_message '전에' 호출된다 — 정리된 출력이
    채널 메시지 본문으로 그대로 이어지게 하기 위함이다.
    """
    job = db.get_job(conn, job_id)
    if job is None or job["status"] != "done":
        return []
    agent = db.get_agent(conn, _opt(job, "agent_id"))
    if policy_of(agent) != "ask":
        return []
    cleaned, proposals = parse_proposals(job["output"])
    if not proposals:
        return []
    db.update_job(conn, job_id, output=cleaned)

    task = db.task_by_job(conn, job_id)
    created = []
    for p in proposals:
        created.append(db.create_approval(
            conn, p["title"], detail=p["detail"], action=p["action"],
            risk=p["risk"], job_id=job_id, agent_id=agent["id"],
            channel_id=_opt(job, "channel_id"), message_id=_opt(job, "message_id"),
            task_id=task["id"] if task is not None else None))
    return created


def _opt(row, key):
    try:
        return row[key]
    except (KeyError, IndexError):
        return None


# --- 결정 ------------------------------------------------------------------

def build_execution_prompt(approval, note=""):
    note_section = (DECIDED_NOTE_SECTION.format(note=note.strip())
                    if (note or "").strip() else "")
    return EXECUTION_PROMPT.format(
        title=approval["title"], detail=approval["detail"] or "(설명 없음)",
        action=approval["action"] or approval["title"], note=note_section)


def approve(conn, approval_id, note=""):
    """승인 → 그 동작을 실행할 잡을 만든다. 실행 job_id 반환.

    실행 잡은 제안을 낸 에이전트로 돌아가되 **게이트가 그 동작 하나에만
    해제된다**(worker 가 approval_execution 잡에는 read_only 를 적용하지 않는다).
    사람이 그 동작을 명시적으로 허락했기 때문이다.
    """
    approval = db.get_approval(conn, approval_id)
    if approval is None:
        raise ValueError("승인 항목을 찾을 수 없습니다")
    if approval["status"] != "pending":
        raise ValueError("이미 처리된 항목입니다")

    agent = db.get_agent(conn, approval["agent_id"])
    if agent is None or agent["status"] != "active":
        raise ValueError("제안한 에이전트가 없거나 보관되었습니다")

    origin = db.get_job(conn, approval["job_id"]) if approval["job_id"] else None
    from app import agents as agents_mod
    from app import council
    provider, _reason = agents_mod.resolve_provider(
        conn, agent, approval["action"] or approval["title"],
        usage_state=council.usage_snapshot())

    message_id = None
    if approval["channel_id"]:
        # 승인 결과도 원래 쓰레드에 남는다 — 채널 밖에서 조용히 실행되면
        # 무슨 일이 일어났는지 대화 기록에서 사라진다.
        root_id = None
        if approval["message_id"]:
            src = db.get_message(conn, approval["message_id"])
            if src is not None:
                root_id = src["root_id"] or src["id"]
        message_id = db.create_message(
            conn, approval["channel_id"], role="agent", body="",
            author=agent["slug"], parent_id=root_id, status="queued",
            provider=provider, author_agent_id=agent["id"])

    job_id = db.create_job(
        conn, build_execution_prompt(approval, note), provider,
        model=agents_mod.model_for(agent, provider) or None,
        workdir=(origin["workdir"] if origin is not None else None)
                or agent["workdir"],
        route_reason=f"승인된 동작 실행 (@{agent['slug']})",
        channel_id=approval["channel_id"], message_id=message_id,
        agent_id=agent["id"])
    if message_id is not None:
        db.update_message(conn, message_id, job_id=job_id)
    db.update_approval(conn, approval_id, status="approved",
                       decided_note=note or None, execution_job_id=job_id,
                       decided_at=db.now_iso())
    return job_id


def reject(conn, approval_id, note=""):
    """거절 — 실행 잡을 만들지 않는다. 사유는 기록에 남는다."""
    approval = db.get_approval(conn, approval_id)
    if approval is None:
        raise ValueError("승인 항목을 찾을 수 없습니다")
    if approval["status"] != "pending":
        raise ValueError("이미 처리된 항목입니다")
    db.update_approval(conn, approval_id, status="rejected",
                       decided_note=note or None, decided_at=db.now_iso())
    if approval["channel_id"]:
        db.create_message(
            conn, approval["channel_id"], role="system",
            body=f"승인 거절: {approval['title']}"
                 + (f" — {note.strip()}" if (note or "").strip() else ""),
            author="system", parent_id=approval["message_id"], status="done")
