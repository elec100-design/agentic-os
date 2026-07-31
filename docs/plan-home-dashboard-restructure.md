# 설계: 대시보드(홈) 레이아웃 재구성 — 좌=비전보드 / 우=대시보드

## 배경

홈(`templates/index.html`, `body.orca-home`)은 지금 좌측 사이드바에 메모리·노트·사용량이
몰려 있고, 하단에 작업 큐 상태바가 있으며, 비전보드는 별도 페이지(`/projects/{id}`)로
이탈해야 쓸 수 있다. 목표는 **좌측 = 비전보드 세계(프로젝트 목록 · 태스크 편집 · 비전보드
채팅), 우측 = 대시보드 세계(일반 작업 채팅 · 노트 · 세션 · 작업 편집), 중앙 = 두 세계가
공유하는 탭 작업 공간**으로 축을 다시 세우는 것이다.

요구사항(사용자 원문 요약):

1. 메모리 / 노트 검색 / 노트 목록 → 우측 레일의 새 '노트' 탭으로 이동.
2. 하단 '작업 큐' 상태바 제거, 그 자리에 '사용량' 배치.
3. 좌측 사이드바 = 상단 비전보드 프로젝트 목록 + 하단 채팅창.
4. 좌측 채팅에서 비전보드를 실행하면 **페이지 이동 없이** 중앙에 새 탭이 열리고 거기서 실행.
5. 중앙 비전보드 워크플로우의 태스크 클릭 → **좌측**에서 편집. 일반 작업 탭의 편집 → **우측**.

확정된 결정(사용자 확인 완료):

- 좌측 채팅은 **비전보드 전용 새 채팅**. 우측의 기존 `/jobs` 채팅은 그대로 유지한다.
- 작업 큐 UI는 없애되 **하단바에 실행 중/대기/실패 칩**을 남기고, 칩을 누르면 기존 큐 목록이
  펼쳐져 취소·삭제를 계속 할 수 있다.
- 작업 탭 안의 '후속 지시' 입력창은 **우측 레일로 완전히 이동**(중앙은 결과·로그 전용).
- 중앙 비전보드 탭은 **여러 개 동시 오픈 허용** → 다이어그램 에디터를 인스턴스화해야 한다.

---

## 0. 현행 구조에서 반드시 알고 갈 것

- **홈 탭은 잡 id 하나로만 식별된다** — `static/home.js:32` `tabs:[{jobId,title,status,dispose}]`,
  패널 id `home-tab-panel-{jobId}`, localStorage `aos-home-tabs = {jobIds, activeJobId}`.
  패널 로더는 `/partials/job/{id}` + `window.mountJobView()`로 하드코딩(`home.js:94-118`).
  서버 저장 없음(프로젝트 페이지의 `board_tabs` 테이블과 대비).
- **`#jobs` 3초 폴링이 홈의 데이터 척추다** — 상태바 칩, 우측 채팅 버블, 탭 상태점이 전부
  `readJobs()`/`syncFromJobsTable()`/`renderChatBubbles()`(`home.js:179-247`)에서 나온다.
  작업 큐 UI를 지우더라도 **이 폴링 엘리먼트는 DOM에 살려 둬야 한다.**
- **`board-editor.js`는 페이지당 보드 1개를 가정한다** — `board()/canvas()/svg()`(`:37-39`),
  모듈 전역 `view/selTask/drag/pinch/busy`(`:22-32`), `#board .graph-toolbar`(`:188,840,849`),
  `#board .node-palette`(`:898`). 탭 인지 코드(`orca-tab-*` 리스너)는 있으나 그건 **태스크 상세
  패널**용이지 보드 다중화가 아니다.
- **`#board`가 htmx 타깃으로 3곳에 박혀 있다** — `templates/partials/board.html:161`,
  `task_detail.html:19,55`, 그리고 `project.html:61`의 폴링 컨테이너.
- **태스크 인스펙터는 `chat-rail.js` 안에 있다**(`:136-332`), 프로젝트 페이지 전용
  (`#orca-chat-rail` 없으면 즉시 반환). 최근 재작성돼 회귀 테스트가 파일 텍스트를 직접
  검사한다 — `tests/test_task_sidebar_mobile.py`.
