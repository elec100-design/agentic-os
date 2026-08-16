# 설계: PC 화면 정리 — 좌측 사이드바 폭 + 대화창 첨부·작업 위치

## 배경

PC(≥901px) 홈 화면에서 세 가지가 걸린다(사용자 원문):

1. **좌측 사이드바의 크기가 부적절하다** — 에이전트 이름·슬러그가 잘리고
   (`Researcher @researc`, `Chief @chief-of-agen`), 목록 아래로 빈 공간이 넓게 남는다.
2. **에이전트 대화창(DM)·채널창에 파일 첨부 기능이 없다.**
3. **에이전트 대화창(DM)·채널창에 폴더/리포(작업 위치) 선택 기능이 없다.**

우측 레일의 작업 채팅·비전보드 채팅은 셋 다 갖고 있다(`partials/composer_controls.html`).
즉 **대화의 주 무대가 중앙 탭(채널·DM)으로 옮겨갔는데 컴포저 기능은 따라오지 않은** 상태다.

---

## 0. 현행 구조에서 반드시 알고 갈 것

- **좌측 사이드바는 데스크톱에서 고정 200px** — `static/orca-theme.css:1408-1414`
  (`body.orca-home .sidebar { flex: 0 0 200px; width: 200px }`). 사용자가 조절할 수단이 없다.
  반면 **우측 레일은 드래그로 280~560px 조절 + localStorage 기억**
  (`static/home.js:672,728-750`, 핸들 `#home-rail-resize`). 좌우 비대칭이 지금의 인상을 만든다.
- **잘림의 직접 원인은 폭 하나가 아니다** — `.rail-preview { max-width: 13rem }`
  (`static/style.css:1774-1781`)는 208px로 **사이드바 내부 폭(200px − 좌우 패딩 ≈ 168px)보다 넓고**,
  `.rail-title`은 flex + `.rail-slug{white-space:nowrap}`(`:1759-1773`)이라 **슬러그가 자리를
  먼저 가져가고 이름이 잘린다.** 폭만 늘리면 증상만 옮겨간다.
- **목록 미리보기는 원문 마크다운 그대로** — `app/main.py:460-475 _preview()`가 공백만
  정규화해서 `안녕하세요. 저는 **Ch…`처럼 별표가 노출된다.
- **대화 화면은 한 벌을 공유한다** — `templates/partials/channel_view.html` 하나를
  전용 페이지(`/channels/{id}` → `channel.html`)와 홈 중앙 탭이 같이 쓴다
  (`static/home.js:133-146`이 `/partials/channel/{id}` + `mountChannelView`).
  **여기를 고치면 두 화면이 동시에 고쳐진다.**
- **그 대화창 컴포저에는 컨트롤이 거의 없다** — `channel_view.html:64-84`는 textarea +
  에이전트 select + 전송 버튼이 전부. 첨부·작업위치·모델·타임아웃이 없다.
- **채널 메시지 API는 JSON 전용** — `MessageCreate{body, provider, model, parent_id}`
  (`app/main.py:791-795`), `POST /api/channels/{id}/messages`(`:952`). 파일이 들어갈 자리가 없다.
  파일 저장 로직은 `_save_uploads()`(`app/main.py:651-668`)에 이미 있고 `POST /jobs`만 쓴다.
- **작업 위치는 채널 생성 시점에만 정할 수 있다** — `channel_new.html:45-50`의 select.
  `ChannelUpdate`(`app/main.py:780-784`)에 `workdir`가 없어 **만든 뒤에는 바꿀 방법이 없다.**
  DM 채널은 에이전트의 workdir을 복사해서 생성된다(`app/db.py:1024-1025`).
- **실행 시 workdir 우선순위는 에이전트 > 채널** — `app/agents.py:364`
  (`agent["workdir"] or channel["workdir"]`). 그래서 대화창에서 폴더를 바꿔도
  에이전트에 workdir이 박혀 있으면 무시된다. 사용자가 그 자리에서 고른 값이 이겨야 한다.
