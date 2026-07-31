# 워크플로 다이어그램 ↔ 좌측 사이드바 태스크 편집 구조 조사 및 설계

목표: 중앙에는 워크플로 다이어그램만 두고, 노드 클릭 시 좌측 사이드바 상단에
태스크 상세 + 그 아래 태스크 전용 채팅창을 띄워 편집/코멘트로 태스크를 고친다.
전체 실행은 중앙 다이어그램에서, 실행 중 '일시정지' 후 일부 태스크를 사이드바에서
다시 편집하고 이어서 실행할 수 있어야 한다.

이 문서는 **조사 + 변경 지점 목록**만 다룬다. 코드 수정 없음.

---

## 1. 현재 탭 구조 — 워크플로 다이어그램 렌더링과 태스크 노드 클릭 처리

### 1.1 다이어그램이 렌더링되는 위치

- `templates/project.html`(project.html:38-70)의 `#orca-workspace` 안에
  `#orca-tab-panels` → `<section id="orca-tab-panel-canvas" data-tab-kind="workflow">`
  하나가 항상 첫 탭으로 고정돼 있고, 그 안에
  `<div id="board" hx-get="/partials/board/{project_id}" hx-trigger="load, every 2s">`
  가 실제 SVG를 담는다(project.html:59-63).
- `board-workspace.js`(전체 워크스페이스 탭바 모듈)가 초기화되면서 `canvas` 탭을
  탭 목록의 0번째로 강제 등록한다(board-workspace.js:631-636). 탭바는 데스크톱/
  태블릿/모바일 모두 **같은 DOM·같은 탭 상태**를 공유하고 CSS만 폭별로 모양을
  바꾼다(가로 탭바 / 칩 스트립 / 세그먼트 컨트롤 뒤 탭).
- `#board`의 내용은 `templates/partials/board.html`이 채운다: 상태 배너 +
  `.graph-canvas > svg.graph-svg`에 `orchestrator.layout_graph()`가 계산한
  노드(`<g class="graph-node" data-task=... data-seq=...>`)와 엣지를 그린다.
  2초 폴링(htmx `every 2s`)으로 실행 상태가 갱신된다.

### 1.2 태스크 노드 클릭 → 현재 열리는 화면

- 포인터 이벤트는 전부 `board-editor.js`가 `document`에 바인딩해서 처리한다
  (`onPointerDown/Move/Up`, board-editor.js:677-792). 노드를 드래그 없이 놓으면
  (`d.moved === false`) `selectTask(d.id)`가 호출된다(board-editor.js:775-777).
- `selectTask(id)`(board-editor.js:412-439):
  1. `GET /partials/task/{id}` (→ `templates/partials/task_detail.html`)로 상세
     조각을 받아온다.
  2. **컨테이너 결정**: `isTabWorkspace()`가 참이면(=`board-workspace.js`가 로드된
     정상 경로, 즉 사실상 항상) `window.OrcaWorkspace.openTaskTab(id, meta)`를 호출해
     **새 탭**(`tabId = "task-{id}"`, `kind: "task"`)을 워크플로 탭과 **같은 탭바**에
     추가하고 활성화한다(board-workspace.js:696-706). 즉 노드를 클릭하면 워크플로
     캔버스가 사라지고 그 자리(중앙 탭 패널 영역)에 태스크 상세 탭이 뜬다 —
     사용자 요청에서 말한 "화면 중앙에 해당 태스크의 내용이 뜨는" 현재 동작.
  3. `OrcaWorkspace`가 없는 폴백 경로에서만 옛 `#task-detail`(project.html:76,
     인라인/바텀시트)에 채운다 — 지금은 정상 경로가 아니라 사실상 죽은 코드다.
  4. SSE 진행 로그(`initTaskLog`), 편집 폼 모델 목록 동기화, 선택 상태
     하이라이트, `localStorage`에 선택 태스크 id 저장, 그리고
     `orca-task-selected` 커스텀 이벤트 발행(→ `chat-rail.js`가 실행 로그 탭으로
     전환하거나 채팅에서 해당 잡 메시지를 하이라이트, chat-rail.js:483-501).
