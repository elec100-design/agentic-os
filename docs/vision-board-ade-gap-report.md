# 비전 보드 ADE 스펙 대비 구현 갭 리포트

대상 스펙: `docs/vision-board-ade-spec.md` §2~§5
대상 코드(작업 트리 현재 상태, 브랜치 `claude/workflow-diagram-editor-pori59`):
`static/board-workspace.js`, `static/chat-rail.js`, `static/orca-theme.css`,
`static/vision-flow.js`, `static/board-editor.js`, `templates/project.html`,
`templates/partials/board.html`, `templates/partials/chat_rail.html`,
`templates/partials/task_detail.html`, `app/main.py`, `app/db.py`

판정 기준: **구현됨**(스펙 그대로 동작) / **부분 구현**(뼈대는 있으나 미완/불일치) /
**미구현**(코드 없음 또는 죽은 코드)

---

## 0. 확정 규명 사항 요약 (a)~(e)

| # | 질문 | 결론 |
|---|---|---|
| (a) | `/api/boards/{id}/tabs` 계열 REST가 실제 존재하는가 | **존재한다.** `app/main.py:1061-1120`에 GET/POST/PATCH/DELETE/PUT order/POST activate 6개 라우트가 모두 구현되어 있고, `app/db.py:125-141`(`board_tabs` 테이블 DDL)와 `app/db.py:446-541`(CRUD 함수)가 실제 SQLite에 쓴다. `board-workspace.js`의 코드 주석(`board-workspace.js:12-14`)은 "API가 없을 수도 있다"고 방어적으로 적어놨지만 이는 **사실과 다르다** — API는 있고 정상 동작한다. 단, 로컬 폴백 코드(`api.*` 호출 실패 시 `catch(() => {})`)는 여전히 남아있어 네트워크 오류 시에는 조용히 로컬 상태로만 동작한다(의도된 설계, 문제 아님) |
| (b) | `messages.created_task_id` 컬럼과 이를 채우는 코드 경로 | **존재하지 않는다.** `app/db.py:80-100`의 `messages` 테이블 DDL에 해당 컬럼 없음. `app/db.py`, `app/main.py`, `app/orchestrator.py` 전체에서 `created_task_id` 문자열 자체가 0건 매치. §4의 "채팅에서 작업이 생성되면 탭 자동 추가" 기능은 서버 쪽 연결고리가 아예 없어 **원천적으로 동작 불가** |
| (c) | 탭 패널(canvas/task/flow)에 실제 서버 partial이 주입되는가 | **kind=canvas, task는 실제 partial 주입. kind=flow는 탭 자체가 생성되지 않음(빈 개념).** 아래 §2 표 참조 |
| (d) | 모바일에서 `.orca-rail-composer` 전송 버튼이 안 보이는 원인 | **가설 확인됨.** `.orca-mobile-segctl`(높이 ≈ 44px 버튼 + 상하 패딩 + `env(safe-area-inset-bottom)`, `z-index:45`, `orca-theme.css:1078-1089`)이 화면 하단에 고정되는데, `.orca-chat-rail`(모바일에서 `top:0;bottom:var(--orca-vv-offset,0px)`, `z-index:20`, `orca-theme.css:1021-1033`)은 이 세그먼트 컨트롤 높이만큼 **하단 여백을 전혀 확보하지 않는다.** 반면 같은 모바일 블록에서 `.orca-project-main`은 `padding-bottom: calc(56px + env(safe-area-inset-bottom))`(`orca-theme.css:1038`)으로 정확히 이 문제를 보정하고 있다 — **채팅 레일에만 이 보정이 빠져 있는 비대칭 버그.** `.orca-rail-composer`는 `.orca-chat-rail`의 마지막 flex 자식(`flex-shrink:0`, `orca-theme.css:508-515`)이라 레일의 물리적 바닥, 즉 뷰포트 최하단에 렌더링되고, 그 자리를 `z-index:45`인 세그먼트 컨트롤이 위에서 덮어버려 전송 버튼이 시각적으로 가려지고 클릭도 세그먼트 컨트롤이 가로챈다 |
| (e) | 모바일 워크스페이스 뷰에서 채팅으로 복귀하는 UI가 실제 렌더되는가 | **렌더된다.** `templates/project.html:71-78`의 `<nav id="orca-mobile-segctl">`은 `.orca-project-shell`의 직계 자식으로, 레일/메인 어느 쪽에도 속하지 않고 `position:fixed`로 항상 DOM에 존재한다. `orca-theme.css:1078`에서 `<768px`이면 무조건 `display:flex`이며, `board-workspace.js:454-459`의 `setMobileView()`가 `[채팅]`/`[작업]` 버튼 클릭에 반응해 `body.dataset.orcaMobileView`를 토글한다. **버튼 자체는 존재하고 동작한다** — 다만 (d)의 겹침 버그 때문에 채팅 화면에서는 이 세그먼트 컨트롤 바가 입력창 전송 버튼과 같은 자리에 겹쳐 보여, 사용자 입장에서는 "전송 버튼이 없다"와 "복귀 버튼이 이상한 위치에 있다"가 동시에 체감되는 상황 |