- **`POST /projects`는 항상 303 리다이렉트**(`app/main.py:1197-1223`). `request` 인자조차 없다.
  반면 `POST /jobs`는 Accept 헤더로 JSON 분기(`:381-386`) — 이 패턴을 그대로 따라간다.
- **`partials/projects.html:18`의 카드는 `<a href="/projects/{id}">`** — 템플릿은 `/board`
  페이지도 쓰므로 건드리지 말고 홈에서 클릭을 가로챈다(기존 잡 링크 가로채기 `home.js:255-260`과
  동일한 수법).
- **`app.js`는 컴포저를 전역 id로 묶는다** — `#agent-btn`, `#model-btn`, `#workspace-picker`,
  `#tools-popup`, `#provider-input`… 약 400줄. **한 페이지에 피커 달린 컴포저 2개는 불가능**하다.

---

## 1. 목표 레이아웃

```
┌──────────────────────────────┬───────────────────────────┬─────────────────────┐
│ 좌: aside.sidebar (300px)     │ 중앙: .orca-home-ws        │ 우: .orca-side-rail │
│  ├ .brand                    │  ├ #home-tabbar            │  ├ 탭: 채팅 |노트 |세션│
│  ├ #projects   (프로젝트 목록) │  └ #home-tab-panels        │  ├ 채팅: /jobs 컴포저 │
│  ├ #home-task-inspector      │     · kind=job → job_view  │  ├ 노트: #memory     │
│  │   (태스크 클릭 시)          │     · kind=project → board │  ├ 세션: #channels   │
│  ├ #vision-composer (비전보드) │                            │  └ (숨김) 작업 인스펙터│
│  └ .side-foot                │                            │      = 후속 지시 폼   │
├──────────────────────────────┴───────────────────────────┴─────────────────────┤
│ 하단 .orca-statusbar: [사용량 요약]            [실행 3 · 대기 1 · 실패 0 ▾]        │
│   ▾ 펼치면 기존 #jobs 큐 목록(취소·삭제)                                          │
└────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. 우측 레일 — '노트' 탭 추가 (요구 1)

- `index.html:107` 뒤에 `<button class="orca-rail-tab" data-rail-tab="notes">노트</button>`,
  `:186` 뒤에 대응 패널. 탭 전환 루프(`home.js:289-297`)는 `[data-rail-tab]` 제네릭이라 수정 불필요.
- `section.side-memory#memory`를 **`.orca-rail-scroll` 안에 넣지 말고 패널의 직계 자식**으로 둔다.
  `.orca-rail-panel`은 이미 `position:absolute; inset:0; display:flex; flex-direction:column`
  (`orca-theme.css:350`)이라 `.side-memory`가 가정하는 flex 컬럼 문맥과 정확히 일치한다.
  추가 CSS는 `.orca-rail-panel > .side-memory { padding: .75rem; }` 한 줄이면 된다.
  (`.orca-rail-scroll` 안에 넣으면 스크롤 컨테이너가 이중으로 겹친다.)
- `id="memory"`는 절대 바꾸지 않는다 — `partials/memory.html:17`의 검색 폼이
  `hx-target="#memory"`, `app.js:334,350,368`이 이 id를 잡는다. htmx 트리거
  `load, refresh-memory from:body`도 그대로(`from:body`는 문서 스코프라 이사해도 무해).
- **노트 컨텍스트 메뉴 보정**: `#note-dropdown`은 `document.body` 기준 절대배치라 내부 스크롤
  컨테이너 안에서는 버튼과 어긋난다. `app.js`의 `scroll`(capture) 리스너에 걸린
  `.note-scroll` 존재 가드를 없애 **모든 조상 스크롤에서 메뉴가 닫히게** 한다(가장 싼 교정).

## 3. 하단바 — 작업 큐 → 사용량 + 상태 칩 (요구 2)

`index.html:193-203`을 교체하되 **세 개의 id를 유지**한다(`#home-statusbar`, `#statusbar-toggle`,
`#statusbar-body`) — `home.js:266-279`와 `tests/test_home_layout.py:31-33,69-70`이 의존한다.

```
.orca-statusbar#home-statusbar
├ .orca-statusbar-bar
│   ├ section.side-usage.orca-usagebar#usage  hx-get=/partials/usage hx-trigger="load, every 15s"
│   └ button#statusbar-toggle  →  span#statusbar-counts + caret
└ .orca-statusbar-body#statusbar-body[hidden]
    └ section.panel#jobs  hx-get=/partials/jobs hx-trigger="load, every 3s, refresh-jobs from:body"
```