- 태스크 탭 안의 내용(`task_detail.html`)은: 헤더(제목/상태뱃지/에이전트) +
  편집·연결·삭제 버튼 + 설명 + 진행 로그(SSE) + 편집 폼(제목/설명/종류/에이전트/
  모델/세부지시, hidden, '편집' 버튼으로 토글) + 선행 태스크 체크리스트 + 오류
  조치(`<details class="task-recover">`, 모델 교체 후 재실행) + 산출물/출력.
- 편집 폼 제출(`hx-post="/tasks/{id}/edit" hx-target="#board"`)은 **`#board`를
  통째로 교체**한다(태스크 상세 자체가 아니라 캔버스가 타깃). 그 후
  `htmx:afterRequest`가 `#task-detail` 또는 `.orca-tab-panel[data-tab-kind="task"]`
  안에서 일어난 성공 요청을 감지하면 `refreshPanel()`을 불러 열려 있던 태스크
  탭을 다시 그린다(board-editor.js:1020-1026). 즉 편집 저장 시 캔버스 갱신 →
  탭 갱신의 2단계 경로다.

### 1.3 탭 종류 요약

`board-workspace.js`가 다루는 탭 kind: `workflow`(캔버스, 유일 고정 탭),
`task`(노드 클릭/채팅에서 생성된 태스크 상세), `flow`(진행 흐름 뷰,
`vision-flow.js`가 채움), `artifact`/`diff`/`preview`(태스크 상세 조각에서
일부만 재사용해 보여주는 파생 탭). 태스크 상세는 지금 **탭 하나**이지 사이드바
요소가 아니다.

---

## 2. 좌측 사이드바(chat-rail) — 현재 DOM 구조와 렌더링 경로

- `templates/partials/chat_rail.html`을 `project.html`이 `<body class="orca">`
  최상단에 include한다(project.html:16) — `#orca-chat-rail`은 항상 화면
  왼쪽에 고정된 `<aside>`이고, 태스크 탭/워크플로 탭과는 완전히 분리된 별도
  패널이다(리사이즈 핸들, 접기/펼치기, 모바일 오프캔버스 지원).
- 내부 탭은 두 개뿐이다(chat_rail.html:20-23):
  - **채팅** (`data-rail-tab="chat"`): `#orca-chat-scroll` + 하단 컴포저
    (`#orca-chat-form`, 에이전트 선택 `#orca-chat-provider`). **프로젝트 채널
    전체**의 대화이지 특정 태스크 전용이 아니다. 서버 쪽 채널은
    `project['channel_id']`(projects 테이블 컬럼) 하나뿐 — 태스크별 채널/스레드
    개념이 없다.
  - **실행 로그** (`data-rail-tab="log"`): `#orca-log-task-select`(현재
    `running` 상태이고 `job_id`가 있는 태스크 목록 드롭다운) + `#orca-log-stream`
    (`/jobs/{id}/stream` SSE 그대로 출력).
- `chat-rail.js`가 렌더링을 담당한다:
  - `loadChatHistory()`: `GET /api/channels/{channelId}/messages` →
    루트 메시지들 → 각각 `GET /api/messages/{root.id}/thread`로 스레드 전체를
    가져와 `buildMessageEl()`로 그린다. 메시지 전송은
    `POST /api/channels/{channelId}/messages`.
  - `renderLogPicker()`: `window.ORCA_CHAT_RAIL.tasks`(project.html이 초기
    주입, `orca-tasks-updated` 이벤트로 board-editor.js가 매 폴링마다 갱신)에서
    `running` 태스크만 골라 드롭다운을 채우고 SSE를 연다.
  - `orca-task-selected` 이벤트(board-editor.js가 노드 클릭 시 발행)를 받으면:
    태스크가 running/queued면 **실행 로그 탭으로 전환**하고 해당 job을 고르며,
    done/failed면 **채팅 탭으로 전환**해 같은 `job_id`를 가진 메시지 행을 찾아
    스크롤+하이라이트한다. 즉 지금도 "노드 클릭 → 사이드바 반응"의 연결고리는
    있지만, **태스크 상세/편집 자체는 사이드바에 없고** 여전히 중앙 탭에 있다.
- 요약: 사이드바는 이미 "채팅 + 실행 로그"라는 2탭 구조를 갖고 있고 노드
  클릭과 약하게 연동돼 있으나, 태스크 상세·편집 UI는 전혀 없다. 채팅도
  프로젝트 전체 채널이라 "이 태스크에 대한 코멘트"라는 개념이 없다.

