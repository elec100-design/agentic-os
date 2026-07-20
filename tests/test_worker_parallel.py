"""병렬 디스패처 정책 테스트: provider별 직렬 · 서로 다른 provider 병렬 ·
협의(council) 배타. run_job을 인터벌 기록용 가짜로 교체해 스케줄링 정책만
결정적으로 검증한다(실제 CLI·council 내부 불필요)."""
import asyncio
import time

from app import config, db, worker


def _overlap(a, b):
    """두 (start, end) 인터벌이 겹치면 True."""
    return a[0] < b[1] and b[0] < a[1]


async def _drain(conn, job_ids, timeout=5):
    """모든 잡이 기록될 때까지 워커를 돌리고 멈춘다. intervals 반환."""
    intervals = {}

    async def fake_run_job(c, job, providers=None, save=True):
        start = time.monotonic()
        await asyncio.sleep(0.25)
        intervals[job["id"]] = (start, time.monotonic())

    orig = worker.run_job
    worker.run_job = fake_run_job
    stop = asyncio.Event()
    task = asyncio.create_task(
        worker.worker_loop(stop, providers={}, save=False, poll_sec=0.05))
    try:
        deadline = time.monotonic() + timeout
        while len(intervals) < len(job_ids) and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
    finally:
        stop.set()
        await task
        worker.run_job = orig
    return intervals


async def test_different_providers_run_in_parallel(tmp_env):
    conn = db.get_conn(config.DB_PATH)
    a = db.create_job(conn, "p", "alpha")
    b = db.create_job(conn, "p", "beta")
    intervals = await _drain(conn, [a, b])
    assert len(intervals) == 2
    # 서로 다른 provider → 동시에 실행되어 인터벌이 겹쳐야 한다.
    assert _overlap(intervals[a], intervals[b])


async def test_same_provider_serializes(tmp_env):
    conn = db.get_conn(config.DB_PATH)
    a = db.create_job(conn, "p1", "alpha")
    b = db.create_job(conn, "p2", "alpha")
    intervals = await _drain(conn, [a, b])
    assert len(intervals) == 2
    # 같은 provider(CLI) → 직렬. 인터벌이 겹치면 안 된다(사용량 레이스 방지).
    assert not _overlap(intervals[a], intervals[b])


async def test_council_runs_exclusively(tmp_env):
    conn = db.get_conn(config.DB_PATH)
    c = db.create_job(conn, "협의", "council")
    other = db.create_job(conn, "단일", "alpha")
    intervals = await _drain(conn, [c, other])
    assert len(intervals) == 2
    # 협의 잡이 먼저(id 순) 배타 실행 → 단일 잡은 협의가 끝난 뒤에야 시작.
    assert not _overlap(intervals[c], intervals[other])
    assert intervals[other][0] >= intervals[c][1] - 0.01


async def test_council_waits_for_running_to_drain(tmp_env):
    conn = db.get_conn(config.DB_PATH)
    # 단일 잡이 먼저 큐에 있고, 그 뒤 협의 잡. 단일이 도는 동안 협의는 대기.
    single = db.create_job(conn, "단일", "alpha")
    c = db.create_job(conn, "협의", "council")
    intervals = await _drain(conn, [single, c])
    assert len(intervals) == 2
    assert not _overlap(intervals[single], intervals[c])
    assert intervals[c][0] >= intervals[single][1] - 0.01


async def test_concurrency_cap_respected(tmp_env, monkeypatch):
    monkeypatch.setattr(config, "MAX_CONCURRENT_JOBS", 2)
    conn = db.get_conn(config.DB_PATH)
    ids = [db.create_job(conn, "p", f"prov{i}") for i in range(4)]
    intervals = await _drain(conn, ids, timeout=8)
    assert len(intervals) == 4
    # 어느 시점에도 동시 실행이 상한(2)을 넘지 않아야 한다.
    events = []
    for s, e in intervals.values():
        events.append((s, 1))
        events.append((e, -1))
    events.sort()
    cur = peak = 0
    for _, delta in events:
        cur += delta
        peak = max(peak, cur)
    assert peak <= 2


def test_claim_excludes_busy_providers(tmp_env):
    conn = db.get_conn(config.DB_PATH)
    db.create_job(conn, "a", "alpha")
    db.create_job(conn, "b", "beta")
    # alpha를 제외하면 그 다음 후보(beta)를 집는다 — 직렬화의 핵심 프리미티브.
    job = db.claim_next_job(conn, exclude_providers={"alpha"})
    assert job["provider"] == "beta"
