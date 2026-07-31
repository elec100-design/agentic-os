# 설계: 취소된 비전보드 삭제 + 다이어그램 편집 확장

조사 범위: `app/main.py`, `app/orchestrator.py`, `app/db.py`, `app/models.py`,
`templates/board.html`, `templates/project.html`, `templates/partials/projects.html`,
`templates/partials/board.html`, `templates/partials/task_detail.html`,
`static/board-editor.js`, `tests/test_board_routes.py`.

용어: 이 코드베이스에서 "비전 보드"의 단위는 **project** (`projects` 테이블).
보드 목록(`/board`)은 project 카드 목록이고, project 상세(`/projects/{id}`)의
캔버스가 "워크플로우 다이어그램"(n8n식 태스크 그래프)이다.

---

## 1. 현행 코드 조사

### 1.1 상태 모델 · 취소 경로

- `app/db.py:34` — `projects` 테이블. `status` 컬럼: 자유 문자열
  (`planning`, `plan_ready`, `plan_failed`, `running`, `paused`, `done`,
  `failed`, `cancelled` — enum 제약 없음, 표시 라벨은
  `templates/partials/projects.html`의 `STATUS` 매핑에서만 정의).
- `app/orchestrator.py:572` `cancel_project(conn, project_id, _status="cancelled")`
  — 실행 중 태스크·계획 잡을 강제 종료(`db.update_job(..., status="failed")`,
  `worker.terminate_job_procs`)하고 `projects.status`를 `cancelled`로 갱신.
  `replan()`이 재사용하며 이때만 `_status="paused"`로 호출.
- `app/main.py:1304` `POST /projects/{project_id}/cancel` → `orchestrator.cancel_project`.
- `app/main.py:1309` `POST /projects/{project_id}/delete` (**이미 존재하는 단건 하드 삭제**):
  ```python
  orchestrator.cancel_project(conn, project_id)   # 실행 중이면 먼저 정지
  db.delete_project(conn, project_id)              # DB 로우 삭제
  shutil.rmtree(artifacts_dir, ignore_errors=True) # 산출물 폴더 삭제
  ```
- `app/db.py:371` `delete_project(conn, project_id)` — `jobs`(태스크가 만든 잡 +
  계획 잡), `tasks`, `board_tabs`, `projects` 로우를 순서대로 하드 삭제. 스키마
  변경 없이 이미 캐스케이드 삭제를 수행한다.
- `app/db.py:351` `list_projects(conn, limit=50)` — 상태 필터 없음, 전체를
  `id DESC`로 반환. **상태별 필터 조회 함수가 없다** (신규 필요).
- UI 진입점: `templates/project.html:31` 상세 페이지에만 삭제 버튼(폼 +
  `confirm()`)이 있다. **목록 카드(`partials/projects.html`)에는 취소/삭제
  버튼이 없다** — 사용자가 취소된 프로젝트를 지우려면 카드를 열어 상세
  페이지까지 들어가야 한다.
- **일괄 삭제("취소된 항목 모두 비우기") 엔드포인트는 존재하지 않는다.**

### 1.2 워크플로우 다이어그램 에디터

캔버스는 `partials/board.html`(그래프 SVG) + `partials/task_detail.html`
(선택한 노드의 사이드 패널) 조합이며, 모든 편집 액션은 htmx/fetch로
`#board`를 통째로 교체해 팬/줌 상태를 보존한다 (`app/main.py:1327` `_edit_action`).

편집 가능 상태: `orchestrator.EDITABLE_PROJECT_STATUSES = ("plan_ready", "paused")`
(`app/orchestrator.py:599`) — 진행 중이면 오케스트레이터 루프와 경합하므로 잠금.

기존 엔드포인트 (모두 `app/main.py`):