---

## 3. 태스크 수정/저장 REST 엔드포인트와 스키마

### 3.1 관련 REST 엔드포인트 (`app/main.py`)

| 메서드/경로 | 폼 필드 | 오케스트레이터 함수 | 반환 | 편집 가능 조건 |
|---|---|---|---|---|
| `POST /tasks/{id}/edit` | title, description, task_type, agent, model, extra_instruction, o | `update_task_fields` | 보드 조각(`#board`) | `task_editable` |
| `POST /tasks/{id}/deps` | deps[](seq 목록), o | `set_task_deps` | 보드 조각 | 〃 |
| `POST /tasks/{id}/move` | x, y, o | `move_task` | 보드 조각 | 항상 허용(실행에 영향 없음) |
| `POST /tasks/{id}/delete` | o | `delete_task` | 보드 조각 | `task_editable` |
| `POST /projects/{id}/tasks` | title, description, task_type, agent, x, y, o, message_id | `add_task` | 보드 조각 | `project_editable` |
| `POST /projects/{id}/relayout` | o | `reset_layout` | 보드 조각 | `project_editable`(내부적으로 언제나 가능하지만 UI는 편집 상태에서만 노출) |
| `POST /tasks/{id}/retry` | agent, model, instruction, cascade | `retry_task` | **303 리다이렉트**(`/projects/{id}`, 전체 페이지 새로고침) | `task_recoverable`(failed 또는 output_error가 있는 done) — `plan_ready/paused/running/done` 어디서든 가능, `planning/plan_failed`만 제외 |
| `POST /projects/{id}/approve` \| `/replan` \| `/retry-plan` \| `/cancel` \| `/delete` | - | 각각 | 303 리다이렉트 | 프로젝트 상태별 |
| `GET /partials/task/{id}` | - | - | `task_detail.html` 조각 | 조회는 항상 가능 |

주의점: `retry`만 다른 액션들과 달리 **보드 조각이 아니라 페이지 리다이렉트**를
반환한다 — 사이드바로 옮길 때 이 엔드포인트도 부분 응답(JSON 또는 조각)으로
바꾸거나, 클라이언트에서 리다이렉트 대신 fetch 후 사이드바만 새로 그리는 방식이
필요하다.

`board-editor.js`의 `post()` 헬퍼(board-editor.js:203-230)가 `edit/deps/move/
delete/relayout`을 전부 감싸며 실패 시 되돌리기 → `#board.innerHTML` 교체 →
`refreshPanel()` 순으로 처리한다. **사이드바 중심 구조로 가면 이 헬퍼가 지금처럼
"보드 갱신 후 열린 탭을 새로고침"하는 게 아니라 "보드 갱신 후 사이드바의 태스크
패널을 새로고침"하도록 대상이 바뀌어야 한다.**

### 3.2 관련 테이블 (`app/db.py`)

`projects`:
```
id, goal, title, status, plan_job_id, planner, planner_model, workdir,
error, created_at, updated_at, channel_id
```
`status` 값: `planning`, `plan_failed`, `plan_ready`, `running`, `paused`,
`done`, `cancelled` (`failed`는 태스크 레벨에만 있고 프로젝트 레벨에는 안 씀 —
task_detail 템플릿의 STATUS 사전에 있지만 실제로는 도달하지 않음, 실패 시
`paused`로 감).

`tasks`:
```
id, project_id, seq, title, description, task_type, provider, depends_on(TEXT,
콤마구분 seq 목록), status, job_id, output, artifact_path, error, model,
extra_instruction, pos_x, pos_y, created_at, started_at, finished_at
```
`status` 값: `pending`, `queued`, `running`, `done`, `failed`.

`channels`/`messages`: 프로젝트당 채널 1개(`projects.channel_id`)를
`get_or_create_project_channel`류 로직이 붙여준다(정확한 생성 지점은
`main.py`의 project_page 근처 — 이번 조사에서 상세 추적은 생략, 존재만 확인).
`messages.created_task_id`는 메시지가 채팅에서 태스크를 새로 만들었을 때만
채워지는 **메시지→태스크** 역참조이지, **태스크→메시지**(태스크 전용 채팅
스레드) 관계가 아니다. 지금 스키마에는 "이 태스크에 대한 코멘트/채팅 스레드"를
표현할 컬럼/테이블이 없다.