---

## 1. §2 목표 DOM 트리 대조

| 스펙 요소 | 판정 | 근거 file:line | 남은 작업 |
|---|---|---|---|
| `div.orca-project-shell` (flex 컨테이너) | 구현됨 | `templates/project.html:15`, `orca-theme.css:786-789` | 없음 |
| `aside#orca-chat-rail` + 하단 고정 입력 | 구현됨 | `templates/partials/chat_rail.html:11,28` | 없음 |
| 사이드바 내부 보조 탭(`orca-rail-tabs` 채팅/실행 로그) 유지 | 구현됨 | `templates/partials/chat_rail.html:20-23`, `chat-rail.js:113-129` | 없음 |
| `main.orca-project-main` → `header.orca-tabbar` + `div.orca-tab-panels` | 구현됨 | `templates/project.html:34-54` | 없음 |
| `.orca-tabbar-scroll` 안 `button.orca-tab[data-tab-id][data-tab-kind][aria-selected]` | 구현됨 | `board-workspace.js:93-118` (렌더), `templates/project.html:38-39` | 없음 |
| `.orca-tab-icon` / `.orca-tab-title` / `.orca-tab-status-dot[data-status]` / `.orca-tab-close` | 부분 구현 | `board-workspace.js:107-113` | 아이콘·제목·닫기 버튼은 있음. **`data-status` 속성은 없고 `class="status-${…}"` 만 있음** — 스펙 §5의 `data-tab-status` 요구사항과 어긋남(§4 표 참조) |
| `button.orca-tabbar-overflow[hidden]` (탭 넘침 드롭다운) | 미구현 | 코드 전역 `orca-tabbar-overflow` 0건 매치 | 대신 좌/우 스크롤 네비게이션 버튼(`orca-tabbar-navbtn`, `board-workspace.js:19-20,120-129`)으로 대체 구현됨 — 스펙이 요구한 "드롭다운" UX는 없음. 필요하면 드롭다운 신규 구현하거나 스펙을 스크롤 방식으로 갱신 |
| `section.orca-tab-panel[data-tab-id][data-tab-kind][hidden]` | 부분 구현 | `orca-theme.css:951-959`, `board-workspace.js:204-210` | 스펙은 `[hidden]` 속성 토글을 요구했지만 실제로는 `.active` 클래스 + `display:none/block` CSS로 구현됨(`.orca-rail-panel[hidden]` 패턴과 다름). 기능은 동등하지만 §5 네이밍 규칙과 불일치 |
| `kind="canvas"` → 기존 `#board` 서브트리 그대로 이식 | 구현됨 | `templates/project.html:49-53` — `#orca-tab-panel-canvas` 안에 기존 `#board` `hx-get` 그대로 위치 | 없음 |
| `kind="task"` → `partials/task_detail.html` 그대로 이식 | 구현됨 | `board-editor.js:369-396`(`selectTask`가 `/partials/task/{id}` fetch), `board-workspace.js:587-597`(`openTaskTab`) | 없음 |
| `kind="flow"` → vision-flow.js 카드/트리 뷰 | **미구현** | `vision-flow.js:23-26,154-166` — 여전히 `#board` 내부에 자체 `#orca-flow` 패널을 붙였다 뗐다 하는 **토글 방식**(스펙 §1의 "현재 상태" 그대로), `orca-tab-panels`/`orca-tabbar`와 전혀 연결 안 됨 | `ensureTab({kind:"flow", ...})` 호출 지점을 새로 만들어 vision-flow.js 콘텐츠를 독립 탭 패널로 이식해야 함. 현재 `board-workspace.js`에서 `"flow"`라는 문자열 리터럴이 전혀 등장하지 않음(코드 확인됨) |
| `kind="diff"` → "지금은 자리만" | 부분 구현(죽은 코드) | `board-workspace.js:149-202`(`renderRefTabContent`가 `artifact/diff/preview` kind를 처리하는 로직 자체는 존재) | **하지만 이 세 kind로 `ensureTab`을 호출하는 곳이 코드 전체에 0건** — 실제로는 아무도 diff/artifact/preview 탭을 열 수 없다. UI 진입점(버튼 등)이 없어 도달 불가능한 코드 |