| 기능 | 라우트 | 처리 함수 |
|---|---|---|
| 태스크 편집(제목/설명/종류/에이전트) | `POST /tasks/{id}/edit` (1343) | `orchestrator.update_task_fields` (640) |
| 의존성(화살표) 전체 교체 | `POST /tasks/{id}/deps` (1364) | `orchestrator.set_task_deps` (656) |
| 노드 좌표 이동 | `POST /tasks/{id}/move` (1380) | `orchestrator.move_task` (723) |
| 태스크 삭제 | `POST /tasks/{id}/delete` (1390) | `orchestrator.delete_task` (711) |
| 태스크 추가 | `POST /projects/{id}/tasks` (1399) | `orchestrator.add_task` (679) |
| 재배치 | `POST /projects/{id}/relayout` (1428) | `orchestrator.reset_layout` (732) |
| 실패/오류 태스크 조치(모델 교체+추가 지시) | `POST /tasks/{id}/retry` (1438) | `orchestrator.retry_task` (494) |

**화살표(의존성) 추가/삭제는 이미 완전히 구현되어 있다**:
- 캔버스 드래그로 연결: `static/board-editor.js:772` `connect()` / `:784`
  `disconnect()` — 둘 다 `/tasks/{id}/deps`를 호출.
- 사이드 패널의 체크박스 폼(`task_detail.html:87` `.task-deps`)도 같은
  엔드포인트를 호출 — 터치 환경 대안.
- `orchestrator.set_task_deps` (656)가 자기참조 금지·순환 금지를 검증.

**태스크 노드 편집(제목/설명)도 이미 있다** — `task_detail.html:50` `.task-edit`
폼 + `update_task_fields`.

**갭 (실제로 없는 것)**:
1. **세부 계획/추가 지시 편집** — `tasks.extra_instruction` 컬럼은 이미 존재하고
   실패 조치 패널(`retry`, 1438)에서만 편집 가능하다. `plan_ready/paused`
   상태의 정규 편집 폼(`/tasks/{id}/edit`)에는 이 필드가 없다.
2. **태스크별 사용 모델 선택** — `tasks.model` 컬럼은 이미 존재하고
   `retry_task`(494)를 통해서만 채워진다(실패/완료 태스크 재실행 시). 정규
   편집 폼에는 모델 select가 없다 — 에이전트(provider)만 고를 수 있고,
   그 안의 구체 모델(예: Claude sonnet/opus, gemini pro/flash)은 고를 수 없다.
   모델 목록 자체는 `app/models.py:get_provider_models()`가 제공하며,
   `task_detail.html:120`이 이미 `provider_models` JSON을 페이지에 심어 두고
   `board-editor.js:492 syncRecoverModels()`가 에이전트 select 변경에 맞춰
   모델 select를 다시 채우는 패턴을 갖고 있다 — **이 패턴을 재사용**하면 된다.
3. `is_valid_model(provider, model)` (`app/models.py:527`)이 검증 함수로 이미 존재.

---

## 2. 기능 설계

### 2.1 취소된 비전보드 내역 삭제

#### (a) 단건 삭제 — 목록에서 바로

기존 `POST /projects/{project_id}/delete` 엔드포인트를 그대로 재사용한다
(스펙·동작 변경 없음). 변경은 프론트 전용:

- `templates/partials/projects.html`의 카드에 `status == 'cancelled'`일 때만
  삭제 버튼 노출:
  ```html
  {% if p['status'] == 'cancelled' %}
  <form method="post" action="/projects/{{ p['id'] }}/delete" class="inline-form"
        onclick="event.stopPropagation()"
        hx-post="/projects/{{ p['id'] }}/delete"
        hx-target="#projects" hx-select="#projects" hx-swap="outerHTML"
        hx-confirm="{{ '이 프로젝트를 삭제할까요? 되돌릴 수 없습니다.' | t }}">
    <button type="button" class="btn-ghost btn-del" onclick="event.stopPropagation()">{{ '삭제' | t }}</button>
  </form>
  {% endif %}
  ```
  카드 자체가 `<a>`라서 버튼 클릭이 링크 네비게이션과 겹치지 않도록
  `stopPropagation` 필요 (기존 코드 컨벤션에 이런 중첩 클릭 케이스가 없어
  신규 처리 필요).