- **컴포저는 이미 폼 단위로 마운트된다** — `mountComposer(form)`(`static/app.js:101`)은
  전부 class로 요소를 찾고, 홈은 컴포저 2개를 이렇게 돌리고 있다. 다만
  `document.querySelectorAll("form.composer").forEach(mountComposer)`(`:382`)는 **로드 시 1회만**
  실행되므로, 탭으로 나중에 붙는 대화창은 **명시적으로 마운트**해야 한다.
- **`channel.html`은 `app.js`도, 작업위치 모달(`#ws-modal`)도 불러오지 않는다**
  (`channel.html:37-42` vs `index.html:194-206,215`). 전용 페이지에서도 같은 컴포저를 쓰려면
  둘 다 실어야 한다.
- 드래그 앤 드롭 첨부는 `mountComposer` 안에 폼 단위로 이미 구현돼 있다(`app.js:144-162`).
  **마운트만 하면 따라온다.**

---

## 1. 목표 화면

```
┌────────────────────┬──────────────────────────────┬──────────────────┐
│ 사이드바 240px     │ 중앙 탭 (대화·작업·비전보드) │ 우측 레일        │
│ ↔ 드래그 180–360   │                              │ ↔ 280–560 (기존) │
│ ⇤ 접기 64px(아이콘)│  ┌─ #채널 / @DM ──────────┐  │                  │
│                    │  │ 헤더: 멤버 · 📁작업위치│  │                  │
│ 에이전트           │  │ 대화 …                 │  │                  │
│ 채널               │  │ ┌ 컴포저 ────────────┐ │  │                  │
│                    │  │ │ ＋ 자동 모델 📁폴더│ │  │                  │
│ ─────────          │  │ └────────────────────┘ │  │                  │
│ ⚙︎ 설정 · EN · ☾   │  └────────────────────────┘  │                  │
└────────────────────┴──────────────────────────────┴──────────────────┘
```

- 좌측은 **폭을 사용자가 정하고 기억한다**(우측 레일과 같은 조작감).
- 대화창 컴포저는 **우측 작업 채팅과 같은 컨트롤 한 벌**을 갖는다.
- 작업 위치는 **채널 기본값(헤더)** 과 **이번 발화 한정 지정(컴포저)** 두 층으로 나눈다.

---

## 2. A단계 — 좌측 사이드바 크기 (CSS/JS만, 서버 변경 없음)

**A-1. 폭을 변수화하고 드래그 가능하게**

- `static/orca-theme.css:1408-1414`의 고정 200px을 CSS 변수로:
  `body.orca-home { --aos-side-w: 240px }` → `.sidebar { flex: 0 0 var(--aos-side-w); width: var(--aos-side-w) }`.
  기본값을 200 → **240px**로 올린다(이름 + 슬러그가 한 줄에 들어가는 최소 폭).
- `templates/index.html`의 `<aside class="sidebar">` 끝에 우측 레일과 같은 핸들
  `<div class="side-resize-handle" id="home-side-resize" role="separator" …>`를 추가.
- `static/home.js`에 `#home-rail-resize` 로직(`:733-752`)을 좌측 방향으로 대칭 복제:
  범위 **180–360px**, `localStorage["aos-home-side-width"]`에 기억, `≤900px`(오프캔버스)에서는 비활성.
  → 우측 레일 로직과 겹치므로 `mountResizer({el, handle, min, max, key, dir})` 하나로 뽑고
  좌우가 함께 쓰게 정리한다(순 코드 증가 최소화).

**A-2. 접기(아이콘 전용) 모드**

- `.sidebar.is-collapsed { --aos-side-w: 64px }` — 아바타/해시만 남기고 `.rail-body`,
  `.side-settings` 라벨, `h2`를 숨긴다. 브랜드 줄의 되접기 버튼(`#nav-close`,
  현재 ≤900px 전용 `style.css:120-134`)을 데스크톱에서도 노출해 토글로 쓰고
  상태는 `localStorage["aos-home-side-collapsed"]`에 남긴다(우측 레일 `LS_RAIL_COLLAPSED`와 동형).