---

## 2. §3 브레이크포인트 3단계 대조

| 구간 | 판정 | 근거 file:line | 남은 작업 |
|---|---|---|---|
| 데스크톱 `min-width:1024px` — 사이드바+메인 동시 표시, 리사이즈 핸들, 탭바 가로 | 구현됨 | `orca-theme.css:786-789`(`display:flex` 기본), `chat-rail.js:70-111`(리사이즈), `orca-theme.css:816-843`(탭바 가로) | 없음 |
| 태블릿 `768px~1023px` — 사이드바 오프캔버스, 메인 전체 폭, 탭바 데스크톱과 동일 | 구현됨 | `orca-theme.css:731-771`(오프캔버스 공통 로직, `max-width:1023px`), `orca-theme.css:776-783`(768~1023 전용 토글 위치 보정) | 스펙이 요구한 "768~1023 구간으로 재한정"은 실제로는 `max-width:1023px`(태블릿+모바일 공통) 블록과 `min-width:768px and max-width:1023px`(태블릿 전용 보정) 두 개로 나뉘어 구현됨 — 결과적으로 3단계 분리는 달성했으나 스펙 문서가 묘사한 것만큼 깔끔하게 분리되어 있진 않음(사소, 기능상 문제 없음) |
| 모바일 `<768px` — 채팅 기본 화면, 세그먼트 컨트롤로 탭 전환 | 부분 구현 | `orca-theme.css:1014-1142`, `templates/project.html:71-78`, `board-workspace.js:447-471` | 세그먼트 컨트롤·전체화면 전환 자체는 동작하지만 **(d)의 겹침 버그**로 채팅 입력창이 가려짐. 또한 스펙이 명시한 "하단 시트(작업 1개일 때) 또는 풀스크린 스와이프 스택(여러 개일 때)" 중 **하단 시트 모드는 구현되지 않음** — 현재는 항상 풀스크린 전환만 존재(`orca-mobile-segctl` 클릭 → `body[data-orca-mobile-view=workspace]`로 전체 전환, 탭 개수 무관) |
| 뒤로가기/스와이프-다운으로 채팅 복귀 | 부분 구현 | `board-workspace.js:483-493`(좌우 스와이프로 탭 전환은 있음) | **아래로 스와이프해서 채팅으로 복귀하는 제스처는 코드에 없음** — 오직 세그먼트 컨트롤의 `[채팅]` 버튼 탭으로만 복귀 가능. 브라우저 뒤로가기(popstate) 연동도 없음 |
| `task-detail-backdrop` 패턴을 탭 시트에도 재사용 | 미구현 | `templates/project.html:65`(`task-detail-backdrop`는 여전히 구버전 `#task-detail` 폴백 전용) | 탭 시트(`.orca-tab-sheet`) 자체가 없으므로 재사용할 대상도 없음 |

---

## 3. §4 탭 상태 모델·책임 분리 대조

### 탭 상태 모델 필드 대조

| 스펙 필드 | 판정 | 근거 file:line | 비고 |
|---|---|---|---|
| `id` | 구현됨 | `board-workspace.js:39,538,588`(`tabId`, `{kind}-{id}` 규칙 그대로: `"canvas"`, `"task-42"`) | 없음 |
| `title` | 구현됨 | `board-workspace.js:96-113,234-253` | 없음 |
| `kind` | 부분 구현 | `board-workspace.js:39` | 스펙 값(`canvas|task|flow|diff`)과 실제 코드 값(`workflow|task|chat|artifact|diff|preview`, `board-workspace.js:28`)이 다름 — 스펙의 `canvas`가 코드에서는 `workflow`로 쓰임, 스펙에 없던 `chat` kind가 아이콘 맵에 존재(미사용) |
| `source_message_id` | **미구현** | 코드 전체 0건 매치 | (b)에서 지적한 서버 컬럼 부재와 직결. `ensureTab()`(`board-workspace.js:234-253`)에 해당 파라미터 자체가 없음 |
| `status` | 구현됨 | `board-workspace.js:437-445`(`syncStatuses`), `board-editor.js:605` | 값 정규화(`STATUS_MAP`, `board-workspace.js:29-32`)가 스펙이 요구한 "기존 값 그대로 재사용" 원칙과 다르게 `queued→pending`, `error→error`로 새로 매핑함 |
| `active` | 부분 구현 | `board-workspace.js:216-232` | 별도 boolean 필드로 저장하지 않고 `activeTabId === tabId` 비교로 매번 파생시킴 — 기능적으로는 동일하나 스펙이 명시한 데이터 구조와 다름(사소) |