- 현재 `/projects/{id}/delete`는 303 리다이렉트만 반환(`RedirectResponse`,
  htmx 폴백 없음). htmx에서 쓰려면 응답을 `partials/projects.html` 조각으로
  바꾸거나, `hx-boost`/`HX-Redirect` 헤더 처리 중 하나를 선택해야 한다.
  **권장**: 기존 라우트의 계약을 안 건드리기 위해, 응답에
  `HX-Redirect: /board` 대신 요청 헤더로 htmx 여부를 감지해 분기하지 말고,
  **별도 partial 갱신 없이 그냥 페이지 새로고침(`hx-boost` 미사용, 기존
  form 그대로 두고 `onsubmit=confirm(...)`)** 방식이 기존 상세 페이지
  삭제 버튼(`project.html:31`)과 동일한 패턴이라 더 안전하고 일관적이다.
  즉 htmx 속성 없이 표준 폼 제출 + 303 리다이렉트로 `/board`.

#### (b) 취소된 항목 모두 비우기 (일괄 삭제)

**DB 변경**: 불필요. `db.delete_project`가 이미 캐스케이드 삭제를 한다.

**신규 함수 (`app/db.py`)**:
```python
def list_projects_by_status(conn, status):
    return conn.execute(
        "SELECT * FROM projects WHERE status = ? ORDER BY id DESC", (status,)
    ).fetchall()
```

**신규 엔드포인트 (`app/main.py`, `/projects/{id}/delete` 근처)**:
```python
@app.post("/projects/cancelled/clear")
def clear_cancelled_projects():
    conn = db.get_conn()
    for p in db.list_projects_by_status(conn, "cancelled"):
        db.delete_project(conn, p["id"])
        artifacts = config.ARTIFACTS_DIR / str(p["id"])
        if artifacts.is_dir():
            import shutil
            shutil.rmtree(artifacts, ignore_errors=True)
    return RedirectResponse("/board", status_code=303)
```
- 라우트를 `/projects/cancelled/clear`로 둔 이유: `/projects/{project_id}/...`
  패턴과 경로 충돌 없이 (FastAPI는 정적 경로를 먼저 매치하지 않으므로,
  등록 순서상 이 라우트를 `{project_id}` 관련 라우트들보다 **먼저** 선언해야
  `"cancelled"`가 `project_id: int`로 잘못 파싱되는 걸 피한다 — 실제로는
  이 라우트에 `project_id` 파라미터가 없어 별도 함수이므로 경로 프리픽스만
  다르면 되지만, 혼동 방지를 위해 `/projects/{project_id}/delete` 정의
  이전에 두는 걸 권장).
- 각 프로젝트가 이미 `cancelled`이므로 `orchestrator.cancel_project` 재호출은
  불필요(실행 중 잡이 없는 상태). 바로 `db.delete_project` 호출.

**프론트 (`templates/partials/projects.html`)**:
- 프로젝트 헤더 옆에 취소 상태 카드가 1개 이상 있을 때만 버튼 노출:
  ```html
  <h2>{{ '프로젝트' | t }}</h2>
  {% if projects | selectattr('status', 'equalto', 'cancelled') | list %}
  <form method="post" action="/projects/cancelled/clear" class="inline-form"
        onsubmit="return confirm(t('취소된 프로젝트를 모두 삭제할까요? 되돌릴 수 없습니다.'))">
    <button class="btn-ghost btn-del">{{ '취소된 항목 모두 비우기' | t }}</button>
  </form>
  {% endif %}
  ```
- `board.html:84`의 `hx-trigger="load, every 5s"` 폴링이 알아서 목록을
  갱신하므로, 표준 폼 제출(303 → `/board` 전체 리로드) 후에도 5초 내 자연
  반영된다. htmx 부분 갱신을 원하면 `hx-post` + `hx-target="#projects"
  hx-select="#projects" hx-swap="outerHTML"`로 바꿔도 되나, 기존 삭제
  버튼(project.html) 컨벤션과 맞추려면 표준 폼이 더 간단하다.

**테스트 관점** (`tests/test_board_routes.py` 컨벤션 참고, 기존
`test_delete_project_removes_tasks_and_jobs`(252) 패턴 재사용):
- `test_clear_cancelled_projects_deletes_only_cancelled`: cancelled 1개 +
  running/plan_ready 각 1개 생성 → `/projects/cancelled/clear` 호출 →
  cancelled만 `db.get_project`에서 `None`, 나머지는 유지되는지 검증.