`board_tabs`: 워크스페이스 탭바의 서버측 영속화(탭 목록, 순서, 활성 여부).
`kind`(`workflow`/`task`/...), `ref_id`, `status`, `order_index`, `is_active`,
`deleted_at`(소프트 삭제). 사이드바 구조로 바뀌면 "태스크 탭"이라는 개념 자체가
없어지므로 이 테이블의 `kind="task"` 레코드들의 존재 이유도 재검토가 필요하다.

---

## 4. 오케스트레이터 실행 루프·상태 전이·취소 처리

### 4.1 루프

`orchestrator_loop()`(orchestrator.py:409-421)가 `ORCH_POLL_SEC` 간격으로
`db.active_projects(conn)`(= `planning`/`running` 상태만, 코드상 유추 — 조사
범위상 `db.py`의 정확한 쿼리까지는 안 봤으나 `_advance`가 이 두 상태만
분기한다는 사실로 확정됨: orchestrator.py:402-406)를 순회하며 `_advance()`를
호출한다. 예외가 나도 프로젝트를 `paused`로 내리고 루프 자체는 죽지 않는다.

### 4.2 상태 전이

```
planning --(계획 잡 완료 파싱 성공)--> plan_ready --(approve)--> running
planning --(계획 잡 실패/파싱 재시도도 실패)--> plan_failed --(retry-plan)--> planning
plan_ready --(replan)--> planning (paused를 경유)
running --(태스크 실패 감지, _sync_tasks)--> paused
running --(모든 태스크 done)--> done
paused --(retry_task 호출로 실패 태스크 재개)--> running (자동)
paused --(replan)--> planning
아무 상태 --(cancel)--> cancelled
```

- `_advance_running()`(orchestrator.py:364-399): 매 틱마다
  1. `_sync_tasks()`로 태스크의 잡 상태를 반영 — 실패가 하나라도 있으면 즉시
     프로젝트를 `paused`로 내리고 그 틱은 끝(추가 디스패치 없음).
  2. 전부 `done`이면 프로젝트 `done`.
  3. `ORCH_MAX_INFLIGHT`까지, `depends_on`이 전부 `done`인 `pending` 태스크를
     골라 잡을 만들고 태스크를 `queued`로 올린다.
- **중요**: 지금 `paused`는 오직 "태스크 실패로 인한 자동 정지"만을 의미한다.
  **사용자가 실행 도중 임의로 누르는 '일시정지' 액션은 존재하지 않는다.**
  `POST /projects/{id}/cancel`은 있지만 이는 잡을 강제 종료하고 프로젝트를
  완전히 `cancelled`로 되돌리는 것이라 "재개 가능한 일시정지"가 아니다.

### 4.3 취소 처리

`cancel_project(conn, project_id, _status="cancelled")`(orchestrator.py:572-591):
- 해당 프로젝트의 `queued`/`running` 태스크마다 연결된 잡을 `failed` +
  `error="cancelled"`로 강제 표시하고 `worker.terminate_job_procs(job_id)`로
  실제 프로세스를 죽인다.
- 계획 잡이 아직 진행 중이면 그것도 같은 방식으로 중단.
- 마지막에 프로젝트 상태를 `_status`(기본 `cancelled`, `replan`에서 호출할 땐
  `"paused"`로 오버라이드)로 바꾼다. 즉 `replan`은 "취소 로직을 재사용해 실행
  중이던 잡을 다 죽이고 paused를 거쳐 planning으로" 가는 구조다.

### 4.4 편집 가능 상태

`EDITABLE_PROJECT_STATUSES = ("plan_ready", "paused")`,
`EDITABLE_TASK_STATUSES = ("pending", "failed")`(orchestrator.py:599-601).
`active_projects()`가 `planning`/`running`만 도니까 `plan_ready`/`paused`
동안은 루프가 그 프로젝트를 절대 안 건드린다 — 편집과 디스패치가 경합할 수
없다는 것이 현재 설계의 핵심 불변식이다. **이 불변식은 목표 구조(실행 중
일시정지 후 편집)에서도 반드시 유지해야 한다** — 즉 "일시정지"는 반드시
`active_projects()` 대상에서 빠지는 상태(=사실상 지금의 `paused`)로 떨어져야
하고, `paused` 상태에서 이미 편집 UI(`project_editable`)가 켜지는 것도 기존
그대로 재사용 가능하다.

