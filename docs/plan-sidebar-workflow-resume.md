# 설계: 사이드바 태스크 편집 + 전체 실행/일시정지/재개 + 중단 후 이어하기

조사 범위: `app/main.py`, `app/orchestrator.py`, `app/db.py`, `static/board-editor.js`,
`static/app.js`, `static/chat-rail.js`, `templates/partials/board.html`,
`templates/partials/task_detail.html`, `templates/partials/chat_rail.html`,
그리고 기존 계획 문서 `docs/plan-vision-delete-and-diagram-edit.md`,
`docs/plan-workflow-sidebar-editing.md`, `docs/vision-board-editing.md`.

이 문서는 후속 태스크가 **이 문서만 보고** 독립적으로 구현할 수 있도록 현재
상태를 정확히 못박고, 목표 아키텍처·스키마 변경·API·이벤트 흐름을 구체적으로
못박는다.

---

## 0. 이미 구현된 것 vs 갭 — 반드시 먼저 읽을 것

이전 계획 문서(`plan-workflow-sidebar-editing.md`)는 "사이드바에 태스크
편집/채팅이 전혀 없다"는 전제로 쓰였으나, **그 사이 상당 부분이 이미
구현되었다**. 후속 작업은 아래를 재구현하지 말고 그대로 재사용해야 한다.

### 0.1 이미 있음 (재사용)

- **`tasks` 테이블에 `model`, `extra_instruction`, `pos_x`, `pos_y` 컬럼** —
  `app/db.py:61-64, 196-201`.
- **`projects.pause_reason` 컬럼** (`app/db.py:190-191`) — `"manual"` |
  `"task_failed"` | `NULL`. `orchestrator._advance_running()`
  (`app/orchestrator.py:412-459`)이 이미 이 값을 보고 분기한다:
  `pause_reason == "manual"`이면 새 태스크 디스패치를 건너뛰고, 인플라이트
  태스크가 다 빠지면 그 틱에 `status="paused"`로 내린다. **즉 "실행 중이던
  태스크는 끝까지 두고 대기 중인 것만 멈춘다"는 일시정지 의미론은 오케스트레이터
  레벨에 이미 구현돼 있다.**
- **`task_messages` 테이블** (`app/db.py:143-151`) — `task_id, role, content,
  suggested_description, created_at`. 태스크 전용 채팅 스레드가 이미 스키마
  레벨에 있다(별도 채널이 아니라 전용 테이블 방식 — 이전 문서의 "옵션 (a)"와도
  다른, 더 단순한 전용 테이블 방식으로 결론 남).
- **사이드바 태스크 컨텍스트 UI**: `templates/partials/chat_rail.html:29-33`의
  `#orca-task-context`(현재 `hidden`)가 이미 채팅 레일 안에 존재하고,
  `static/chat-rail.js:131-242`의 `showTaskContext(id)` /
  `renderTaskContext(task, messages)`가 이미 태스크 편집 폼 + 태스크 채팅
  스레드를 사이드바에 그린다. `GET /api/tasks/{id}`, `GET
  /api/tasks/{id}/messages`, `PATCH /api/tasks/{id}`,
  `POST /api/tasks/{id}/messages`(`app/main.py:1524-1661`)를 호출한다.
- **`retry_task`에 agent/model/instruction/cascade 조치 UI** —
  `task_detail.html:130-171`(`<details class="task-recover">`), 다이어그램
  캔버스 쪽 태스크 상세 탭에서 이미 완결.

### 0.2 실제 갭 (이 문서가 설계하는 부분)

1. **`showTaskContext()`를 트리거하는 코드가 없다.** 노드를 클릭하면 지금도
   `board-editor.js:selectTask()` → `taskContainerFor()` →
   `window.OrcaWorkspace.openTaskTab(id, ...)`로 **중앙 탭**이 열린다
   (`board-editor.js:282-290`). `chat-rail.js`의 `showTaskContext`를 호출하는
   지점이 어디에도 없다 — **죽은 코드**. `orca-task-selected` 커스텀 이벤트
   리스너(`chat-rail.js:596`)는 실행 로그 탭 전환/채팅 하이라이트만 하지
   `showTaskContext`를 부르지 않는다.
2. **수동 일시정지를 거는 엔드포인트가 없다.** `pause_reason="manual"`을
   세팅하는 코드가 `app/main.py`, `app/orchestrator.py` 어디에도 없다(grep
   결과 0건). 오케스트레이터는 이 값을 "읽기"만 할 뿐 "쓰는" 경로가 없다.
3. **명시적 재개(resume) 엔드포인트가 없다.** `paused → running` 전이는
   `retry_task()` 호출의 부수효과(`orchestrator.py:592-595`)로만 일어난다.
   "여러 태스크를 편집만 하고 이어서 실행" 버튼이 없다.
4. **`chat-rail.js`의 사이드바 편집 폼이 `agent` 텍스트 입력**
   (`chat-rail.js:208` `<input name="agent">`)이라 `task_detail.html`의
   `<select>`(에이전트 목록 + 자동)보다 기능이 떨어진다. `task_type`, `model`,
   `extra_instruction`, 의존성(`depends_on`) 편집도 사이드바 폼에 없다.
5. **`retry`는 여전히 303 리다이렉트** — 사이드바에서 재시도를 걸면 페이지
   전체가 새로고침된다. JSON 응답으로 바꿔야 사이드바 안에서 매끄럽게 갱신된다.