---

### 2.2 워크플로우 다이어그램: 태스크 편집 · 화살표 · 사용 모델

화살표(의존성) 추가/삭제는 **이미 구현되어 있어 변경 불필요** (§1.2 참고,
`connect`/`disconnect`/`/tasks/{id}/deps`). 아래는 실제 갭인 세부계획·모델
선택만 다룬다. 제목/설명 편집도 기존 폼을 확장하는 형태로 간다.

#### API 변경

`POST /tasks/{task_id}/edit` (`app/main.py:1343`) 시그니처 확장:

- 요청 폼 필드 추가:
  - `extra_instruction: str = Form("")` — 세부 계획/추가 지시. 빈 문자열이면
    `None`으로 저장(기존 `retry_task`의 `instruction.strip() or None` 규칙과
    동일하게 맞춘다).
  - `model: str = Form("")` — 선택한 구체 모델 id. 빈 값이면 "기본값".
- 응답: 변경 없음 — 기존과 동일하게 `partials/board.html` 조각(HTML)을
  반환 (`_edit_action` 그대로 재사용).

```python
@app.post("/tasks/{task_id}/edit", response_class=HTMLResponse)
def edit_task_endpoint(
    request: Request,
    task_id: int,
    title: str = Form(...),
    description: str = Form(...),
    task_type: str = Form("text"),
    agent: str = Form("auto"),
    model: str = Form(""),
    extra_instruction: str = Form(""),
    o: str = Form("lr"),
):
    conn = db.get_conn()
    pid = _task_project_id(conn, task_id)
    return _edit_action(
        request, pid,
        lambda c: orchestrator.update_task_fields(
            c, task_id, title=title, description=description,
            task_type=task_type, agent=agent, model=model,
            extra_instruction=extra_instruction,
            enabled=settings.enabled_providers()),
        orientation=o)
```

`orchestrator.update_task_fields` (`app/orchestrator.py:640`) 확장:

```python
def update_task_fields(conn, task_id, *, title, description, task_type, agent,
                       model="", extra_instruction="",
                       usage_state=None, enabled=None):
    _project, task = _editable_task(conn, task_id)
    title = (title or "").strip()
    description = (description or "").strip()
    if not title or not description:
        raise ValueError("제목과 설명은 비울 수 없습니다")
    if task_type not in TASK_TYPES:
        raise ValueError(f"알 수 없는 type {task_type!r}")
    provider = _resolve_or_400(task_type, agent, description,
                               usage_state=usage_state, enabled=enabled)
    fields = {"title": title, "description": description,
              "task_type": task_type, "provider": provider}
    # retry_task(494)와 동일한 규칙: 에이전트가 바뀌면 이전 모델은 무효
    if provider != task["provider"]:
        fields["model"] = None
    elif model:
        fields["model"] = model if models.is_valid_model(provider, model) else None
    fields["extra_instruction"] = extra_instruction.strip() or None
    db.update_task(conn, task["id"], **fields)
```

- **DB 스키마 변경 없음** — `tasks.model`, `tasks.extra_instruction` 컬럼은
  이미 존재 (`app/db.py:61-62`).
- `app/models.py`에 새 함수 불필요 — `is_valid_model` 재사용.

#### 프론트 상태 관리 변경점

`templates/partials/task_detail.html`의 `.task-edit` 폼 (50번 줄)에
아래 2개 필드 추가:

```html
<label class="pal-row">
  <span>{{ '세부 계획' | t }}</span>
  <textarea name="extra_instruction" rows="3"
            placeholder="{{ '예: 권한이 필요한 명령은 쓰지 말고 파일 편집만으로 끝내라' | t }}">{{ task['extra_instruction'] or '' }}</textarea>
</label>
{% if task['task_type'] == 'text' %}
<label class="pal-row">
  <span>{{ '모델' | t }}</span>
  <select name="model" data-edit-model data-current="{{ task['model'] or '' }}">
    <option value="">{{ '기본값' | t }}</option>
  </select>
</label>
{% endif %}
```
- 미디어 태스크(`image`/`video`/`audio`)는 기존 에이전트 select 옆 주석처럼
  "미디어 태스크의 에이전트 선택은 무시된다"(media.py가 CLI를 고름) — 같은
  이유로 모델 select도 text 태스크에만 노출한다(기존 recover 패널과 동일
  조건, 73번 줄 컨벤션과 일치).