- **`#jobs`는 그대로 3초 폴링**(숨은 컨테이너 안, 지금과 동일). `syncFromJobsTable`이 이미
  `#statusbar-counts`에 칩을 그리므로(`home.js:195-210`) 칩 기능은 **추가 구현이 없다**.
- 사용량은 `class="side-usage"`를 **유지**해야 한다 — `app.js:43-62`의 `aos-usage-open` 토글과
  `style.css:772,793`의 `.side-usage.usage-open` 규칙이 그대로 살아난다. 레이아웃 전용으로
  `orca-usagebar` 클래스만 덧붙인다. `partials/usage.html`은 **무수정**.
- CSS 화해(세로 카드 → 가로 바): `.orca-statusbar-bar`를 flex 행으로, `.side-usage`의
  `border-top/padding-top/margin-top`(`style.css:152-155`)을 0으로 덮고, `.usage-summary`는
  한 줄 가로 스크롤. 펼친 `.usage-detail`은 34px 바 안에 들어갈 수 없으므로
  **바 위로 뜨는 오버레이**(`position:absolute; bottom:100%`)로 처리한다.
- `.orca-statusbar-body #jobs > h2 { display:none }`(`orca-theme.css:1506`)은 **해제**한다 —
  바에서 '작업 큐' 라벨이 사라지므로 펼친 목록의 제목이 유일한 라벨이 된다.

## 4. 좌측 사이드바 재구성 (요구 3)

```
aside.sidebar   (flex column, 260px → 300px)
├ .brand                        flex:none        (#nav-close 유지 — ≤900px 드로어)
├ #projects                     flex:1 1 40%; overflow-y:auto
│    hx-get=/partials/projects  hx-trigger="load, every 5s, refresh-projects from:body"
├ #home-task-inspector[hidden]  flex:1 1 auto; overflow-y:auto   (§6에서 채움)
├ form.composer#vision-composer flex:none                        (§8)
└ .side-foot                    flex:none
```

- 폭을 260px(`orca-theme.css:1335-1343`) → **300px**로 넓힌다. 인스펙터 폼(제목·설명·의존성·
  태스크 채팅)이 260px에서는 못 쓴다(프로젝트 페이지 레일도 `min-width:280px`).
- 인스펙터가 열리면 `.sidebar.has-inspector .side-projects { flex: 0 0 30%; }`로 목록을 눌러 주고
  `← 프로젝트` 되돌리기 버튼을 단다(`chat_rail.html:15-17`의 대응물).
- **프로젝트 카드 클릭 가로채기**(템플릿 무수정): `home.js`에서
  `e.target.closest('.project-card')` → `e.preventDefault()` → 중앙 탭 오픈.
  ⌘/Ctrl·Shift 클릭과 딥링크는 그대로 살려 둔다.
- ≤900px에서 사이드바는 오프캔버스 드로어(`style.css:175-188`)다. 요구 5가 모바일에서 죽지
  않도록 **태스크 선택 시 드로어를 자동으로 연다**(`body.classList.add("nav-open")`).
  드로어 하단에 상태바 높이만큼 `padding-bottom`을 준다.

## 5. 중앙 탭 일반화 — kind 도입 (요구 4)

`static/home.js`의 탭 모델을 `{tabId, kind, refId, title, status, dispose}`로 바꾼다.
탭 id는 `job-{id}` / `project-{id}`, 패널 id는 `home-tab-panel-{tabId}`.

- **하위호환 마이그레이션**: `aos-home-tabs`가 `{jobIds, activeJobId}`면 restore 시
  `{v:2, tabs:[{kind:"job",refId}], activeTabId}`로 변환해 읽고, 이후 v2만 쓴다.
- **kind별 로더 테이블**로 `loadPanel()`을 대체:
  - `job` → `/partials/job/{id}` + `window.mountJobView()` (현행 그대로), 복원 프로브는 `HEAD`.
  - `project` → 패널에 `.orca-board-host[data-project-id]`를 만들고 `window.mountBoardEditor()`
    (§6) 호출, 복원 프로브는 `GET /api/boards/{id}/tabs`(이미 404 처리됨) 또는
    `partial_board`에 `methods=["GET","HEAD"]` 추가.
