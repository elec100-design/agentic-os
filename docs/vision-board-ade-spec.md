# 비전 보드 — Orca ADE식 사이드바 채팅 + 멀티탭 메인뷰 스펙

목표: 좌측 사이드바는 연속 대화 스레드(입력창 하단 고정), 우측 메인 영역은
탭바 + 탭 콘텐츠 패널로 재구성. 채팅에서 작업이 생성되면 탭이 자동 추가되고,
사용자가 닫기·전환·재정렬할 수 있어야 한다. 데스크톱/태블릿/모바일 각각 별도
레이아웃 규칙을 갖는다.

## 1. 현재 상태 갭 분석

| 항목 | 현재 구현 | 파일:라인 | 목표와의 갭 |
|---|---|---|---|
| 채팅 입력 위치 | 이미 좌측 `<aside class="orca-chat-rail">`에 있음, 입력창(`#orca-chat-form`)이 레일 하단 고정 | `templates/partials/chat_rail.html:11,28` | **충족됨.** Orca ADE의 "좌측 사이드바 + 하단 고정 입력"은 이미 존재. 이 부분은 재작업 불필요 |
| 메인 영역 구조 | `#board` 하나의 div, htmx가 2초 폴링으로 `partials/board.html` 전체를 통째로 교체 | `templates/project.html:34-35` | **충족됨(V4.5).** `static/board-workspace.js`가 `orca-tabbar`/`orca-tab-panels`를 도입해 canvas/task/flow 탭을 동시에 열어둘 수 있음 |
| 탭 개념 흔적 | `orca-rail-tabs`라는 이름의 탭은 **사이드바 안에서** "채팅"/"실행 로그" 2개만 전환하는 것. 메인 뷰의 탭이 아님. `vision-flow.js`의 "진행 흐름" 토글도 탭이 아니라 같은 `#board` DOM 안에서 다이어그램 ↔ 카드/트리 뷰를 **전환(하나만 보임)**하는 것 | `templates/partials/chat_rail.html:20-23`, `static/chat-rail.js:93-109`, `templates/partials/board.html:76-77`, `static/vision-flow.js:1-26` | **충족됨(V4.5).** `kind="flow"`가 실제 탭으로 이식되어 canvas/task/flow가 각각 독립 탭으로 공존 (`kind="diff"`만 여전히 자리만 있는 상태) |
| 탭 자동 생성 | 없음. 채팅에서 메시지를 보내도(`chat-rail.js:332` submit 핸들러) 메인 영역에는 아무 변화가 없음 — `#board`는 프로젝트 단위 폴링으로만 갱신 | `static/chat-rail.js:332-370` | **충족됨(V4.5).** `messages.created_task_id` 컬럼 + `orca-tab-opened` 커스텀 이벤트로 채팅에서 생성된 태스크가 탭으로 자동 열림 |
| 태스크 상세 | 노드 클릭 → `#task-detail`에 `partials/task_detail.html`을 주입 (모바일은 바텀시트로 CSS 처리 추정) | `templates/project.html:37-39`, `templates/partials/task_detail.html:4-5` | **충족됨(V4.5).** `kind="task"` 탭으로 이식되어 여러 태스크를 동시에 탭으로 열어둘 수 있음(재정렬·재방문 시 localStorage 복원, 존재하지 않는 태스크는 자동 정리) |
| 프론트엔드 스택 | HTMX(서버 렌더 partial + `hx-get`/`hx-post`/`hx-target`) 주도, 바닐라 JS(`board-editor.js`, `chat-rail.js`, `vision-flow.js`)가 DOM 이벤트·SVG 드래그·EventSource(SSE) 등 htmx가 못 하는 부분을 보완. React/Vue 등 클라이언트 프레임워크 없음 | `templates/project.html:12,34-35,46-47`, `static/board-editor.js` 전체 | 탭 상태(활성 탭, 열린 탭 목록, 순서)를 관리할 상태 저장소가 없음. 새로 만든다면 htmx의 partial-swap 패턴과 충돌하지 않게 **탭 컨테이너는 클라이언트 상태, 탭 내용은 서버 partial**로 역할을 나눠야 함 |
| 채널/메시지 데이터 모델 | `channels`/`messages`/`execution_steps` 테이블 존재. `messages`는 `channel_id`/`parent_id`/`root_id`/`seq`로 스레드 구조. `projects`/`tasks` 테이블은 별도 — `projects.channel_id`로 1:1 연결 | `app/db.py:69-117`, `app/db.py:34-68`, `app/db.py:308-320` | **충족됨(V4.5).** `messages.created_task_id INTEGER REFERENCES tasks(id)` 컬럼 추가, `orchestrator.add_task(source_message_id=)`가 채움 |
| 반응형 브레이크포인트 | Orca 전용 스타일은 `static/orca-theme.css`에 분리: `@media (max-width: 1023px)`(사이드바 오프캔버스 전환), `.orca-project-shell`은 `display:flex`(≥1024px) → `display:block`(<1024px). `static/style.css`는 전역 대시보드용 브레이크포인트(900/640/560/480px)만 있고 orca 레이아웃과 무관 | `static/orca-theme.css:731,742,774-786`, `static/style.css:158,1043,1344,2344` | **충족됨(V4.5).** 태블릿(`768px~1023px`) 전용 블록으로 분리해 3단계(≥1024 데스크톱 / 768~1023 태블릿 / <768 모바일)를 확보, 경계값(767/768/1023/1024px) 겹침 없음을 `tests/test_responsive_breakpoints.py`로 검증 |
| 모바일 전용 진입점 | `.orca-rail-mobile-toggle` 버튼으로 사이드바를 오프캔버스로 열기만 구현. 탭/메인뷰 쪽 모바일 전용 UI(세그먼트 컨트롤, 하단 시트)는 없음 | `templates/partials/chat_rail.html:6-9`, `static/orca-theme.css:746-762` | **충족됨(V4.5, 하단 시트 제외).** 모바일은 채팅이 기본 화면, `orca-mobile-segctl` 세그먼트 컨트롤로 작업 화면 전환, `.orca-ws-header`의 뒤로가기 버튼 + 스와이프다운 + 브라우저 뒤로가기(popstate)로 채팅 복귀. 전송 버튼이 세그먼트 컨트롤에 가려지던 겹침 버그도 `--orca-segctl-h` 공유 변수로 수정. 스펙이 요구한 "하단 시트(탭 1개일 때)" 분기는 여전히 미구현 — 항상 풀스크린 전환만 존재 |
| 리사이즈 핸들 | 이미 존재 — `#orca-rail-resize`, 280~480px 드래그, localStorage 유지 | `static/chat-rail.js:49-91`, `static/orca-theme.css:245-255` | **충족됨.** 데스크톱 리사이즈 핸들은 재사용 가능, 신규 구현 불필요 |