---

## 5. 목표 구조로 가기 위한 변경 지점

### 5.1 사용자 액션 · 오케스트레이터

1. **수동 일시정지 신설**: `paused`가 지금은 "실패로 인한 자동 정지" 전용
   의미라 사용자가 누른 '일시정지'와 뜻이 섞인다. 다음 중 택1 필요:
   - (a) `paused` 상태를 그대로 재사용하되 `projects.error` 또는 새 컬럼
     (예: `pause_reason: "manual" | "task_failed"`)으로 원인을 구분해 배너
     문구를 분기.
   - (b) 별도 상태(`paused_manual`)를 신설 — `active_projects()`/
     `EDITABLE_PROJECT_STATUSES`/각 배너 조건문(`board.html`)에 전부 추가해야
     해 변경 범위가 커진다.
   - (a)가 기존 불변식(편집 가능 = plan_ready/paused)을 그대로 쓸 수 있어
     변경이 작다.
2. **일시정지 시 진행 중 잡 처리 결정 필요**: `cancel_project`처럼 즉시
   강제 종료할지, 진행 중인 태스크는 끝까지 두고 새 디스패치만 막을지
   설계 확정 필요. 후자가 사용자 기대(실행 중이던 것도 완료되고 대기 중인 것만
   멈춤)에 더 가깝다면 `_advance_running`에 "일시정지 플래그가 서면 4단계
   디스패치만 건너뛰되 1~2단계(동기화/완료판정)는 계속 수행"하는 분기가
   필요 — 즉 `active_projects()`에서 완전히 빼면 안 되고, `_advance` 내부에서
   상태별로 판단해야 한다.
3. **재개(이어서 실행) 엔드포인트 신설**: 지금은 `paused → running` 전이가
   `retry_task()` 호출의 부수효과로만 일어난다(orchestrator.py:532-534). 사이드바
   에서 여러 태스크를 편집만 하고 "이어서 실행" 버튼을 눌렀을 때를 위한 명시적
   `resume_project(conn, project_id)` 함수 + `POST /projects/{id}/resume`
   엔드포인트가 필요하다.
4. **`retry_task` 응답 형식 통일**: 현재 303 리다이렉트라 사이드바 갱신과
   안 맞는다. 다른 편집 액션처럼 부분 응답(사이드바 조각 또는 JSON)으로
   바꿔야 fetch 기반 사이드바 갱신 흐름에 자연스럽게 낀다.

### 5.2 프론트엔드 — 사이드바로 이동

5. **`task_detail.html`을 사이드바 전용 부분(top) + 태스크 채팅(bottom)으로
   재구성**: 지금 하나의 탭 콘텐츠(헤더/설명/로그/편집폼/의존성/오류조치/
   산출물)를 쪼개서, 사이드바 상단에 "작업 내용"(제목/설명/에이전트/상태/
   산출물 + 편집 폼)을 놓고, 그 아래 "이 태스크 전용 채팅" 영역을 새로 만든다.
6. **태스크 전용 채팅 스레드 신설**: 현재 채팅은 프로젝트 채널 전체용
   (`channel_id` 1개)이라 태스크별 코멘트를 못 담는다. 옵션:
   - (a) `messages` 테이블에 `task_id` 컬럼을 추가해 "이 메시지는 어느 태스크에
     대한 코멘트/지시인가"를 표시하고, 사이드바가 태스크 선택 시
     `GET /api/tasks/{id}/messages`류로 필터링해서 보여준다.
   - (b) 태스크마다 전용 채널을 새로 만든다(기존 channels 재사용, `topic`에
     task 참조) — 스레드/멘션 로직은 재사용되지만 채널 수가 태스크 수만큼
     늘어난다.
   - (a)가 스키마 변경이 작고 기존 채널 하나 안에서 "전체 대화 vs 태스크
     대화"를 뷰만 필터링하면 되어 더 간단해 보인다. 다만 편집 폼 제출(title/
     description 변경)과 "코멘트로 태스크 수정" 요청을 구분할지(=자연어 코멘트를
     받아 에이전트가 태스크 필드를 다시 쓰게 할지, 아니면 순수 편집 폼만
     지원할지)는 제품 결정이 필요 — 이번 조사 범위 밖.