- 접힘 상태에서는 `title` 속성으로 이름을 보여준다(툴팁).

**A-3. 잘림·빈 공간 정리 (실제 증상)**

- `.rail-preview`의 `max-width: 13rem` **삭제** → `min-width:0` + `flex:1`로 컨테이너에 맞춰 말줄임
  (`style.css:1774-1781`).
- `.rail-title`: 이름에 `overflow:hidden; text-overflow:ellipsis`를 주고 슬러그는
  `flex:none` + **폭이 좁을 때 숨김**(컨테이너 쿼리 대신 `.sidebar` 폭 변수 기반 클래스,
  또는 `@container` 지원 전제 시 `@container (max-width: 220px) { .rail-slug { display:none } }`).
  → **이름이 먼저, 슬러그는 여유가 있을 때만.**
- `.side-scroll`(`style.css:1693`)이 남는 높이를 다 먹으므로 빈 공간 자체는 정상이지만,
  마지막 그룹 아래 `padding-bottom`을 줄이고 `.side-foot`(`:1092-1100`) 위 구분선을 살려
  "비어 보이는" 인상을 줄인다. 목록이 짧을 때 하단 여백이 과하지 않도록
  `.rail-group:last-child { margin-bottom: .4rem }`.

**A-4. 미리보기 문자열 정리 (서버 1곳)**

- `app/main.py:460-475 _preview()`에 마크다운 최소 스트립 추가:
  `**`/`__`/`` ` ``/`#`/`>` 접두, 링크 `[텍스트](url)` → `텍스트`. 60자 컷은 유지.

**검증**: `tests/test_home_layout.py`에 `id="home-side-resize"`·`--aos-side-w` 존재 확인,
`tests/test_agent_rail.py`에 `max-width: 13rem`이 사라졌는지, `_preview` 마크다운 스트립
단위 테스트 추가. `tests/test_responsive_breakpoints.py`의 ≤900px 규칙은 건드리지 않는다.

---

## 3. B단계 — 대화창 파일 첨부

**B-1. 컴포저 마크업 교체 (공유 조각 하나)**

- `templates/partials/channel_view.html:64-84`의 폼을 `composer_controls.html`을 include하는
  형태로 바꾼다: `{% with ids = false, hint = false, send_label = '전송' %}`.
- 조각에 파라미터 하나 추가: `show_agent`(기본 true) — **DM은 상대가 정해져 있으므로**
  에이전트/모델 칩을 감추고(현행 동작 유지) 첨부·작업위치만 남긴다.
- 채널(비DM)의 기존 `select.new-thread-provider`는 컴포저의 에이전트 칩으로 대체
  → `channels.js:397~`의 제출 코드가 `.composer-provider` / `.composer-model` hidden input을
  읽도록 변경.

**B-2. 마운트 (양쪽 화면)**

- `static/channels.js`의 `mountChannelView(root)` 안에서 `window.mountComposer?.(form)` 호출,
  `dispose()` 시 정리. (`app.js:382`은 최초 1회만 돌기 때문 — 탭으로 열리는 대화창은 여기서 붙는다.)
- `templates/channel.html`에 `app.js`와 `#ws-modal` 마크업(`index.html:194-206`)을 추가.
  모달 마크업은 `partials/ws_modal.html`로 뽑아 `index.html`·`channel.html`이 함께 include.

**B-3. 서버: 첨부를 메시지에 싣는다**

두 안 중 **② 선업로드안**을 택한다.

| | ① 메시지 API를 multipart로 | ② 업로드 엔드포인트 분리 (**채택**) |
|---|---|---|
| 변경 범위 | `api_create_channel_message` 시그니처 전면 | 새 라우트 1개 + 필드 1개 |
| 프런트 | `channels.js`의 JSON·낙관적 렌더링 흐름을 다시 씀 | 그대로 유지(업로드 후 경로만 실음) |
| 재사용 | 쓰레드 답글엔 또 손봐야 함 | 답글·후속 지시에도 그대로 |

