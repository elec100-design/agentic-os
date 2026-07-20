"""프로세스 내(in-process) 잡 출력 알림 허브.

워커와 웹 서버가 같은 이벤트 루프에서 돌 때(기본 배포 형태), 워커는 stdout
청크를 DB에 기록한 직후 여기로 '깨우기 신호'를 보낸다. `/jobs/{id}/stream`
제너레이터는 이 신호를 받으면 즉시 DB를 다시 읽어 새 출력을 내보낸다
→ 최대 1초였던 폴링 지연이 사라진다.

설계 원칙:
- **신호는 데이터를 나르지 않는다.** 출력 내용의 단일 출처는 여전히 DB다
  (`db.append_output`). 허브는 "다시 읽어라"는 무내용 신호만 전달하므로
  순서·중복·유실 걱정이 없고, 새로고침 복원·서버 재시작 복구 경로와도
  그대로 정합한다.
- **자연 폴백.** 워커가 별도 프로세스로 분리되면 그 프로세스의 `publish`는
  이쪽 프로세스의 구독자를 찾지 못한다(메모리 분리). 구독자는 신호를 못 받고
  타임아웃마다 DB를 폴링하는 기존 동작으로 자연스럽게 되돌아간다.
- **단일 이벤트 루프 전제.** 워커·웹이 같은 루프에서 돌므로 `asyncio.Queue`로
  충분하다(락 불필요). publish는 동기·비블로킹이라 워커 경로를 막지 않는다.
"""

import asyncio

# job_id -> 구독 중인 asyncio.Queue 집합
_subscribers: dict[int, set[asyncio.Queue]] = {}


def subscribe(job_id):
    """job_id 출력 신호 구독을 시작하고 큐를 반환한다.

    큐는 maxsize=1 — 신호가 이미 대기 중이면 뒤 신호는 합쳐진다(coalesce).
    어차피 구독자는 신호를 받으면 DB 전체 델타를 다시 읽으므로, 신호 1회가
    N개 청크를 모두 커버한다.
    """
    q: asyncio.Queue = asyncio.Queue(maxsize=1)
    _subscribers.setdefault(job_id, set()).add(q)
    return q


def unsubscribe(job_id, q):
    """구독 해제. 스트림 제너레이터의 finally에서 반드시 호출한다."""
    subs = _subscribers.get(job_id)
    if not subs:
        return
    subs.discard(q)
    if not subs:
        _subscribers.pop(job_id, None)


def publish(job_id):
    """job_id 구독자 전원을 깨운다(무내용 신호). 구독자가 없으면 no-op.

    동기·비블로킹. 큐가 이미 차 있으면(신호 대기 중) 조용히 넘어간다 — 이미
    "다시 읽어라"가 예약돼 있으므로 중복 신호는 불필요하다.
    """
    for q in list(_subscribers.get(job_id, ())):
        try:
            q.put_nowait(None)
        except asyncio.QueueFull:
            pass
