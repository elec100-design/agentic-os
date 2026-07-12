"""협의(Council) 모드 — 여러 에이전트가 같은 문제에 답을 제안하고(1단계),
서로의 답을 비평·개선한 뒤(2단계), 종합자가 최종 답을 합성한다(3단계).

전체 흐름은 하나의 잡 안에서 실행된다(큐 슬롯 1개, 내부 병렬). 서로 다른
벤더 CLI를 asyncio.gather로 동시에 띄우므로 세션 충돌이 없고, 에이전트 간
컨텍스트는 세션이 아니라 프롬프트 텍스트로 전달한다(각 호출은 stateless).
"""
from __future__ import annotations

import asyncio
import string
from datetime import datetime, timezone

from app import codexbar, config, db, memory
from app.providers import PROVIDERS, rank_cloud

# 실행 중인 협의 잡의 서브프로세스 목록 (job_id → [proc...]) — 취소용
council_procs: dict[int, list] = {}

PROPOSE_PROMPT = (
    "다음 문제에 대해 당신의 최선의 답을 제시하세요. 핵심 근거를 명확히 밝히고, "
    "확신이 없는 부분은 불확실하다고 표시하세요.\n\n"
    "## 문제\n\n{question}"
)

CRITIQUE_PROMPT = (
    "다음 문제에 대해 여러 AI가 각자 답을 제안했습니다. 각 제안의 강점과 "
    "오류·누락을 간결히 비평한 뒤, 비평을 반영해 개선된 당신의 최종 답을 "
    "작성하세요.\n\n"
    "## 문제\n\n{question}\n\n"
    "## 제안들\n\n{proposals}"
)

SYNTHESIZE_PROMPT = (
    "당신은 여러 AI 전문가의 답변을 종합하는 의장입니다. 아래 제안과 비평을 "
    "모두 검토해 가장 정확하고 완전한 최종 답을 작성하세요. 서로 상충하는 "
    "부분은 근거가 강한 쪽을 채택하고 그 판단 근거를 밝히세요. 최종 답만 "
    "출력하세요.\n\n"
    "## 문제\n\n{question}\n\n"
    "{transcript}"
)

_STATUS_LABEL = {
    "failed": "실패 — 제외",
    "rate_limited": "사용 제한 — 제외",
    "timeout": "타임아웃 — 제외",
    "missing": "CLI 없음 — 제외",
}


def usage_snapshot():
    """CodexBar 캐시에서 라우팅용 최소 상태를 만든다.
    {provider: {"remaining": int|None, "available": bool|None}}"""
    cached = codexbar.read_cache().get("providers", {})
    state = {}
    for name in PROVIDERS:
        if name == "hermes":
            state[name] = {"remaining": None, "available": True}
            continue
        c = cached.get(name) or {}
        state[name] = {"remaining": c.get("remaining"),
                       "available": c.get("available")}
    return state


def select_members(usage_state, providers=None):
    """참여자 선정 — 설정된 멤버 중 사용량이 소진되지 않은 에이전트.
    최소 인원 미달이면 ValueError."""
    providers = providers or PROVIDERS
    members = []
    for name in config.COUNCIL_MEMBERS:
        if name not in providers:
            continue
        st = (usage_state or {}).get(name) or {}
        if st.get("available") is False:  # 사용량 소진
            continue
        members.append(name)
    if len(members) < config.COUNCIL_MIN_MEMBERS:
        raise ValueError(
            f"협의 가능한 에이전트가 부족합니다 "
            f"(가용 {len(members)}명, 최소 {config.COUNCIL_MIN_MEMBERS}명)")
    return members


def select_aggregator(members, usage_state):
    """종합자 선정 — 고정 설정이 생존자면 그것, 아니면 잔여 사용량 기반."""
    if config.COUNCIL_AGGREGATOR in members:
        return config.COUNCIL_AGGREGATOR
    for name, _ in rank_cloud(usage_state):
        if name in members:
            return name
    if "hermes" in members:
        return "hermes"
    return members[0]