- `POST /api/uploads` (multipart, `files: list[UploadFile]`) → `_save_uploads()` 재사용,
  `[{name, path, size}]` 반환. 크기 제한은 기존 `config.MAX_UPLOAD_MB`.
- `MessageCreate`에 `attachments: list[str] = []` 추가. **서버는 경로가
  `config.UPLOAD_DIR` 하위인지 반드시 재검증한다**(임의 경로 주입 차단 — `workspace.valid_path`와 같은 취지).
- DB: `messages`에 `attachments TEXT`(JSON) 컬럼 마이그레이션 추가(`app/db.py`의 기존 마이그레이션 패턴).
- 프롬프트 주입: `POST /jobs`의
  `"첨부 파일 (로컬 경로에서 읽을 것):"` 블록(`app/main.py:739-743`)을
  `attachments_block(paths)` 헬퍼로 뽑아 `agents.build_thread_prompt()`(`app/agents.py:259`)에서도
  같은 문구로 쓴다 — 에이전트가 보는 형식이 화면마다 달라지지 않게.
- 렌더: `channels.js`의 `buildMessageEl`(`:53`)에 첨부 칩 줄 추가(파일명만, 클릭 시 경로 복사).

**검증**: `tests/test_channel_composer.py` 신설 —
`test_vision_composer_controls.py` 패턴 그대로(렌더 HTML에 컨트롤 class 존재, id 중복 없음),
API 테스트로 업로드 → 메시지 생성 → 생성된 job prompt에 첨부 경로가 들어갔는지,
UPLOAD_DIR 밖 경로가 400으로 거부되는지.

---

## 4. C단계 — 폴더 / GitHub 리포(작업 위치) 선택

**C-1. 채널 기본 작업 위치 (헤더)**

- `channel_view.html:27`의 정적 표시(`📁 작업 위치: <code>…</code>`)를
  **workspace-picker 칩**으로 교체(`partials/workspaces.html` 재사용).
  ＋ 버튼은 기존 폴더 탐색/GitHub 리포 모달을 그대로 연다(`app.js:460-638`).
- 선택 시 `PATCH /api/channels/{id}` 로 저장 → **`ChannelUpdate`에 `workdir: str | None` 추가**
  (`app/main.py:780-784`), 서버에서 `workspace.valid_path()` 통과한 경로만 수용
  (`api_create_channel_message`의 기존 검증 `:997-999`과 동일 규칙).
- DM에도 동일하게 적용 — 지금은 에이전트 workdir을 복사만 하고(`db.py:1024`) 이후 바꿀 길이 없다.

**C-2. 이번 발화 한정 지정 (컴포저)**

- B-1로 컴포저에 이미 workspace-picker가 들어온다. 초기값은 채널의 `workdir`,
  바꾸면 **그 메시지에만** 적용.
- `MessageCreate`에 `workdir: str | None` 추가 → `api_create_channel_message`가 검증 후
  `agents.spawn`으로 전달.
- **우선순위를 바꾼다**: `app/agents.py:364`를
  `요청 workdir > 에이전트 고정 workdir > 채널 workdir` 로.
  (지금은 에이전트가 채널을 이기므로 화면에서 고른 폴더가 조용히 무시된다 — 이번 수정의 핵심 버그.)
  에이전트에 workdir이 박혀 있는데 다른 값을 고른 경우, 실행 트레이스/메시지 메타에
  "이번 실행 위치: …"를 남겨 무엇이 이겼는지 보이게 한다.

**검증**: `tests/test_channels.py`에 workdir override 라우팅 테스트
(에이전트 workdir이 있어도 메시지 workdir이 이기는지, 미등록 경로는 거부되는지),
`ChannelUpdate.workdir` PATCH 테스트.

---

## 5. 작업 순서와 범위