## 2. 목표 정보구조 / DOM 트리

```
body.orca (project 페이지)
└── div.orca-project-shell                         [flex 컨테이너, 기존 유지]
    ├── aside#orca-chat-rail.orca-chat-rail          [기존 그대로 — 사이드바 채팅]
    │   ├── div.orca-rail-resize-handle
    │   ├── div.orca-rail-head
    │   ├── (기존 orca-rail-tabs "채팅"/"실행 로그"는 유지 — 이건 사이드바 내부
    │   │    보조 탭이며 메인 멀티탭과는 별개 개념. 걷어내지 않는다)
    │   └── section[data-rail-panel="chat"]
    │       ├── div#orca-chat-scroll
    │       └── form#orca-chat-form                 [입력창 하단 고정, 변경 없음]
    │
    └── main.orca-project-main                      [메인 영역 — 여기를 탭 구조로 교체]
        ├── header.orca-tabbar                       [신규]
        │   ├── div.orca-tabbar-scroll               [가로 스크롤/재정렬 컨테이너]
        │   │   └── button.orca-tab[data-tab-id][data-tab-kind][aria-selected] × N
        │   │       ├── span.orca-tab-icon            (kind별 아이콘)
        │   │       ├── span.orca-tab-title
        │   │       ├── span.orca-tab-status-dot[data-status]  (pending/running/done/failed)
        │   │       └── button.orca-tab-close[aria-label="닫기"]
        │   └── button.orca-tabbar-overflow[hidden]   [탭이 넘칠 때 드롭다운]
        │
        └── div.orca-tab-panels                       [탭 콘텐츠 스택]
            └── section.orca-tab-panel[data-tab-id][data-tab-kind][hidden]  × N
                ├── kind="canvas"  → 기존 #board 서브트리(DAG SVG) 그대로 이식
                ├── kind="task"    → 기존 partials/task_detail.html 내용 그대로 이식
                ├── kind="flow"    → vision-flow.js가 그리는 카드/트리 뷰
                └── kind="diff"    → (향후) 파일 diff 패널, 지금은 자리만
```

핵심 원칙: **탭 콘텐츠 자체는 지금 서버가 이미 만들고 있는 partial(`partials/board.html`,
`partials/task_detail.html`)을 그대로 재사용**한다. 새로 만드는 것은 탭바(`orca-tabbar`)와
탭 상태 관리 레이어(JS)뿐 — DOM/데이터 흐름을 다시 설계하지 않는다.