### 책임 분리 대조

| 책임 | 판정 | 근거 file:line | 남은 작업 |
|---|---|---|---|
| 탭 목록 상태 클라이언트 보관(`orca-tabs-{project_id}` localStorage) | 구현됨 | `board-workspace.js:26,78-86` | 없음 |
| 탭 콘텐츠 서버 partial 재사용(htmx 주입, 탭 자체는 재fetch 안 함) | 부분 구현 | canvas/task는 구현됨(§1 표 참조), flow/diff/artifact/preview는 미구현 또는 죽은 코드 | §1 참조 |
| "메시지가 태스크를 만들었다" 판별(서버, `created_task_id`) | **미구현** | (b) 참조 | `messages` 테이블에 컬럼 추가 + 태스크 생성 경로(`orchestrator.py`/`main.py`의 태스크 생성 지점)에서 채우는 마이그레이션 필요 |
| 탭 자동 오픈 트리거(클라이언트, `chat-rail.js`가 `created_task_id` 감지) | **미구현** | `chat-rail.js` 전체에서 `created_task_id`/`orca-tab-opened` 0건 매치. `chat-rail.js:221-241`의 `done` 핸들러는 `orca-refresh-board` 이벤트만 쏨(스펙이 이미 §1에서 "탭 열기가 아니라 새로고침 트리거일 뿐"이라 지적한 상태 그대로 유지됨) | 서버 스키마(b) 선행 후, `done` 핸들러에서 응답의 `created_task_id`를 읽어 `OrcaWorkspace.openTaskTab()` 호출하는 코드 추가 필요 |
| 탭 닫기/전환/재정렬(클라이언트 전용) | 구현됨 | `board-workspace.js:255-304`(닫기), `216-232`(전환), `282-292`(재정렬 D&D) | 없음 |
| 상태 배지 갱신(2초 폴링 → 탭 status dot) | 구현됨 | `board-editor.js:605`(`OrcaWorkspace.syncStatuses(tasks)` 매 폴링마다 호출), `board-workspace.js:437-445` | 없음 |

---

## 4. §5 네이밍 규칙 대조

### CSS 클래스

| 스펙 클래스 | 판정 | 근거 |
|---|---|---|
| `.orca-tabbar` | 구현됨 | `orca-theme.css:816` |
| `.orca-tabbar-scroll` | 구현됨 | `orca-theme.css:835` |
| `.orca-tab` (기존 `.orca-rail-tab`과 구분) | 구현됨 | `orca-theme.css:845`, `.orca-rail-tab`은 `chat_rail.html:21-22`에서 별도로 계속 사용됨 — 혼용 없음 확인 |
| `.orca-tab.active` + `aria-selected` 병행 | 구현됨 | `board-workspace.js:98,102` |
| `.orca-tab-close` | 구현됨 | `orca-theme.css:898`, `board-workspace.js:112` |
| `.orca-tab-status-dot` + `status-${…}` | 부분 구현 | `orca-theme.css:877-886` — 클래스명 패턴은 맞으나 값 집합이 재매핑됨(§4 표 참조) |
| `.orca-tab-panels` | 구현됨 | `orca-theme.css:946` |
| `.orca-tab-panel` (`[hidden]` 패턴) | 부분 구현 | `[hidden]` 속성 대신 `.active` 클래스 사용(§1 표 참조) |
| `.orca-tabbar-overflow` | **미구현** | 0건 매치, `.orca-tabbar-navbtn`(prev/next 스크롤 버튼)으로 대체 |
| `.orca-segment` / `.orca-segment-btn` | **미구현** | 0건 매치, 실제로는 `.orca-mobile-segctl` / `.orca-mobile-segctl-btn`이라는 다른 이름으로 구현됨(`templates/project.html:71-77`, `orca-theme.css:1078-1090`) — 기능은 있으나 스펙이 지정한 클래스명과 다름 |
| `.orca-tab-sheet` | **미구현** | 0건 매치, 모바일 하단 시트 자체가 없음(§2 표 참조) |