| 순서 | 내용 | 성격 | 위험 |
|---|---|---|---|
| 1 | A단계 (사이드바 폭·접기·잘림·미리보기) | CSS/JS + 서버 1함수 | 낮음 |
| 2 | C-1 (채널 헤더 작업 위치 칩 + PATCH workdir) | 서버 필드 1개 | 낮음 |
| 3 | B (컴포저 이식 + 업로드 API + attachments 컬럼) | 마이그레이션 포함 | 중간 |
| 4 | C-2 (메시지 단위 workdir + 우선순위 교정) | 실행 경로 변경 | 중간 |

각 단계는 독립 커밋. 3단계는 DB 마이그레이션이 있으므로 단독 커밋으로 분리한다.

## 6. 하지 않는 것 (이번 범위 밖)

- 모바일(≤900px) 레이아웃 변경 — 오프캔버스 서랍 동작은 그대로 둔다.
- 우측 레일 폭·탭 구성 변경.
- 첨부 파일 미리보기(이미지 썸네일)·삭제 UI — 칩 표시까지만.
- 에이전트 설정 화면의 workdir 개념 자체 재설계.

## 7. 열린 질문 → 확정된 값

구현 시점에 아래 기본값으로 확정했다(바꾸고 싶으면 각각 한 줄이다).

1. 좌측 사이드바 기본 폭 = **240px** (드래그 180~360px, `--aos-side-w`).
2. 접기 = **아이콘 전용 64px** (`.sidebar.is-collapsed`).
3. 첨부 = 지금처럼 **`data/uploads/`에 두고 절대경로만 전달**(복사하지 않는다).

## 8. 구현 결과 (계획과 달라진 점)

- **작업 위치를 두 층으로 나누지 않았다.** 헤더에 별도 피커를 두는 대신 **컴포저의
  폴더 칩 하나**로 통일하고, 고른 값을 그 방의 기본값으로 함께 저장한다
  (`PATCH /api/channels/{id}` + 그 발화의 `workdir`). app.js의 작업위치 조작이 전부
  `closest("form.composer")` 기준이라 폼 밖의 피커는 배선이 따로 필요했고, 고르는 곳이
  두 군데면 어느 쪽이 이기는지도 불분명해진다. 헤더의 `📁 작업 위치` 줄은 **현재 값을
  보여주는 표시**로 남아 칩을 바꾸면 따라 바뀐다.
- **`app/attachments.py`를 새로 뒀다.** 프롬프트 블록 형식(`첨부 파일 (로컬 경로에서
  읽을 것):`)을 main과 agents가 함께 쓰는데, agents가 main을 import하면 순환이다.
- **대화창 컴포저에서는 '메모리 첨부·타임아웃'을 감췄다**(`extra_tools=false`).
  둘은 폼 제출(`POST /jobs`)로만 전달되는데 대화창은 JSON으로 보내므로 받는 쪽이 없다.
  → 남은 일: 메시지 API에 `attach_memory`/`timeout_min`을 받아 다시 노출하기.
- **덤으로 고친 것**: `board-editor.js`가 홈에서 탭을 열 때마다
  `window.OrcaWorkspace.getTaskPanelId`로 예외를 던지고 있었다(`board-workspace.js`는
  프로젝트 페이지에만 실린다). 옵셔널 체이닝으로 막았다.
- 사이드바 폭 조절 핸들은 우측 레일처럼 `-3px`로 내밀면 `.sidebar { overflow: hidden }`에
  잘려 잡히지 않는다 — 안쪽 가장자리(`right: 0`)에 붙였다.

### 확인한 것 (Chromium 실사용)

폭 드래그 240→340→180(클램프)·`is-narrow`에서 슬러그 접힘·64px 접기·새로고침 후 유지,
대화창에서 폴더 선택 → 파일 첨부 → 전송 → 말풍선 첨부 칩(원래 파일명) → 헤더 작업 위치
갱신 → 잡의 `workdir`가 고른 폴더로 저장되는 것까지 한 번에 확인했다.