## 3. 브레이크포인트별 레이아웃 규칙

기존 orca-theme.css는 1024px 한 지점만 쓴다. 아래로 3단계 세분화한다.

### 데스크톱 (`min-width: 1024px`)
- `.orca-project-shell { display: flex }` (기존 유지)
- 사이드바(`#orca-chat-rail`) + 메인(`.orca-project-main`) **동시 표시**
- 사이드바 리사이즈 핸들 활성 (`#orca-rail-resize`, 기존 그대로)
- 탭바는 가로 배치, 탭이 많아지면 `orca-tabbar-scroll`이 가로 스크롤, 넘치면 `overflow` 드롭다운

### 태블릿 (`768px ~ 1023px`)
- 사이드바는 **기본 숨김 + 오버레이 토글**(기존 `.orca-rail-mobile-toggle` 로직 재사용,
  단 지금은 <1024px 전체에 적용되던 것을 768~1023 구간으로 재한정)
- 메인(탭바+탭 패널)이 항상 전체 폭 차지
- 사이드바를 열면 `position: fixed` 오버레이로 메인 위에 뜸(스크림 클릭/Esc로 닫힘) — 기존
  `#orca-rail-scrim` 재사용
- 탭바는 데스크톱과 동일한 가로 탭 UI 유지(터치 대상 크기만 44px 이상으로 확대)

### 모바일 (`< 768px`)
- **채팅이 기본 화면**: `.orca-chat-rail`이 오프캔버스가 아니라 뷰포트 전체를 차지하는
  기본 레이어로 전환 (`.orca-project-shell`을 두 개의 풀스크린 레이어 스택으로 취급)
- 탭(메인 영역)은 **하단 시트 또는 전체화면 스택**으로 전환하는 세그먼트 컨트롤로 진입:
  - 채팅 화면 상단(또는 하단 탭바)에 `[💬 채팅] [📋 작업 N]` 세그먼트 컨트롤 배치
  - "작업" 세그먼트를 탭하면 현재 활성 탭이 하단 시트(작업 1개만 열려 있을 때) 또는
    풀스크린 스와이프 스택(여러 탭)으로 전환
  - 탭 전환은 시트 상단의 가로 스와이프 세그먼트나 `orca-tabbar`를 축소한 chip 목록으로
  - 뒤로가기/스와이프-다운으로 채팅 화면으로 복귀
- 기존 `task-detail-backdrop`(바텀시트 배경) 패턴을 탭 시트에도 재사용

## 4. 탭 상태 모델과 책임 분리

### 탭 상태 모델 (클라이언트 보관, 서버는 소스만 제공)

```js
{
  id: string,              // "task-42", "flow-{project_id}", "canvas-{project_id}" 등 kind+source 조합으로 결정
  title: string,            // 탭바에 표시할 제목 (태스크 제목/축약, "진행 흐름" 등 고정 라벨)
  kind: "canvas" | "task" | "flow" | "diff",
  source_message_id: number | null,  // 이 탭을 자동으로 연 채팅 메시지 id (수동 오픈이면 null)
  status: "pending" | "queued" | "running" | "done" | "failed" | null,  // task 탭만 유의미, canvas/flow는 null
  active: boolean,          // 현재 선택된 탭인지
}
```

- `id` 생성 규칙: `{kind}-{project_id 또는 task_id}` — 같은 태스크를 다시 열면 새 탭이
  아니라 기존 탭을 활성화(중복 방지)
- `source_message_id`는 "채팅에서 작업이 생성될 때 탭 자동 추가"의 연결고리 — 지금
  `messages`/`tasks` 테이블에는 이 관계가 없으므로 **신규로 필요한 서버측 변경**(§ 아래)

### 책임 분리