- 모델 목록 JSON은 이미 `task_detail.html:120`에
  `<script type="application/json" data-recover-models>`로 심어져 있다.
  edit 폼에서도 재사용하려면 `data-recover-models`를 공용 셀렉터로 쓰거나,
  edit 폼 전용으로 하나 더 심지 말고 **기존 스크립트 태그를 그대로
  참조**하도록 JS를 확장한다(중복 JSON 방지).

`static/board-editor.js` 변경:
- `syncRecoverModels(root)` (492번 줄)의 로직을 agent-select 종류에
  무관하게 동작하도록 일반화하거나, 동일 패턴의 `syncEditModels(root)`를
  추가:
  ```js
  function syncEditModels(root) {
    const panel = root || currentTaskRoot();
    const box = panel && panel.querySelector("[data-recover-models]");
    const agentSel = panel && panel.querySelector(".task-edit select[name=agent]");
    const modelSel = panel && panel.querySelector("[data-edit-model]");
    if (!box || !agentSel || !modelSel) return;
    // syncRecoverModels와 동일한 채우기 로직 (byProvider[agentSel.value])
  }
  ```
- 트리거 지점: 기존에 `[data-recover-agent]` change 리스너가 834번 줄
  근처에 있다 (`e.target.closest("[data-recover-agent]")`) → 동일하게
  `.task-edit select[name=agent]`의 change에도 `syncEditModels` 호출 추가.
- `toggleEdit(on, root)` (519번 줄)에서 `on === true`일 때 `syncEditModels(scope)`
  1회 호출 — 편집 폼을 열 때 현재 저장된 모델을 select에 반영
  (`data-edit-model`의 `data-current` 값을 `syncRecoverModels`와 동일한
  `keep` 로직으로 소비).

#### 기존 코드 컨벤션 준수 사항

- 모든 편집 라우트는 `_edit_action`을 통해 `partials/board.html` 조각을
  반환 → htmx `hx-target="#board" hx-swap="innerHTML"` 그대로 유지(캔버스
  팬/줌 보존). 새 필드도 이 계약을 바꾸지 않는다.
- 에이전트 변경 시 모델 초기화 규칙은 `retry_task`(494번 줄 519)와
  동일하게 `update_task_fields`에도 적용해 두 경로 간 불일치를 막는다.
- `is_valid_model`(모델 검증), `TASK_TYPES`(허용 종류), `EDITABLE_TASK_STATUSES`
  (`pending`, `failed`만 편집 가능) 등 기존 가드는 그대로 재사용 — 신규
  가드 불필요.
- 한국어 주석 스타일과 "왜"를 설명하는 주석 관행(예: 529-530번 줄
  `retry_task`의 모델 무효화 이유 주석)을 그대로 따른다.

---

## 3. 변경 파일 요약

| 파일 | 변경 내용 |
|---|---|
| `app/db.py` | `list_projects_by_status` 신규 추가 |
| `app/main.py` | `POST /projects/cancelled/clear` 신규, `/tasks/{id}/edit`에 `model`/`extra_instruction` 폼 필드 추가 |
| `app/orchestrator.py` | `update_task_fields`에 `model`/`extra_instruction` 파라미터 추가 |
| `templates/partials/projects.html` | 카드별 삭제 버튼(취소 상태에서만), 상단 "모두 비우기" 버튼 |
| `templates/partials/task_detail.html` | `.task-edit` 폼에 세부 계획 textarea + 모델 select 추가 |
| `static/board-editor.js` | `syncEditModels()` 추가, edit 폼 열기/에이전트 변경 시 호출 |

DB 마이그레이션: **불필요** (기존 컬럼 재사용, 신규 테이블 없음).