- `syncFromJobsTable`/`renderChatBubbles`는 `tabs.filter(kind==="job")`만 보도록 한정하고,
  `activeJobId`는 활성 탭에서 파생되는 게터로 바꾼다.
- 탭 활성/종료 시 `document.body`에 `orca-tab-activated` / `orca-tab-closing` /
  `orca-tab-closed`를 **`board-workspace.js`와 같은 이름·같은 detail 형태**로 쏜다
  (`{tabId, kind, refId}`) — 좌측 인스펙터와 보드 인스턴스가 두 페이지에서 한 코드로 반응한다.
- **보드 폴링은 htmx가 아니라 JS 인터벌**: 인스턴스마다 2초 인터벌로 `reloadBoard()`를 돌리되
  **비활성 탭에서는 정지**한다. htmx 폴링은 멈출 수가 없어 탭 3개면 영구히 3배로 때린다.
  `htmx:beforeRequest`의 편집 중 가드(`board-editor.js:989-995`)는 인터벌 콜백 안의
  인스턴스 조건으로 옮긴다. 프로젝트 페이지도 같은 인터벌로 통일하고 `hx-get/hx-trigger`를 뗀다.
- `window.boardOrientation()`은 뷰포트 파생값이라 **전역 유지**(`board.html:161`,
  `task_detail.html:20,56`이 htmx 표현식에서 이름으로 부른다).

## 6. `board-editor.js` 다중 인스턴스화 (요구 4·5의 최대 난관)

현재 IIFE 본문을 **팩토리로 감싸고**, document 레벨 리스너는 얇은 공용 디스패처로 한 번만 설치한다.

```js
window.mountBoardEditor = function (root, { projectId }) { … return dispose; };
window.selectTask = (id, root) => (root ? inst(root) : inst(activeRoot))?.selectTask(id);
```

- `board()/canvas()/svg()` → `root` 기준 조회. 모듈 전역 상태(`view/selTask/drag/pinch/busy/
  paletteOpen/lastOrientation/prevNodeStatus`)는 인스턴스 필드로 내린다.
- `document`에 붙은 포인터/클릭/서브밋 핸들러는 유지하되 첫 줄에서
  `e.target.closest(".orca-board-host")`로 인스턴스를 찾고 없으면 반환한다.
- **htmx 타깃 탈-싱글톤(필수)**: `board.html:161`, `task_detail.html:19,55`의
  `hx-target="#board"` → `hx-target="#board-{{ project['id'] }}"`.
  `partial_task` 컨텍스트에 `project`가 이미 있다(`app/main.py:1288`).
  `vision-flow.js`의 `#board`도 `.orca-board-host`로 바꾼다(홈에는 1단계에서 로드하지 않는다).
- **이벤트에 `projectId`를 실어야** 좌측 인스펙터가 "어느 보드의 태스크인지" 안다 —
  `orca-task-selected`, `orca-task-context-close`, `orca-tasks-updated`, 그리고
  `orca-refresh-board`(이건 detail이 있으면 해당 인스턴스만, 없으면 전체 새로고침 → 기존
  `chat-rail.js` 호출부 무수정).
- **프로젝트 페이지는 같은 진입점을 쓴다**(`project.html`에서 `mountBoardEditor` 1회 호출) —
  코드 분기 없음. 인스턴스가 1개면 동작은 지금과 동일하다.
- 일정 위험이 커지면 **대안**: 탭은 여러 개 허용하되 **활성 탭에만 에디터를 마운트**하고
  비활성 시 해제한다(모듈 싱글톤 유지 → diff 대폭 축소, 대신 탭 전환 시 카메라 위치를 잃는다).

## 7. 태스크 인스펙터 추출 → `static/task-inspector.js` (요구 5 좌측)

`chat-rail.js:136-332`의 인스펙터 함수 일체(`showTaskContext`, `renderTaskContext`,
`closeTaskContext`, `taskMessageEl`, `runsHtml`, `optionsHtml`, `wideScreen`,
`scrollChatToEnd`, `taskArtifact`, `taskEscape`)를 새 모듈로 옮기고 마운트 계약을 준다:

```js
window.mountTaskInspector(container, { onOpen, onClose, onBoardChanged, setTitle })
  → { show(id, {projectId}), close(), destroy(), activeTaskId }
```