| 책임 | 담당 |
|---|---|
| 탭 목록 상태(순서, 열림/닫힘, 활성 탭) 보관 | 클라이언트, `sessionStorage` 또는 메모리(새 JS 모듈, 가칭 `orca-tabs.js`). `orca-rail-width`처럼 `localStorage` 키(`orca-tabs-{project_id}`)로 새로고침 후에도 복원 |
| 탭 콘텐츠 렌더링(HTML) | 서버 partial 그대로 (`partials/board.html`, `partials/task_detail.html`) — htmx `hx-get`으로 탭 패널 안에 주입, 탭 자체는 새로 fetch하지 않고 DOM 유지 |
| "이 메시지가 태스크를 만들었다" 판별 | 서버 — `messages` 응답(`/api/channels/{id}/messages`, `/api/messages/{id}/thread`)에 `created_task_id` 같은 필드 추가 필요 (현재 스키마엔 없음, `app/db.py` 메시지 생성 경로에서 태스크 생성 시 `messages.task_id` 컬럼 채우는 마이그레이션 필요) |
| 탭 자동 오픈 트리거 | 클라이언트 — `chat-rail.js`가 메시지 응답에서 `created_task_id`를 보면 `orca-tabs.js`에 "태스크 탭 열기" 이벤트 발행 (기존 `orca-refresh-board`/`orca-task-selected` 커스텀 이벤트 패턴 재사용) |
| 탭 닫기/전환/재정렬 | 클라이언트 전용 — 서버 상태 변경 없음(뷰 상태이므로) |
| 상태 배지(pending/running/done/failed) 갱신 | 서버가 2초 폴링 partial에 최신 상태를 계속 실어 보냄(`partials/board.html` data-status 기존 로직 재사용) → 클라이언트가 폴링 결과에서 해당 task_id의 상태를 읽어 탭의 status dot만 갱신 |

## 5. 후속 구현이 지켜야 할 네이밍 규칙

기존 프리픽스 컨벤션(`orca-*`, `graph-*`, `task-*`)을 따른다. 신규 요소는 다음을 사용한다.

**CSS 클래스**
- `.orca-tabbar` — 탭바 컨테이너
- `.orca-tabbar-scroll` — 가로 스크롤 트랙
- `.orca-tab` — 탭 버튼 (기존 `.orca-rail-tab`과 구분 — 그건 사이드바 내부 채팅/로그
  전환용이므로 절대 재사용하지 않는다)
- `.orca-tab.active` — 활성 탭 (기존 `.orca-rail-tab.active` 패턴과 동일하게 `active`
  클래스 사용, `aria-selected` 속성과 병행)
- `.orca-tab-close` — 닫기 버튼
- `.orca-tab-status-dot` — 상태 점 (기존 `.orca-trace-status-dot`, `status-${step.status}`
  네이밍 패턴 재사용: `.orca-tab-status-dot.status-running` 등)
- `.orca-tab-panels` — 탭 콘텐츠 스택 컨테이너
- `.orca-tab-panel` — 탭 콘텐츠 패널 (`[hidden]`으로 비활성 탭 감춤, 기존
  `.orca-rail-panel[hidden]` 패턴과 동일)
- `.orca-tabbar-overflow` — 탭 넘침 드롭다운
- `.orca-segment` / `.orca-segment-btn` — 모바일 세그먼트 컨트롤(채팅/작업 전환)
- `.orca-tab-sheet` — 모바일 하단 시트로 전환된 탭 컨테이너 (기존 `task-detail-backdrop`/
  `task-sheet-handle` 시트 패턴 재사용)

**데이터 속성**
- `data-tab-id="{kind}-{source_id}"` — 탭 요소(버튼·패널 양쪽 동일 값으로 매칭, 기존
  `data-rail-tab`/`data-rail-panel` 매칭 패턴과 동일 방식)
- `data-tab-kind="canvas|task|flow|diff"`
- `data-tab-status="pending|queued|running|done|failed"` — 기존 `data-status`
  (`graph-node`, `.orca-msg`에서 이미 쓰는 값 그대로 재사용, 새 값 만들지 않음)
- `data-source-message-id="{message_id}"` — 탭이 어느 메시지에서 생성됐는지 (없으면 속성 생략, `null` 문자열 금지 — 기존 `data-job` 등에서 빈 문자열 관례를 따름: `templates/partials/board.html:107` 참고)
- `data-tab-close` — 닫기 버튼 액션 마커 (기존 `data-task-close`, `data-canvas="..."` 등
  "행동을 나타내는 data-* 속성" 관례를 따름, 커스텀 이벤트 리스너가 `[data-tab-close]`로 위임)

**커스텀 이벤트** (기존 `orca-tasks-updated`, `orca-task-selected`, `orca-refresh-board`
패턴 유지, 모두 `document.body`에 dispatch)
- `orca-tab-opened` — `{ detail: { tabId, kind, sourceMessageId } }`
- `orca-tab-closed` — `{ detail: { tabId } }`
- `orca-tab-activated` — `{ detail: { tabId } }`

**서버측 추가가 필요한 최소 스키마 변경** (구현 태스크 담당자가 별도로 설계할 부분이지만
네이밍은 기존 컬럼 스타일 — snake_case, FK는 `_id` 접미사 — 을 따를 것)
- `messages.created_task_id INTEGER REFERENCES tasks(id)` — §4의 "탭 자동 오픈" 판별용.
  기존 `tasks.job_id`, `projects.channel_id` 같은 nullable FK 컬럼 스타일과 동일하게 추가.