### 데이터 속성

| 스펙 속성 | 판정 | 근거 |
|---|---|---|
| `data-tab-id="{kind}-{source_id}"` | 구현됨 | `board-workspace.js:100,141` |
| `data-tab-kind="canvas\|task\|flow\|diff"` | 부분 구현 | `board-workspace.js:142`, 값이 `workflow/task/artifact/diff/preview`로 스펙과 다름(§4 참조) |
| `data-tab-status="pending\|queued\|running\|done\|failed"` | **미구현** | 0건 매치 — 상태는 클래스로만 표현(`class="status-${…}"`), `data-tab-status` 속성 자체가 DOM에 없음 |
| `data-source-message-id="{message_id}"` | **미구현** | 0건 매치 — 필드가 애초에 존재하지 않으므로 당연히 속성도 없음 |
| `data-tab-close` | 구현됨 | `board-workspace.js:112,499` |

### 커스텀 이벤트

| 스펙 이벤트 | 판정 | 근거 |
|---|---|---|
| `orca-tab-opened` | **미구현** | 0건 매치 — `ensureTab()`(`board-workspace.js:234-253`)이 새 탭을 만들 때 아무 이벤트도 쏘지 않음 |
| `orca-tab-closed` | 구현됨 | `board-workspace.js:268-270`(발행), `board-editor.js:418-426`(구독) |
| `orca-tab-activated` | 구현됨 | `board-workspace.js:227-231`(발행), `board-editor.js:360-367,428-446`(구독) |

### 서버측 스키마

| 스펙 요구 | 판정 | 근거 |
|---|---|---|
| `messages.created_task_id INTEGER REFERENCES tasks(id)` | **미구현** | (b) 참조 |

---

## 5. 종합 — 남은 작업 목록 (우선순위순)

1. **(d) 모바일 전송 버튼 겹침 버그 수정** — `orca-theme.css`의 `<768px` 블록에서 `.orca-chat-rail`(또는 `.orca-rail-composer`)에 `.orca-project-main`과 동일하게 `padding-bottom: calc(56px + env(safe-area-inset-bottom))` 상당의 보정을 추가. 사용자가 실제로 겪은 "전송 버튼이 안 보인다" 증상의 직접 원인이며 수정 범위가 가장 작음
2. **`messages.created_task_id` 컬럼 + 채우는 코드 경로 추가** — (b)/§4 "탭 자동 오픈"의 전제 조건. 이게 없으면 채팅→탭 자동 생성 자체가 불가능
3. **`chat-rail.js`의 `done` 핸들러에서 `created_task_id`를 읽어 탭 오픈 트리거 발행** — 2번 완료 후 진행
4. **`kind="flow"` 탭 이식** — `vision-flow.js`를 `orca-tab-panels`에 독립 탭으로 연결(현재는 `#board` 내부 토글로 남아 스펙 §2와 어긋남)
5. **모바일 하단 시트(`.orca-tab-sheet`) 구현** — 현재 전체화면 전환만 있고 스펙이 요구한 "탭 1개일 때 하단 시트" 분기가 없음
6. **`orca-tab-opened` 이벤트 발행 + `data-tab-status`/`data-source-message-id` 속성 추가** — §5 네이밍 규칙 정합화(기능 영향은 적으나 후속 작업이 스펙을 신뢰하고 붙일 수 있으려면 필요)
7. **`.orca-tabbar-overflow` 드롭다운 vs 현재 스크롤 방식, `.orca-segment` vs `.orca-mobile-segctl` 등 네이밍 불일치** — 기능은 동작하므로 우선순위 낮음. 스펙 문서를 실제 구현에 맞게 갱신하거나, 코드를 스펙 클래스명으로 리네임할지 결정 필요
8. **artifact/diff/preview 탭 죽은 코드 정리 또는 진입점 추가** — `renderRefTabContent`(`board-workspace.js:149-202`)를 실제로 호출할 UI(예: task 탭 안 "산출물 새 탭으로 열기" 버튼)를 추가하거나, 당장 안 쓸 거면 미완성 상태임을 코드 주석에 명시