- 프로젝트 페이지: `chat-rail.js`가 `.orca-rail-panels`에 마운트하고 콜백으로 패널 숨김/
  뒤로가기 버튼/제목 교체를 처리한다(현재 동작 그대로). 레일 탭 클릭 시 `inspector.close()`.
- 홈: `home.js`가 `#home-task-inspector`에 마운트하고 `orca-task-selected`를 받아
  `inspector.show(id, {projectId})`. `onOpen`에서 `.sidebar.has-inspector`(+좁으면 `nav-open`).
- `container`는 `.orca-task-context > .orca-task-context-body` 구조를 갖춰야
  `style.css:2185-2277`가 양쪽에서 그대로 적용된다.
- 홈에는 `window.ORCA_CHAT_RAIL`이 없으므로 제목 교체는 반드시 `setTitle` 콜백으로 뺀다.

## 8. 좌측 비전보드 채팅 (요구 4)

- **서버**: `POST /projects`에 JSON 모드를 추가한다 — `create_job`의 기존 패턴
  (`app/main.py:381-386`)을 그대로 복제. `create_project`에 `request: Request` 인자를 추가하고,
  `Accept: application/json`이면 `{"project_id": id}`, 아니면 지금처럼 303.
  기존 폼 제출(`/board` 페이지, `tests/test_board_routes.py:73,105,163`)은 영향 없다.
- **클라이언트**: `#vision-composer`가 FormData로 `/projects`에 POST(Accept JSON) →
  `window.openHomeTab({kind:"project", refId: project_id, title: goal.slice(0,40)})` →
  `refresh-projects` 이벤트로 좌측 목록 갱신.
- **대화 이력은 따로 만들지 않는다.** `#projects` 목록(상태 배지 + 진행률,
  `partials/projects.html:19-33`)이 곧 비전보드의 히스토리다.
- **피커(에이전트/모델/작업 위치)는 1단계에서 넣지 않는다.** `app.js`가 컴포저를 전역 id로
  묶고 있어 두 번째 피커 세트를 붙이려면 `mountComposer(form)` 리팩터(≈400줄)가 선행돼야 한다.
  플래너는 `orchestrator._pick_planner`가 자동 선택하므로 기본 흐름은 성립하고, 세부 제어가
  필요하면 기존 `/board` 페이지를 쓴다. (피커 일반화는 별도 후속 과제로 분리.)

## 9. 우측 작업 인스펙터 — 후속 지시 이동 (요구 5 우측)

- `job_view.html:52-83`의 `form.composer.job-followup`을 **`partials/job_followup.html`로 분리**.
- `_job_view_ctx`에 `embed_followup` 플래그 추가 → 독립 페이지(`GET /jobs/{id}`)는 `True`(현행
  유지), 홈 탭용 `GET /partials/job/{id}`는 `False` → **중앙 탭은 자동으로 읽기 전용**이 된다.
- 새 라우트 `GET /partials/job/{id}/followup`이 조각만 반환(후속 불가면 빈 본문).
  히든 필드(provider/model/workdir/session_id/context_note…)는 이미 `_job_view_ctx:770-798`가
  계산하므로 **JSON API보다 서버 렌더 조각이 훨씬 싸다.**
- `job-view.js`의 `wireFollowUp`을 `window.wireJobFollowUp(root, jobId)`로 노출만 하고 본문은
  유지(이미 `openJobTab(newId, +jobId)`를 부른다). `mountJobView`도 계속 이걸 호출 → 독립
  페이지 무영향.
- 우측 레일에 숨은 `data-rail-panel="job"` 패널을 두고, `orca-tab-activated`(kind=job)에서
  조각을 불러 채운다. kind=project이거나 탭이 없으면 비우고 `chat` 탭으로 되돌린다.

---

## 10. 테스트

**깨지거나 손봐야 하는 것**