7. **`selectTask()`의 컨테이너 로직 교체** (board-editor.js:282-290,
   412-439): `taskContainerFor()`가 `OrcaWorkspace.openTaskTab()`으로 새 탭을
   여는 대신, `chat_rail.html` 안의 "선택된 태스크" 섹션 DOM을 채우도록 바꿔야
   한다. 탭바에는 워크플로 탭만 남기거나(단일 뷰가 되어 탭바 자체가 불필요해질
   수 있음), 최소한 `kind: "task"` 탭 생성 로직을 제거해야 한다.
8. **`board-workspace.js`의 task-탭 관련 코드 정리**: `openTaskTab`,
   `ICONS.task`, `getTaskPanelId`, `orca-tab-opened`(kind==="task") 리스너,
   로컬스토리지 복원 로직 중 `kind === "task"` 분기 등이 전부 사이드바 구조로
   대체되거나 삭제 대상. `board_tabs` 테이블의 `kind="task"` 레코드도 같이
   정리할지 결정 필요.
9. **`chat-rail.js`의 탭 구조 확장**: 지금 채팅/실행 로그 2탭 구조에 "선택된
   태스크 상세+편집+태스크 채팅" 섹션을 얹을지, 아니면 완전히 별도 모드(태스크
   선택 시 사이드바 전체가 "태스크 모드"로 전환되고 채팅/로그 탭은 숨겨지는
   구조)로 갈지 UX 결정이 필요하다. 문서 서두의 요구사항("사이드바 상단에 작업
   내용, 그 아래 채팅창")은 후자(전용 모드 전환)에 더 가깝다.
10. **다중 선택(일시정지 중 일부 태스크만 편집)**: `board-editor.js`의
    `selTask`는 단일 값이다(board-editor.js:24). 여러 노드를 선택해 사이드바에서
    순차적으로/동시에 편집하는 UX를 지원하려면 다중 선택 상태 관리(예: 노드
    다중 클릭 시 배열로 관리, 사이드바에는 선택된 태스크 목록 + 각각의 편집
    폼)가 추가로 필요. 최소 버전으로는 "한 번에 하나씩 선택해 편집 → 다음
    태스크 선택"만 지원해도 요구사항은 충족되므로, 다중 동시 편집 UI는 1차
    범위에서 제외 가능.
11. **진행 로그(SSE)의 대상 변경**: 지금 `initTaskLog()`가 태스크 탭 패널
    안에 로그 스트림을 심는다(board-editor.js:312-393). 사이드바 구조에서는
    이 스트림이 사이드바의 "실행 로그" 탭과 또는 새 "태스크 상세" 섹션과
    중복될 수 있어 — 태스크 선택 시 사이드바 실행 로그 탭이 자동으로
    해당 job으로 전환되는 기존 로직(`orca-task-selected` → chat-rail.js:483-490)
    을 그대로 살리고, 태스크 상세 섹션 자체에는 별도 로그를 안 붙이는 방향이
    중복을 줄인다.
12. **캔버스 편집 UI(팔레트/편집가능 강조 등)와의 정합성**: `board.html`의
    `editable` 플래그(=plan_ready/paused)에 따라 캔버스에 포트/드래그가
    나타나는 로직은 그대로 유지 가능 — "그래프 구조 편집"(노드 추가/연결/삭제/
    이동)은 지금처럼 캔버스에 남기고, "태스크 필드 편집"(제목/설명/에이전트/
    모델/세부지시)만 사이드바로 옮기는 분리가 기존 코드 재사용 폭을 가장
    넓힌다.

### 5.3 정리 — 우선순위가 높은 변경 지점

1. 오케스트레이터: 수동 일시정지/재개 상태·엔드포인트 (5.1의 1~4)
2. `selectTask()`/`taskContainerFor()`를 사이드바 대상으로 전환 (5.2의 7)
3. `task_detail.html`을 사이드바용 마크업(상단 상세+편집, 하단 채팅)으로 분리 (5.2의 5)
4. 태스크 스코프 채팅(메시지 테이블에 task_id 추가 또는 유사 방안) (5.2의 6)
5. `board-workspace.js`의 task 탭 관련 코드 제거/치환 (5.2의 8)
6. 다중 선택 UX (있으면 좋음, 1차 범위에서 생략 가능) (5.2의 10)