6. **다이어그램 편집(`plan_ready`/`paused`에서 제목/설명/에이전트/의존성
   변경, `POST /tasks/{id}/edit`, `.../deps`)이 캔버스 탭(`task_detail.html`)에
   남아 있고, 사이드바 편집(`PATCH /api/tasks/{id}`)과 경로가 두 개로
   갈라져 있다.** 목표 구조에서는 캔버스 클릭 시 열리는 화면 자체가
   사이드바여야 하므로, 다이어그램 편집 폼 하나로 합치거나(사이드바가
   `/tasks/{id}/edit`을 호출하도록) 최소한 두 경로의 검증 규칙을 하나의
   오케스트레이터 함수로 통일해야 한다 — §5.2 참고.
7. **중단 후 이어하기가 "프로세스 재시작 복구"만 되고 "단위 태스크
   진행 중 체크포인트"는 없다.** §4에서 상세.
8. **`board_tabs`의 `kind="task"` 레코드와 `board-workspace.js`의
   `openTaskTab`이 여전히 살아 있다.** 사이드바로 완전히 옮기면 이 탭 종류를
   없애거나 최소한 클릭 시 열리지 않도록 해야 한다(§5.1).

---

## 1. 현행 구조 요약

### 1.1 다이어그램 렌더링 · 노드 클릭

- `project.html`의 `#board`가 `hx-get="/partials/board/{project_id}"
  hx-trigger="load, every 2s"`로 `partials/board.html`을 폴링해 그린다.
  SVG 노드/엣지는 `orchestrator.layout_graph()`(순수 함수,
  `orchestrator.py:984-1006`)가 계산한다.
- `board-editor.js`가 `document`에 포인터 이벤트를 바인딩해 드래그(이동/연결)와
  클릭(`selectTask(id)`)을 처리한다. 클릭 시:
  1. `GET /partials/task/{id}` → `task_detail.html` 조각.
  2. `isTabWorkspace()`가 참(사실상 항상)이면 `OrcaWorkspace.openTaskTab(id,
     meta)`로 **중앙 탭바에 새 탭**을 만들어 그 안에 채운다 — 이것이 사용자가
     "화면 중앙에 뜬다"고 표현한 현재 동작.
  3. `orca-task-selected` 커스텀 이벤트를 쏘아 `chat-rail.js`가 실행 로그
     탭 전환/채팅 하이라이트를 하지만, **사이드바에 태스크 상세를 채우지는
     않는다.**

### 1.2 태스크 실행 오케스트레이션 흐름 (작업 큐 · 상태 전이 · DB 저장)

세 레이어로 나뉜다:

1. **`projects`/`tasks`(오케스트레이터 북키핑)** — `orchestrator.py`가 관리.
   `project.status`: `planning → plan_ready → running → (paused|done|cancelled)`.
   `plan_failed`는 `planning`에서만 진입. `task.status`: `pending → queued →
   running → (done|failed)`.
2. **`jobs`(실제 실행 단위)** — 태스크가 `pending`이고 의존성이 다 풀리면
   `_advance_running()`이 `db.create_job()`으로 `jobs` 행을 만들고
   `task.job_id`를 채운 뒤 `task.status="queued"`로 올린다
   (`orchestrator.py:452-459`).
3. **`worker_loop()`(`app/worker.py:332-385`)** — `jobs` 테이블만 보고 도는
   범용 디스패처. `db.claim_next_job()`으로 `queued`(또는 `resume_at`이 지난
   `rate_limited`) 잡을 원자적으로 `running`으로 바꿔 실제 CLI 프로세스를
   실행한다(`run_job()`). 오케스트레이터가 만든 잡인지, 채팅에서 만든 잡인지
   구분하지 않는다 — `jobs.channel_id`/`message_id`로 역참조만 다르다.

`orchestrator_loop()`(`orchestrator.py:469-481`)가 `ORCH_POLL_SEC`마다
`db.active_projects(conn)`(= `status IN ('planning','running')`만)를 돌며
`_advance()`를 호출해 계획 파싱 → 태스크 생성, 또는 태스크 동기화 → 디스패치를
진행시킨다. **`plan_ready`/`paused`는 이 루프가 절대 건드리지 않는다** — 이
불변식 위에 "편집 가능 상태 = 안전하게 건드릴 수 있는 상태"가 성립한다
(`EDITABLE_PROJECT_STATUSES = ("plan_ready", "paused")`).

`_sync_tasks()`(`orchestrator.py:373-409`)가 매 틱마다 `task.job_id`가 가리키는
`jobs` 행의 상태를 태스크로 반영한다(잡 상태 → 태스크 상태 매핑:
`queued→queued, running→running, rate_limited→queued, done→done, failed→failed`).
태스크가 실패로 관측되면 그 즉시 프로젝트를 `paused`(`pause_reason=
"task_failed"`)로 내리고 그 틱의 추가 디스패치를 중단한다.

### 1.3 채팅 레일이 태스크와 연결되는 방식

- **프로젝트 채팅**(`#orca-chat-scroll`)은 `projects.channel_id` 1개짜리
  채널 전체 대화다. 태스크 스코프가 아니다.