| 파일 | 조치 |
|---|---|
| `tests/test_home_layout.py:69` | `#statusbar-body` 문자열 정확 일치 — 속성 순서 유지하거나 완화 |
| `tests/test_home_layout.py:75-83` | 사이드바 슬라이스 패턴 유지, 같은 방식으로 메모리·사용량 이동 테스트 추가 |
| `tests/test_task_sidebar_mobile.py:16,55-72` | `RAIL_JS` → `INSPECTOR_JS`(`static/task-inspector.js`)로 재조준. 탭 클릭 테스트는 `closeTaskContext()` → `inspector.close()`. **테스트가 문자열로 잡는 조각**(`class="orca-task-advanced"`, `wideScreen() ? " open" : ""`, `matchMedia("(min-width: 768px)")`, 배지 템플릿)은 추출 시 **글자 그대로 옮긴다** |
| `test_board_routes.py`, `test_mobile_layout.py`, `test_responsive_breakpoints.py`, `test_board_tabs.py` | 그대로 통과해야 한다(폼 POST 303 유지, 프로젝트 셸 셀렉터 불변) |

**새로 추가**

- `test_home_layout.py`: 노트가 레일에 있고 사이드바에 없다 / 사용량이 상태바에 있고 `#jobs`
  3초 폴링이 살아 있다 / 사이드바에 `#projects`·`#home-task-inspector`·`#vision-composer`가 있다 /
  **컴포저 전역 id가 문서에 정확히 1번씩만** 등장한다(좌측 컴포저의 중복 방지).
- `POST /projects` JSON 모드(`{"project_id": int}`)와 폼 303 동시 보장, `HEAD /partials/board/{id}`.
- `GET /partials/job/{id}`에는 `job-followup`이 **없고**, `GET /jobs/{id}`에는 **있고**,
  `/partials/job/{id}/followup`에는 세션 필드가 있다.
- `test_home_tabs.py`: `kind:"project"` 로더, v1→v2 마이그레이션 분기, `window.openJobTab` 유지.
- `test_board_editor_multi_instance.py`: `board-editor.js`에 `#board` 직접 조회가 없고
  `window.mountBoardEditor`를 노출한다 / 세 템플릿의 `hx-target`이 `#board-`로 바뀌었다.

---

## 11. 구현 순서 (각 단계 끝에서 앱은 항상 동작)

| 단계 | 내용 | 크기 | 위험 |
|---|---|---|---|
| 1 | 하단바(사용량+칩+접히는 큐) + 우측 '노트' 탭 | 소 | 낮음 — `.usage-detail` 오버레이, 노트 드롭다운 스크롤 |
| 2 | 좌측 셸: `#projects` + 인스펙터 자리 + 비전 컴포저(아직 폼 303) | 소 | 낮음 — 요구 1~3 완료 지점 |
| 3 | `POST /projects` JSON + 홈 탭 `kind` 일반화(보드는 **읽기 전용** 렌더) | 중 | 중 — localStorage v2 마이그레이션, `activeJobId` 분리가 칩·버블·상태점의 척추를 건드림 |
| 4 | `mountBoardEditor` 팩토리 + `#board` 탈싱글톤 + 인스턴스 폴링 | 대 | **높음** — 카메라·드래그·핀치·링크모드·팔레트·SSE가 전부 모듈 전역. **프로젝트 페이지에서 호출부 1개로 먼저 착지시키고 검증한 뒤** 홈에 켜는 2커밋으로 나눌 것 |
| 5 | `task-inspector.js` 추출 + 좌측 마운트 | 중 | 중 — 회귀 테스트가 텍스트를 grep |
| 6 | 우측 작업 인스펙터(후속 지시 이동) | 중소 | 중 — `mountJobView` 디스포저/onFinish와 레일 동기화 |
| 7 | 마무리: 모바일 드로어 자동 오픈, 상태바 여백, `.has-inspector` 크기 조정 | 소 | 낮음 |

## 12. 비싼 지점과 더 싼 대안

1. **좌측 컴포저 피커** — `app.js`의 전역 id 결합 때문에 두 번째 피커 세트는 `mountComposer`
   리팩터(≈400줄)를 요구한다. → **1단계에서는 피커 없는 컴포저**로 출발.
2. **`#board` htmx 타깃 3곳** — `closest` 기반이 아니라 **서버 렌더 per-project id**가 가장 싸다
   (`task_detail.html`의 폼은 보드 호스트 밖에 있다).
3. **`board-editor.js` 전역 상태** — 진짜 다중 인스턴스가 부담되면 §6의 대안(활성 탭만 마운트).
4. **`#jobs` 폴링 제거 금지** — 지우면 칩·버블·상태점이 조용히 죽는다.