def _clip(text):
    limit = config.COUNCIL_ANSWER_MAX_CHARS
    if len(text) <= limit:
        return text
    return text[:limit] + "\n\n...[길이 제한으로 생략]"


def _label_answers(answers, labels):
    """{provider: text}를 익명 라벨([A],[B]...) 마크다운으로. 편향 완화를 위해
    비평·종합 프롬프트에는 에이전트 이름 대신 라벨만 노출한다."""
    parts = []
    for name, text in answers.items():
        parts.append(f"### 제안 [{labels[name]}]\n\n{_clip(text)}")
    return "\n\n".join(parts)


async def _run_one(provider, prompt, job_id, timeout):
    """CLI 1회 stateless 실행. (status, text, duration_sec) 반환.
    status: ok | failed | rate_limited | timeout | missing"""
    from app import worker  # 순환 임포트 방지 (worker가 council을 지연 임포트)
    cmd = provider.build_command(prompt)
    start = datetime.now(timezone.utc)

    def _elapsed():
        return (datetime.now(timezone.utc) - start).total_seconds()

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=worker._clean_env(),
        )
    except (FileNotFoundError, OSError) as e:
        return "missing", str(e), 0.0

    procs = council_procs.setdefault(job_id, [])
    procs.append(proc)
    stdout_parts, stderr_parts = [], []
    try:
        await asyncio.wait_for(
            asyncio.gather(
                worker._pump(proc.stdout, stdout_parts.append),
                worker._pump(proc.stderr, stderr_parts.append),
                proc.wait(),
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return "timeout", "", _elapsed()
    finally:
        if proc in procs:
            procs.remove(proc)

    stdout = "".join(stdout_parts)
    stderr = "".join(stderr_parts)
    result = provider.parse_output(stdout, stderr, proc.returncode)
    if provider.detect_rate_limit(stdout + "\n" + stderr, proc.returncode):
        return "rate_limited", result.text, _elapsed()
    if proc.returncode != 0:
        return "failed", (stderr or f"exit {proc.returncode}")[-500:], _elapsed()
    return "ok", result.text, _elapsed()


async def _run_stage(conn, job_id, providers, prompts, timeout, emit):
    """한 단계를 병렬 실행. prompts: {name: prompt}. 성공한 {name: text} 반환.
    실패한 에이전트는 진행 로그에 표시하고 이 협의에서만 제외한다."""
    async def one(name):
        status, text, dur = await _run_one(providers[name], prompts[name],
                                           job_id, timeout)
        outcome = status if status in ("ok", "rate_limited") else "failed"
        db.log_usage(conn, providers[name].name, dur, outcome, job_id)
        if status == "ok":
            emit(f"- ✅ {name} ({int(dur)}초)\n")
        else:
            emit(f"- ❌ {name} ({_STATUS_LABEL[status]})\n")
        return name, status, text

    results = await asyncio.gather(*(one(n) for n in prompts))
    emit("\n")
    return {name: text for name, status, text in results if status == "ok"}


def _is_cancelled(conn, job_id):
    fresh = db.get_job(conn, job_id)
    return fresh is None or (
        fresh["status"] == "failed" and fresh["error"] == "cancelled")


def _fail(conn, job_id, reason):
    db.update_job(conn, job_id, status="failed", error=reason,
                  finished_at=db.now_iso())


async def run_council(conn, job, providers=None, save=True, usage_state=None):
    """협의 잡 실행 — run_job과 같은 계약(DB 상태·usage_log·노트 저장까지 책임)."""
    providers = providers or PROVIDERS
    job_id = job["id"]
    question = job["prompt"]
    usage = usage_state if usage_state is not None else usage_snapshot()
    # 사용자 타임아웃(timeout_sec)이 있으면 단계별 CLI 타임아웃으로 쓴다
    timeout = job["timeout_sec"] or config.COUNCIL_STAGE_TIMEOUT_SEC

    def emit(text):
        db.append_output(conn, job_id, text)

    try:
        members = select_members(usage, providers)
    except ValueError as e:
        _fail(conn, job_id, str(e))
        return
    aggregator = select_aggregator(members, usage)
    labels = dict(zip(members, string.ascii_uppercase))

    try:
        emit(f"## 🧑‍⚖️ 협의 모드\n\n참여: {', '.join(members)} · 종합: {aggregator}\n\n")

        # ── 1단계 · 제안 (병렬) ────────────────────────────────────────
        emit("### 1단계 · 제안\n\n")
        propose_prompt = PROPOSE_PROMPT.format(question=question)
        answers = await _run_stage(
            conn, job_id, providers,
            {m: propose_prompt for m in members}, timeout, emit)
        if _is_cancelled(conn, job_id):
            return
        if len(answers) < config.COUNCIL_MIN_MEMBERS:
            _fail(conn, job_id,
                  f"협의 실패: 제안 단계 생존 에이전트 부족 ({len(answers)}명)")
            return

        transcript = "## 제안들\n\n" + _label_answers(answers, labels)

        # ── 2단계 · 상호비평 (병렬, 설정으로 생략 가능) ────────────────
        critiques = {}
        if config.COUNCIL_ROUNDS >= 2:
            emit("### 2단계 · 상호비평\n\n")
            critique_prompt = CRITIQUE_PROMPT.format(
                question=question,
                proposals=_label_answers(answers, labels))
            critiques = await _run_stage(
                conn, job_id, providers,
                {m: critique_prompt for m in answers}, timeout, emit)
            if _is_cancelled(conn, job_id):
                return
            # 비평은 전원 실패해도 치명적이지 않다 — 제안만으로 종합한다
            if critiques:
                transcript += "\n\n## 비평·개선안\n\n" + "\n\n".join(
                    f"### 비평 [{labels[name]}]\n\n{_clip(text)}"
                    for name, text in critiques.items())

        # ── 3단계 · 종합 ──────────────────────────────────────────────
        # 종합자가 앞 단계에서 탈락했으면 생존자 중에서 다시 뽑는다
        survivors = list(critiques or answers)
        if aggregator not in survivors:
            aggregator = select_aggregator(survivors, usage)
        emit(f"### 3단계 · 종합 ({aggregator})\n\n")
        synth_prompt = SYNTHESIZE_PROMPT.format(question=question,
                                                transcript=transcript)
        final = None
        # 종합자 실패 시 나머지 생존자로 1회씩 폴백
        for name in [aggregator] + [s for s in survivors if s != aggregator]:
            status, text, dur = await _run_one(providers[name], synth_prompt,
                                               job_id, timeout)
            outcome = status if status in ("ok", "rate_limited") else "failed"
            db.log_usage(conn, providers[name].name, dur, outcome, job_id)
            if status == "ok":
                aggregator, final = name, text
                emit(f"- ✅ {name} ({int(dur)}초)\n\n")
                break
            emit(f"- ❌ {name} ({_STATUS_LABEL[status]})\n")
            if _is_cancelled(conn, job_id):
                return
        if _is_cancelled(conn, job_id):
            return
        if final is None:
            _fail(conn, job_id, "협의 실패: 종합 단계에서 모든 에이전트가 실패")
            return
    finally:
        council_procs.pop(job_id, None)

    # 최종 답을 앞에 두어 잡 목록·노트에서 바로 보이게 한다
    label_map = " · ".join(f"[{labels[m]}] {m}" for m in answers)
    full_output = (
        f"{final}\n\n---\n\n## 협의 과정\n\n"
        f"참여: {', '.join(members)} · 종합: {aggregator}\n"
        f"라벨: {label_map}\n\n{transcript}\n"
    )
    db.update_job(conn, job_id, status="done", output=full_output,
                  finished_at=db.now_iso())

    if save:
        try:
            note_path = memory.save_note(question, "council", full_output,
                                         workdir=job["workdir"])
            db.update_job(conn, job_id, note_path=str(note_path.resolve()))
        except OSError as e:
            db.update_job(conn, job_id, error=f"memory_save_failed: {e}")
