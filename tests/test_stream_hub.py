import asyncio

import pytest

from app import config, db, stream_hub, worker
from app.providers import ParseResult


def test_publish_without_subscribers_is_noop():
    # 구독자가 없어도 예외 없이 조용히 넘어가야 한다(워커 경로를 막지 않음).
    stream_hub.publish(9999)


async def test_subscribe_receives_signal():
    q = stream_hub.subscribe(1)
    try:
        stream_hub.publish(1)
        # 신호가 즉시 큐에 들어와 대기 없이 회수된다.
        await asyncio.wait_for(q.get(), timeout=1)
    finally:
        stream_hub.unsubscribe(1, q)


async def test_signals_coalesce_maxsize_one():
    q = stream_hub.subscribe(2)
    try:
        # 연속 신호는 하나로 합쳐진다(maxsize=1) — get 한 번이면 델타 전체 커버.
        stream_hub.publish(2)
        stream_hub.publish(2)
        stream_hub.publish(2)
        await asyncio.wait_for(q.get(), timeout=1)
        assert q.empty()
    finally:
        stream_hub.unsubscribe(2, q)


async def test_unsubscribe_cleans_up_registry():
    q = stream_hub.subscribe(3)
    assert 3 in stream_hub._subscribers
    stream_hub.unsubscribe(3, q)
    # 마지막 구독자가 빠지면 job_id 항목 자체가 정리된다(누수 방지).
    assert 3 not in stream_hub._subscribers


async def test_publish_targets_only_matching_job():
    qa = stream_hub.subscribe(10)
    qb = stream_hub.subscribe(11)
    try:
        stream_hub.publish(10)
        await asyncio.wait_for(qa.get(), timeout=1)
        assert qb.empty()  # 다른 job_id 구독자는 깨우지 않는다
    finally:
        stream_hub.unsubscribe(10, qa)
        stream_hub.unsubscribe(11, qb)


class _EchoProvider:
    name = "fake"

    def build_command(self, prompt, session_id=None, model=None):
        return ["sh", "-c", "printf 'chunk'"]

    def parse_output(self, stdout, stderr, exit_code):
        return ParseResult(text=stdout.strip(), session_id="s1")

    def detect_rate_limit(self, output, exit_code, now=None):
        return None


async def test_worker_publishes_signal_on_output(tmp_env):
    conn = db.get_conn(config.DB_PATH)
    db.create_job(conn, "테스트", "fake")
    job = db.claim_next_job(conn)

    q = stream_hub.subscribe(job["id"])
    try:
        await worker.run_job(conn, job, providers={"fake": _EchoProvider()},
                             save=False)
        # 워커가 출력·종료 시 신호를 보냈으므로 구독자는 폴링 없이 깨어난다.
        await asyncio.wait_for(q.get(), timeout=1)
    finally:
        stream_hub.unsubscribe(job["id"], q)