- **태스크 채팅**은 `task_messages` 테이블 + `/api/tasks/{id}/messages`
  API로 이미 별도 구현돼 있다(§0.1). 다만 진입 UI(§0.2 #1)가 없어 죽은
  기능이다.
- `messages.created_task_id`는 반대 방향 역참조(채팅이 새 태스크를 만들었을
  때)로, 태스크 채팅과는 무관한 별개 기능이다.

### 1.4 중단(토큰 소진 · 프로세스 종료) 시 무엇이 유실되는가

`worker.run_job()`은 CLI 서브프로세스를 실행하고 stdout을 `db.append_output()`
으로 **실시간** 누적하지만, 이는 `jobs.output` 컬럼에만 쓰이고 **`tasks.output`
에는 잡이 `done`이 될 때 `_sync_tasks()`가 한 번에 복사**한다
(`orchestrator.py:391-393`). 즉:

- **서버 프로세스가 죽으면**: `jobs.status='running'`인 채로 멈춘다. 재기동 시
  `db.recover_running()`(`db.py:321-332`)이 `provider='council'`인 잡만
  `failed`로, 그 외 모든 `running` 잡은 그냥 `queued`로 되돌린다. **이때
  `jobs.output`에 쌓여 있던 부분 출력, `session_id`는 보존되지만
  `resume_at`은 설정되지 않으므로 `run_job()`이 원래 프롬프트를 처음부터
  다시 보낸다**(`CONTINUE_PROMPT`을 쓰는 조건은 `session_id and resume_at`이
  둘 다 있을 때뿐 — `worker.py:143-145`). 결과적으로 **그 CLI 호출의 진행
  중이던 부분 작업은 버려지고 처음부터 재시도**된다. 프로젝트/태스크
  레벨에서는 태스크가 `running`으로 남아 있다가 재기동 후 잡이 다시
  `queued→running`으로 도는 것을 `_sync_tasks`가 반영하므로, **오케스트레이터
  진행 자체(어느 태스크까지 끝났는지)는 잃지 않는다** — 완료된(`done`) 태스크는
  그대로 남는다.
- **에이전트가 CLI 사용량 한도(토큰/레이트리밋)에 부딪히면**: `provider.
  detect_rate_limit()`이 감지해 `jobs.status='rate_limited'` +
  `resume_at`을 세팅한다(`worker.py:220-229`). `claim_next_job()`이
  `resume_at`이 지나면 자동으로 다시 집어 `CONTINUE_PROMPT`로 이어간다 — **이
  경로는 이미 "이어서 진행"이 구현돼 있다.** 사용자가 말한 "토큰 부족으로
  중단되면 이어서 할 수 없다"는 이 자동 경로가 아니라, **오케스트레이터
  자체를 실행 중인 상위 에이전트(Claude Code 세션)가 죽는 경우**(=본 코드베이스를
  개발 중인 세션이 컨텍스트 소진으로 끊기는 것)를 가리키는 것으로 보인다 —
  이는 애플리케이션 버그가 아니라 **개발 세션 연속성 문제**이며, 이 문서
  §4에서 별도로 "실행 이력의 영속적 가시성"으로 다룬다(작업 진행 상황을
  DB에서 그대로 복원해 볼 수 있게 하는 것 — 이미 상당 부분 가능하나
  명시적 "재개" 액션이 없다는 것이 진짜 갭).
- **결론적 갭**: (a) 개별 CLI 잡이 중간에 죽으면 부분 출력이 버려지고
  재시작한다(체크포인트 없음), (b) 사용자가 명시적으로 "일시정지"를 걸
  방법이 없다(§0.2 #2), (c) 일시정지 후 "이어서 실행"을 명시적으로 누를
  방법이 없다(§0.2 #3), (d) 태스크별 실행 이력(몇 번 재시도했는지, 각
  시도의 부분 출력)이 남지 않아 "왜 멈췄는지" 재구성이 잡 1건의 최종
  상태로만 가능하다(잡이 재사용되지 않고 매 실행마다 새 `jobs` 행이
  생기므로 이력 자체는 잡 테이블에 다 있지만, 태스크당 잡 히스토리를 보여주는
  UI/API가 없다).

---

## 2. 목표 아키텍처

### 2.1 사이드바 태스크 편집 패널

- 노드 클릭 → `board-editor.js:selectTask()`가 **`OrcaWorkspace.openTaskTab`
  호출을 제거**하고 대신 `chat-rail.js`가 export하는
  `window.OrcaChatRail.showTaskContext(id)`를 호출한다(§0.1의 기존 함수를
  전역으로 노출하기만 하면 됨 — `window.showTaskContext = showTaskContext;`
  한 줄 추가).
- 사이드바 상단 = 작업 내용(제목/설명/타입/에이전트/모델/세부지시/의존성 +
  상태 배지 + 산출물 링크), 그 아래 = 그 태스크 전용 채팅(`task_messages`).
  이는 이미 `chat-rail.js:renderTaskContext()`의 골격과 일치한다 — 폼 필드만
  `task_detail.html`의 `.task-edit` 수준으로 보강한다(§5.2).
- 캔버스는 어떤 경우에도 워크플로우 그래프로 남는다(탭 전환 없음). 이는
  `chat_rail.html`이 이미 그렇게 설계돼 있다 — "노드 선택 시 같은 좌측
  레일이 태스크 인스펙터로 전환된다. 중앙 캔버스는 어떤 경우에도 워크플로우
  화면으로 남는다"(`chat_rail.html:29-30` 주석). **이 주석이 이미 목표
  아키텍처를 정확히 서술하고 있다 — 구현만 안 됐을 뿐.**

### 2.2 태스크별 채팅

- `task_messages` 테이블 그대로 사용(§0.1). 사용자가 코멘트를 남기면
  `_generate_task_reply()`(`main.py:1622-1641`)가 그 자리에서 잡을 만들어
  동기 실행하고(`worker.run_job(..., save=False)`), 에이전트가 새 `description`을
  제안하면(`parse_task_chat_reply`) 사이드바에 "제안 설명 적용" 버튼이
  뜬다(이미 `chat-rail.js:155-164`에 구현됨) — 적용은 편집 폼의 textarea
  값만 바꾸고 저장은 사용자가 별도로 눌러야 한다(안전).
- 갭: 채팅 메시지로 **의존성**이나 **에이전트/모델**을 바꾸자는 제안은
  현재 프로토콜(`{"reply":..., "description":...}`)에 없다 — 1차 범위에서는
  description 제안만 지원하고, 에이전트/모델 변경은 편집 폼으로만 하는 것으로
  범위를 좁힌다(제품 결정, 확장 시 `parse_task_chat_reply`의 JSON 스키마에
  `agent`/`model` 키를 추가하면 됨).

### 2.3 전체 실행 · 일시정지 · 재개

- **실행**: 기존 `POST /projects/{id}/approve`(plan_ready→running) 그대로.
- **일시정지**(신규): `POST /projects/{id}/pause` → `pause_reason="manual"`
  세팅. 오케스트레이터의 기존 분기(`orchestrator.py:435-438`)가 인플라이트
  태스크는 끝까지 두고 새 디스패치만 막다가, 다 빠지면 자동으로
  `status="paused"`로 내린다 — **이 로직은 이미 있으므로 신규 함수는 상태
  플래그만 세팅하면 된다.**
- **재개**(신규): `POST /projects/{id}/resume` → `status="paused"` +
  `pause_reason` 무관하게 `status="running", pause_reason=NULL`로 전이.
  `task_failed`로 일시정지된 경우도 이 엔드포인트로 재개 가능해야 한다(실패
  태스크를 사이드바에서 재시도로 고친 뒤 "이어서 실행"을 누르는 흐름).
- **일부 태스크 선택 재편집**: 이미 되는 것 — `paused` 상태는
  `EDITABLE_PROJECT_STATUSES`에 포함되므로 그 상태에서 사이드바로 아무
  `pending`/`failed` 태스크나 열어 편집 가능(§2.1). 별도 "다중 선택 UI"는
  1차 범위에서 제외(순차 선택-편집으로 충분, 기존 계획 문서 결론과 동일).

### 2.4 중단 후 이어하기

세 층위로 나눠 설계한다 — **프로젝트 재개**, **태스크 재개**, **잡(개별 CLI
호출) 재개**.

1. **프로젝트 재개**: 이미 됨. `db.active_projects()`가 서버 재기동 시에도
   `planning`/`running` 프로젝트를 다시 집어 `_advance()`를 태운다. 추가 작업
   불필요.
2. **태스크 재개**: 이미 됨(§1.4) — `_sync_tasks()`가 잡 상태를 반영하므로
   `done` 태스크는 유지되고 `running`이던 태스크는 잡이 재개되는 대로 계속
   진행한다.
3. **잡(개별 CLI 호출) 재개 — 진짜 갭**: 지금은 `recover_running()`이
   `running` 잡을 무조건 `queued`로 되돌려 **처음부터 재시도**한다. 개선안:
   - `resume_at`을 굳이 안 쓰고도, `session_id`가 있는 잡이 중단 복구되면
     `CONTINUE_PROMPT`를 쓰도록 `run_job()`의 조건
     (`worker.py:143-145`)을 `job["session_id"] and (job["resume_at"] or
     job["attempts"] > 1)`로 완화한다 — 즉 "재시도 중(attempts > 1)이고
     세션이 있으면 항상 이어가기 프롬프트"로 바꾼다. `supports_resume=False`인
     provider는 기존처럼 원 프롬프트로 폴백(변경 없음).
   - `jobs` 테이블에 `checkpoint` 같은 컬럼을 새로 만들 필요는 없다 —
     `output` 컬럼이 이미 부분 출력을 실시간 누적하고 있으므로, 재개 시
     `CONTINUE_PROMPT` 앞에 "이전 부분 출력"을 붙여 컨텍스트를 주는 방식도
     가능하나 이는 §3에서 다루는 `attempt_seq`/부분출력 아카이빙과 함께
     처리한다(잡이 재시도되면 `output`이 새로 시작되므로, 이전 시도의
     `output`을 보존하려면 실행 시도 단위로 분리 저장해야 한다 — §3.2).

---

## 3. DB 스키마 변경안

### 3.1 `tasks.status` — enum 정리 (제약 강화, 값 자체는 변경 없음)

현재 `tasks.status`는 자유 문자열이며 실제 쓰이는 값은
`pending/queued/running/done/failed` 5가지다. 사용자가 요청한
`pending/running/paused/done/failed` 5종 enum과는 **"queued"가 빠지고
"paused"가 들어간다는 차이**가 있다. 이 문서의 결론: **`queued`는 그대로
유지한다** — "잡이 만들어졌지만 아직 워커가 안 집었다"는 상태는 실행 흐름상
반드시 필요하고 `queued`를 없애면 `_advance_running`의 인플라이트 카운트
로직이 깨진다. 대신:

- **`paused`는 태스크 레벨에 추가하지 않는다.** 일시정지는 **프로젝트
  레벨** 개념이다(§2.3) — 태스크는 일시정지 중에도 자기 상태(`pending`,
  `running`, `done`, `failed`)를 그대로 유지하고, "이 프로젝트가 지금
  일시정지 중"이라는 사실은 `projects.status='paused'` +
  `projects.pause_reason`만으로 충분히 표현된다. 태스크에 `paused`를
  추가하면 "일시정지 때 실행 중이던 태스크"의 상태를 `running`에서
  `paused`로 옮겨야 하는데, 그러면 그 태스크가 실제로 계속 실행되고 있다는
  사실과 모순된다(§2.3에서 "인플라이트는 끝까지 둔다"고 명시).
- SQLite는 컬럼에 CHECK 제약을 추가하는 마이그레이션이 번거로우므로(테이블
  재생성 필요), **enum 강제는 애플리케이션 레벨 상수로 충분**하다 —
  `orchestrator.py`에 이미 있는 판단 함수들(`task_editable`,
  `task_recoverable` 등)이 사실상의 상태 기계 역할을 한다. 신규 상수만
  추가:
  ```python
  # app/orchestrator.py
  TASK_STATUSES = ("pending", "queued", "running", "done", "failed")
  PROJECT_STATUSES = ("planning", "plan_failed", "plan_ready", "running",
                      "paused", "done", "cancelled")
  PAUSE_REASONS = ("manual", "task_failed")
  ```
  DB 마이그레이션 불필요.

### 3.2 실행 시도(run) 이력 — 신규 테이블 `task_runs`

목적: (a) 태스크당 몇 번 시도했는지, 각 시도의 부분 출력/에러를 보존해
"중단 후 무엇이 있었는지" 재구성 가능하게 하고, (b) 잡이 재시도로 덮어써도
이전 시도 로그가 사라지지 않게 한다. 지금은 `retry_task()`가 태스크 필드를
초기화(`_RESET_FIELDS`)하면서 `output`/`error`를 지우므로 이전 시도 기록이
완전히 사라진다 — 디버깅도, "이어서 하기"의 근거도 없어진다.

```sql
CREATE TABLE IF NOT EXISTS task_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id INTEGER NOT NULL REFERENCES tasks(id),
  job_id INTEGER REFERENCES jobs(id),
  attempt INTEGER NOT NULL,           -- 이 태스크에서 몇 번째 시도인지 (1부터)
  provider TEXT NOT NULL,
  model TEXT,
  status TEXT NOT NULL DEFAULT 'running',  -- running|done|failed
  output TEXT NOT NULL DEFAULT '',    -- 그 시도의 최종(or 중단 시점) 출력 스냅샷
  error TEXT,
  interrupted INTEGER NOT NULL DEFAULT 0,  -- 서버 재시작으로 강제 중단됐는지
  created_at TEXT NOT NULL,
  finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_task_runs_task ON task_runs(task_id, attempt);
```

- `db._migrate()`에 `CREATE TABLE IF NOT EXISTS`(SCHEMA 문자열에 추가)로
  넣는다 — 기존 컬럼 추가형 마이그레이션과 달리 신규 테이블은 `ALTER TABLE`
  불필요, `SCHEMA` executescript에 넣으면 기존 DB에도 자동 생성된다(SQLite
  `CREATE TABLE IF NOT EXISTS`는 기존 DB에서도 안전).
- **쓰는 지점**: `orchestrator._advance_running()`이 태스크에 새 잡을
  디스패치할 때(`orchestrator.py:452-458` 근처) `task_runs` 행을 하나 만들고
  `attempt = 이전 최대 attempt + 1`로 채운다. `worker._sync_message`류 훅과
  별개로, `orchestrator._sync_tasks()`가 태스크를 `done`/`failed`로 반영하는
  시점에 해당 `task_runs` 행도 같이 `status`/`output`/`error`/`finished_at`을
  채운다(태스크당 활성 run은 항상 1개뿐이므로 "가장 최근 running 행" 갱신으로
  충분 — 별도 조인 없이 `task_id`+`status='running'` 조회).
- **`recover_running()`이 서버 재시작으로 잡을 강제 `queued`로 되돌릴 때**,
  해당 잡에 연결된 `task_runs` 행(`status='running'`)을
  `interrupted=1, status='failed', error='interrupted: server restarted'`로
  마감한다 — 다음 시도가 새 `task_runs` 행(`attempt+1`)을 만들게 된다. 이
  한 줄 추가로 "중단 이력이 조회 가능"해진다(§4의 목표 정확히 충족).
- **API**: `GET /api/tasks/{id}/runs` — 사이드바가 "이전 시도 이력" 아코디언을
  보여줄 때 사용(1차 범위에서는 선택 사항, §6 참고).

### 3.3 `projects` 테이블 — 재개 카운터(선택)

일시정지→재개가 반복될 수 있으므로 감사 목적의 `resume_count INTEGER NOT
NULL DEFAULT 0` 컬럼을 추가할 수도 있으나, **필수는 아니다** — `pause_reason`
이 NULL이 되는 시점을 재개로 간주하면 되고, 굳이 카운터가 필요하면
`updated_at` 변화만으로도 감사 로그(`usage_log`류)에 append하는 방식이 더
일관적이다. **이 문서는 스키마에 추가하지 않는 것으로 결론**(YAGNI).

### 3.4 마이그레이션 방식

이 코드베이스의 기존 컨벤션을 그대로 따른다(`app/db.py:_migrate()`):
- **신규 테이블**(`task_runs`)은 `SCHEMA` 문자열에 `CREATE TABLE IF NOT
  EXISTS`로 추가 — `get_conn()`이 매 연결마다 `executescript(SCHEMA)`를
  실행하므로 기존 DB에도 자동으로 생긴다. 데이터 백필 불필요(새 테이블이라
  과거 데이터 없음).
- **기존 테이블에 컬럼 추가가 필요해지면** `_migrate()` 안에 `PRAGMA
  table_info(tbl)`로 컬럼 존재를 확인하고 없으면 `ALTER TABLE ... ADD
  COLUMN`(SQLite는 `ALTER TABLE ADD COLUMN`만 지원, `DROP COLUMN`/제약 추가
  불가) — 이번 설계에서는 §3.1 결론에 따라 **`tasks`/`projects`에 신규
  컬럼이 필요 없다**(기존 `pause_reason`, `model`, `extra_instruction`으로
  충분).
- 별도 마이그레이션 프레임워크(Alembic 등)는 도입하지 않는다 — 기존
  스타일(SCHEMA 문자열 + `_migrate()` 함수)을 그대로 따르는 것이 이 문서의
  결론이다(기존 코드베이스에 이미 확립된 패턴이므로 일관성 우선).

---

## 4. 오케스트레이터 함수 변경안

`app/orchestrator.py`:

```python
def pause_project(conn, project_id):
    """실행 중 프로젝트에 수동 일시정지를 건다. 인플라이트 태스크는 끝까지
    두고, 다음 틱부터 _advance_running이 새 디스패치만 멈춘다."""
    project = db.get_project(conn, project_id)
    if project is None or project["status"] != "running":
        raise ValueError("실행 중인 프로젝트만 일시정지할 수 있습니다")
    db.update_project(conn, project_id, pause_reason="manual")
    # status는 그대로 running으로 둔다 — _advance_running이 인플라이트가
    # 다 빠졌을 때 스스로 paused로 내린다(orchestrator.py:435-438 기존 로직).
    # 즉시 status=paused로 바꾸면 active_projects()에서 빠져 인플라이트
    # 태스크의 잡 상태 동기화(_sync_tasks)가 멈춘다 — 반드시 running 유지.


def resume_project(conn, project_id):
    """일시정지(수동/실패 무관)를 풀고 실행을 재개한다."""
    project = db.get_project(conn, project_id)
    if project is None or project["status"] != "paused":
        raise ValueError("일시정지 상태의 프로젝트만 재개할 수 있습니다")
    db.update_project(conn, project_id, status="running", pause_reason=None,
                      error=None)
```

주의: `pause_project`는 `status`를 바꾸지 않고 `pause_reason`만 세팅한다 —
`db.active_projects()`가 `status IN ('planning','running')`만 골라야
`_advance_running`이 계속 돌아 인플라이트 태스크를 동기화(`_sync_tasks`)하고
다 끝나면 스스로 `paused`로 떨어뜨릴 수 있다. 만약 즉시 `status="paused"`로
바꾸면 오케스트레이터 루프가 그 프로젝트를 더 이상 안 보므로 인플라이트
태스크의 완료 반영이 멈춘다(치명적 버그가 될 지점이므로 구현 시 반드시
테스트: "인플라이트 태스크가 있는 상태에서 pause 호출 → 그 태스크가 done될
때까지 project.status는 running으로 남아 있어야 하고, 그 후에야 paused로
전이해야 한다").

`recover_running()`(`db.py:321-332`)에 `task_runs` 마감 로직 추가(§3.2):

```python
def recover_running(conn):
    conn.execute(
        "UPDATE task_runs SET status='failed', interrupted=1, "
        "error='interrupted: server restarted', finished_at=? "
        "WHERE status='running' AND job_id IN "
        "(SELECT id FROM jobs WHERE status='running')",
        (now_iso(),))
    conn.execute(  # 기존 council 처리
        "UPDATE jobs SET status = 'failed', ... WHERE status='running' "
        "AND provider='council'", ...)
    conn.execute("UPDATE jobs SET status='queued' WHERE status='running'")
    conn.commit()
```
(순서 주의: `task_runs` 갱신은 `jobs.status`를 바꾸기 **전에** 실행해야
`WHERE job_id IN (SELECT ... WHERE status='running')` 서브쿼리가 맞는 잡을
가려낸다.)

`worker.run_job()`의 재개 프롬프트 조건 완화(§2.4-3):

```python
# 기존: job["session_id"] and job["resume_at"] and supports_resume
# 변경:
if (job["session_id"] and getattr(provider, "supports_resume", True)
        and (job["resume_at"] or job["attempts"] > 1)):
    send_prompt = CONTINUE_PROMPT
```

---

## 5. API 엔드포인트 목록

### 5.1 신규

| 메서드 | 경로 | 요청 | 응답 | 비고 |
|---|---|---|---|---|
| `POST` | `/projects/{project_id}/pause` | (본문 없음) | `303 → /projects/{id}` (기존 `_project_action` 패턴 재사용) | `status='running'`일 때만 허용, 아니면 `400 {"detail": "..."}` |
| `POST` | `/projects/{project_id}/resume` | (본문 없음) | `303 → /projects/{id}` | `status='paused'`일 때만 허용 |
| `GET` | `/api/tasks/{task_id}/runs` | - | `[{"id", "attempt", "provider", "model", "status", "output", "error", "interrupted", "created_at", "finished_at"}]` | 사이드바 "이전 시도" 아코디언용(1차 범위 선택) |

`pause`/`resume`은 기존 `approve`/`replan`/`cancel`과 동일하게
`_project_action(project_id, fn)` 헬퍼(`main.py:1292-1299`)를 그대로 재사용:

```python
@app.post("/projects/{project_id}/pause")
def pause_project_endpoint(project_id: int):
    return _project_action(project_id, orchestrator.pause_project)


@app.post("/projects/{project_id}/resume")
def resume_project_endpoint(project_id: int):
    return _project_action(project_id, orchestrator.resume_project)
```

사이드바(JS)에서 fetch로 부를 때는 303 리다이렉트를 따라가지 않고
`{ redirect: "manual" }` 또는 `credentials`만 쓰고 응답 후
`document.body.dispatchEvent(new Event("orca-refresh-board"))`로 보드 폴링을
강제 갱신하는 패턴을 쓴다(§6.2). 리다이렉트 대상 페이지를 사이드바 컨텍스트에서
그대로 로드하면 안 되므로, **htmx가 아니라 plain `fetch()` + 수동 새로고침
이벤트**를 쓴다(기존 `chat-rail.js`가 `PATCH /api/tasks/{id}`에 이미 이
패턴을 쓰고 있다 — `chat-rail.js:224-227`).

### 5.2 변경

| 메서드 | 경로 | 변경 내용 |
|---|---|---|
| `POST /tasks/{task_id}/retry` | 303 리다이렉트 → **JSON 응답**으로 변경. `Accept: application/json` 헤더 유무로 분기하거나(기존 폼 기반 UI 하위호환), 아예 새 `POST /api/tasks/{task_id}/retry`를 만들고 기존 폼 라우트는 그대로 둔다. **권장: 후자**(기존 다이어그램 캔버스의 `<form method=post>` 제출을 안 건드리기 위해). 신규 `POST /api/tasks/{task_id}/retry`는 `orchestrator.retry_task`를 호출하고 `dict(db.get_task(conn, task_id))`를 반환한다 — `PATCH /api/tasks/{id}`와 동일한 응답 형태. |
| `PATCH /api/tasks/{task_id}` | `payload`에 `task_type`, `extra_instruction`, `depends_on`(seq 목록) 필드 추가. 현재 `TaskUpdate`는 `title/description/task_type/agent/model/reset_status`만 있고 `extra_instruction`과 의존성이 빠져 있다 — 사이드바 편집 폼을 캔버스 편집 폼과 동등하게 만들려면 필수. `depends_on` 변경 시 내부적으로 `orchestrator.set_task_deps()` 재사용(자기참조/순환 검증 그대로 적용). |

`TaskUpdate` 확장안:
```python
class TaskUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    task_type: str | None = None
    agent: str | None = None
    model: str | None = None
    extra_instruction: str | None = None
    depends_on: list[int] | None = None
    reset_status: bool = False
```
`api_update_task()`에 다음 분기 추가:
```python
if payload.extra_instruction is not None:
    fields["extra_instruction"] = payload.extra_instruction.strip() or None
...
db.update_task(conn, task_id, **fields)
if payload.depends_on is not None:
    orchestrator.set_task_deps(conn, task_id, payload.depends_on)  # ValueError → 400
```

### 5.3 변경 없음(그대로 재사용)

| 경로 | 용도 |
|---|---|
| `GET /api/tasks/{id}` | 사이드바 태스크 로드 |
| `GET /api/tasks/{id}/messages` | 태스크 채팅 이력 |
| `POST /api/tasks/{id}/messages` | 태스크 채팅 전송(+ description 제안) |
| `GET /partials/board/{project_id}` | 캔버스 폴링(2초) |
| `POST /projects/{id}/approve` | 계획 승인 → 실행 시작 |
| `POST /projects/{id}/cancel` | 완전 취소(재개 불가) — 일시정지와 구분 유지 |
| `POST /projects/{id}/replan` | 재계획(내부적으로 `paused`를 경유) |

---

## 6. 프런트엔드 이벤트 흐름

### 6.1 노드 클릭 → 사이드바 오픈 (변경)

```
board-editor.js: onPointerUp(노드, 드래그 없음)
  → selectTask(taskId)
    → (변경) taskContainerFor() 호출 제거
    → (변경) window.OrcaChatRail.showTaskContext(taskId) 호출
      → chat-rail.js: showTaskContext(id)
        → GET /api/tasks/{id}, GET /api/tasks/{id}/messages (병렬)
        → renderTaskContext(task, messages) — 사이드바 채팅 레일이
          "태스크 모드"로 전환(#orca-task-context 표시,
          .orca-rail-panel[chat|log] 숨김, ← 뒤로가기 버튼 노출)
    → document.body.dispatchEvent("orca-task-selected", {taskId, status})
      (기존 이벤트 유지 — 실행 로그 SSE 소스 전환 등 부가 효과 유지 목적이면
      showTaskContext 내부에서 로그 스트림도 같이 열 수 있으나, 1차 범위는
      "채팅 레일이 곧 태스크 상세"이므로 별도 실행 로그 탭 전환은 생략 가능
      — task-context 패널 안에 SSE 로그를 붙이는 것으로 통합(§6.3))
```
- 캔버스는 그대로 남는다(탭 전환 없음) — `OrcaWorkspace.openTaskTab` 호출을
  제거하는 것이 이번 변경의 핵심 한 줄.
- `board-workspace.js`의 `openTaskTab`/`kind==="task"` 관련 코드와
  `board_tabs` 테이블의 `kind="task"` 레코드 생성(`db.get_or_create_board_tab
  (..., "task", ...)` 호출부, `orchestrator.py:229-230, 406-408, 777-778`)은
  더 이상 필요 없어지지만 **삭제는 별도 후속 작업으로 분리**(이 문서의
  스코프는 "사이드바로 전환"이지 "탭 인프라 제거"가 아니다 — 탭 인프라는
  `flow`/`artifact`/`diff`/`preview` 등 다른 kind에도 쓰이므로 신중히 분리
  삭제해야 함).

### 6.2 편집 저장 → 보드/사이드바 동기화

```
사이드바 편집 폼 submit
  → PATCH /api/tasks/{id} (JSON)
  → 성공 시:
      1. document.body.dispatchEvent(new Event("orca-refresh-board"))
         (기존 이벤트 — board-editor.js가 리스닝해 #board를 강제 재조회하는
         핸들러가 이미 있는지 확인 필요: 없다면 신규로
         `document.body.addEventListener("orca-refresh-board", () =>
         htmx.trigger("#board", "load"))` 한 줄 추가)
      2. showTaskContext(id) 재호출로 사이드바 자체도 최신화(이미
         chat-rail.js:227에 구현됨)
  → 실패 시: alert(err.message), 버튼 재활성화(이미 구현됨)
```

### 6.3 실행 로그(SSE) — 사이드바 태스크 컨텍스트 안으로 통합

- `task_detail.html`의 `.task-log`(SSE 구독, `initTaskLog()` in
  board-editor.js)를 사이드바 `renderTaskContext()` 안에 옮긴다: 태스크
  `status in (running, queued)`이고 `job_id`가 있으면 `#orca-task-context-body`
  안에 로그 스트림 `<div>`를 렌더하고 `/jobs/{job_id}/stream`을 구독한다.
  실행 로그 탭(`data-rail-tab="log"`)은 프로젝트 전체 관점(여러 태스크 중
  실행 중인 것 고르는 드롭다운)으로 남기고, 태스크를 선택했을 때는 굳이 그
  탭으로 전환하지 않고 태스크 컨텍스트 안에 인라인으로 보여주는 편이
  "캔버스는 안 바뀌고 사이드바 하나로 완결된다"는 목표에 더 부합한다.

### 6.4 일시정지 · 재개 버튼 (신규)

```
board.html 상태 배너 (project.status == 'running'):
  <button data-action="pause">일시정지</button>
    → JS: fetch(`/projects/${id}/pause`, {method:"POST"})
        → 성공: document.body.dispatchEvent("orca-refresh-board")
        → 실패(400): alert(detail)

board.html 상태 배너 (project.status == 'paused'):
  <button data-action="resume">이어서 실행</button>
    → fetch(`/projects/${id}/resume`, {method:"POST"}) → 동일 갱신 패턴
  (기존 '재계획'/'취소' 버튼과 나란히 배치, pause_reason에 따라 문구 분기:
   "manual"이면 "일시정지됨 — 태스크를 편집한 뒤 이어서 실행하세요",
   "task_failed"면 기존 문구 "실패한 태스크를 클릭해 재시도하거나...")
```
`board.html`은 이미 `pause_reason`을 템플릿 컨텍스트에서 안 쓰고 있으므로,
`_board_partial()`(`main.py:1238-1255`)의 context에 `pause_reason":
project["pause_reason"]`를 추가해 템플릿이 분기할 수 있게 한다.

### 6.5 다중 노드 선택 후 순차 편집 (1차 범위)

- 다중 동시 편집 UI는 만들지 않는다(§0.2 결론과 동일). 대신 **일시정지
  상태에서 노드를 연속으로 클릭하면 사이드바가 매번 갱신**되는 것만으로
  "여러 태스크를 골라 편집"이 자연스럽게 된다 — 이미 `showTaskContext(id)`가
  매 호출마다 컨텍스트를 새로 그리므로 추가 구현 불필요.

---

## 7. 구현 순서 제안 (후속 태스크 분할 기준)

1. **오케스트레이터**: `pause_project`/`resume_project` 추가 + 테스트
   (인플라이트 보존 확인) — §4.
2. **API**: `POST /projects/{id}/pause`, `/resume`, `TaskUpdate` 확장
   (`extra_instruction`, `depends_on`), `POST /api/tasks/{id}/retry`(JSON) — §5.
3. **프런트 배선**: `selectTask()` → `showTaskContext()` 전환,
   `orca-refresh-board` 이벤트 배선, 사이드바 편집 폼 필드 보강(select 기반
   에이전트/타입/모델, 의존성 체크리스트) — §6.1, §6.2.
4. **일시정지/재개 UI**: `board.html` 배너 버튼 + `pause_reason` 문구 분기 — §6.4.
5. **`task_runs` 테이블 + `recover_running` 연동**: 중단 이력 가시화 — §3.2, §4.
6. **재개 프롬프트 조건 완화**(`worker.run_job`): 서버 재시작 후 CLI가
   맥락을 이어받도록 — §4.
7. (선택, 낮은 우선순위) `board-workspace.js`의 `openTaskTab`/`kind="task"`
   탭 인프라 정리 — §6.1 마지막 문단.

각 단계는 이전 단계 없이도 독립적으로 배포 가능하다(1~2는 백엔드만, 3~4는
프런트만, 5~6은 복구 견고성만 다뤄 서로 의존성이 낮다).
