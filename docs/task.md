# Agentic OS — 작업 내역

완료/진행/예정 작업 추적. 로드맵은 [plan.md](plan.md) 참고.

## 완료 (V3 — 공개 배포 착수, 2026-07)

4개 CLI(claude/antigravity/grok/hermes) 협의(Council) 세션의 개선 제안을 코드
검증과 대조해 [docs/plan.md](plan.md)의 V3 로드맵(Phase 0~4)으로 세움. 이후 Phase 1의
G9(미설치 CLI 감지)와 Phase 2 항목 일부를 먼저 구현.

- [x] **공개 배포 마스터플랜 작성** — 이후 `docs/plan.md`의 V3 섹션으로 통합
      (별도 `docs/masterplan.md`는 통합 후 제거)
- [x] **첫 실행 셋업 위저드(`/setup`)** — 환영 → 에이전트 선택(설치 감지) → CLI별
      OAuth 로그인 안내 → 완료의 4단계. 첫 실행 시 `/`가 자동 안내, 사이드바
      ⚙︎ 링크로 재설정 가능 (`templates/setup.html`, `static/setup.js`)
- [x] **CLI·보조도구 설치 감지** — `shutil.which` 기반, CLI를 실행하지 않아
      로그인 프롬프트·사용량 소모 없음 (`app/setup.py`, `/api/setup/status`)
- [x] **활성 에이전트 필터링(`app/settings.py`, `data/settings.json`)** — 선택한
      에이전트만 사용량 사이드바·에이전트 칩·`/note`·자동 라우팅
      (`route_auto`/`rank_cloud`)·협의 모드(`select_members`)에 반영. 비활성
      에이전트로 잡 제출 시 400 거부, 이미 큐에 있던 잡은 실행 유지
- [x] 설정 파일 없음/손상 시 전체 활성으로 폴백 — 기존 환경 무변경 보장
- [x] 테스트 30개 추가 (총 208개)
- [x] Council 모드가 masterplan 브랜치 분기 이후 merge된 것을 뒤늦게 발견 →
      `origin/master` 재병합으로 누락 해소, 마스터플랜 진단 정정
- [x] `docs/masterplan.md`를 `docs/plan.md`의 V3 섹션으로 통합, 기존 "V3 후보"는
      V4로 이름 변경(멀티턴·병렬은 V3 Phase 3/4로 편입돼 중복 제거)
- [x] **V3 Phase 0(신뢰·첫인상) 완료** — `CONTRIBUTING.md` 교체(다른 프로젝트
      문서였던 확정 버그 수정), `.github/workflows/ci.yml`(Python 3.11/3.12
      pytest), 이슈·PR 템플릿, README 영문 전환 + 스크린샷 4장 + CI·라이선스
      배지, 기존 한국어 내용은 `README.ko.md`로 보존
- [ ] GitHub 저장소 description/topics/homepage — API 도구 없음, 수동 설정 필요
- [ ] 데모 GIF — 정적 스크린샷만 있음

## 완료 (V2.2 — 세션 파편화 해소, 2026-07)

`-p` 비대화형 호출 특성상 세션을 이어갈 때마다 노트가 파편화되던 문제 해소.

- [x] **스레드당 노트 1개** — "이 세션 이어서 진행"으로 만든 작업은 새 노트를 만들지 않고 원본 노트에 `## 결과 (N차)` 섹션으로 이어 씀 (`jobs.note_path`를 스레드 링크로 재사용, `memory.append_note`)
- [x] **워크스페이스 자동 그룹** — 노트 저장 시 작업이 실행된 워크스페이스 이름으로 자동 그룹 지정 (frontmatter `workspace:` 기록), 수동 그룹 변경이 항상 우선(`auto_group` 플래그로 구분)
- [x] **소급 그룹핑** — 서버 기동 시 그룹 없는 기존 노트를 잡의 workdir 기준으로 1회 멱등 백필
- [x] **노트에 workdir 기록** — frontmatter `workdir:`에 실행 경로 저장, 세션 이어가기 폼에도 그대로 전달해 같은 위치에서 계속 작업
- [x] **동적 모델 목록** — 하드코딩 대신 CLI(`agy models`/`grok models`/claude 패밀리 별칭)에서 기동 시·주기적으로 수집해 캐시(`data/models_cache.json`), 실패 시 폴백
- [x] **메모리 사이드바 접이식 폴더 UI** — 그룹이 기본 접힌 폴더로 표시(셰브런 + 노트 수 배지), 클릭 시 롤링 펼침, 열어둔 그룹은 localStorage로 유지
- [x] 스레드가 공유하는 노트는 잡 하나만 삭제해도 보존 (`db.jobs_sharing_note` 가드)
- [x] 테스트 158개로 확장

## 완료 (V2 — 2026-07)

### UI 리디자인 (Claude Desktop 컨셉)
- [x] 웜 페이퍼 톤 팔레트 + 세리프 헤드라인, 라이트/다크 모드
- [x] 좌측 고정 사이드바 (모바일은 햄버거 오프캔버스)
- [x] 사이드바에 메모리(위) + 사용량(아래) 배치

### 메모리 (노트)
- [x] 노트 hover 시 ··· 컨텍스트 메뉴: 고정 / 이름변경 / 그룹(새 그룹) / 보관 / 삭제
- [x] 노트 상태를 볼트 밖 사이드카(`data/note_state.json`)에 저장 (MEMORY_DIR 노트만 관리)
- [x] 노트 검색·클릭 → 해당 CLI 세션 이어가기 (완료 시 session_id를 frontmatter에 기록)
- [x] 메모리 ↔ 작업큐 양방향 연동 (한쪽 삭제 시 반대쪽도 삭제, 이름변경 시 재연결)

### 모델 선택
- [x] 에이전트 pill 클릭 → 모델 선택 팝업 (동적 목록)
- [x] jobs.model 컬럼 + `--model` 플래그 전달, 첫 항목 기본값(플래그 생략)
- [x] CLI 동적 모델 목록: `agy models` / `grok models` / claude 패밀리 별칭(fable·opus·sonnet·haiku) — 백그라운드 캐시(`data/models_cache.json`), 실패 시 폴백

### 사용량 (CodexBar 연동)
- [x] `codexbar usage --format json`을 백그라운드로 주기 조회 → `data/usage_cache.json` 캐시
- [x] 실제 사용률(%)·창별 리셋 카운트다운 표시 (claude/grok 실측)
- [x] 계정 단위 창(primary/secondary)만 가용성 계산, 모델 스코프 창은 표시만

### 자동 모드 (코칭)
- [x] 단순 작업 → Hermes(로컬), 복잡 작업 → 실측 잔여 사용량 최다 에이전트
- [x] `/api/recommend`로 실시간 라우팅 추천 힌트 (컴포저 debounce)

### 입력 / 실행 위치
- [x] 파일 첨부 버튼 + 드래그앤드롭 → `data/uploads/` 저장 후 경로를 프롬프트에 추가
- [x] 작업 위치(workspace): 로컬 폴더 / GitHub 리포(clone) 등록, 작업을 해당 cwd에서 실행
- [x] 등록된 경로만 실행 허용 (임의 경로 차단)

### 외부 접속
- [x] Tailscale serve로 tailnet 내 HTTPS 노출 (`https://<host>.<tailnet>.ts.net`)
- [x] CSRF 미들웨어 same-origin 비교로 개선 (포트 무관) + tailnet origin 허용

### 운영
- [x] jobs 테이블 마이그레이션 (model / note_path / workdir 컬럼)
- [x] 폴더 이동으로 깨진 launchd plist 경로 복구
- [x] 테스트 통과 (당시 122개, 현재 158개 — V2.2 항목 참고)

### 작업 위치 선택 UI (팝업)
- [x] 작업 폴더: 서버 파일시스템 탐색 팝업 (`/api/folders`, 홈 이하로 제한, Finder식)
- [x] GitHub: `gh` 로그인 계정의 리포·브랜치 팝업 선택 후 클론 (app/github_cli.py, `gh repo clone`으로 private 지원)
- [x] 추가 후 새 위치 자동 선택 (X-Workspace-Path 헤더)

### 버그 수정 / 반응형
- [x] 모달이 안 닫히던 버그 — `.modal-overlay{display:flex}`가 `[hidden]` 덮어쓰던 문제, `[hidden]` 규칙 추가 + Esc 닫기
- [x] 모바일(아이폰): 작업 큐 테이블 → 카드형 목록, 사이드바 로고/햄버거 겹침 해소, 본문 여백 축소

### Antigravity·모바일 UI (V2.1)
- [x] Gemini 제거, Antigravity CLI(`agy`) 도입 — `agy -p`/`-c`, 구글 OAuth, 모델 목록·자동 라우팅·프로바이더 목록 반영
- [x] 사용량 패널: 기본 접힘(요약 칩), 헤더 터치 시 펼침(localStorage 유지)
- [x] 모바일 가로 슬라이딩(러버밴드) 방지: `overflow-x`/`overscroll-behavior` 잠금
- [x] 대화창 하단 정리: ＋도구 메뉴(파일·메모리·타임아웃), 에이전트 칩+모델 팝업 통합

### 외부 접속 / 권한
- [x] Tailscale serve HTTPS 접속 + CSRF 허용목록에 tailnet 이름
- [x] 파일 읽기/쓰기 권한 진단 — launchd 파이썬에 Documents/Desktop/Downloads/iCloud/외장볼륨 TCC 허용 확인, 자식 CLI가 상속(hermes 쓰기 실측)

## 진행/예정

### V3 Phase 0 — 신뢰·첫인상 (거의 완료)
- [ ] 데모 GIF (스크린샷은 완료)
- [ ] GitHub 메타(description·topics·homepage) — 저장소 Settings에서 수동 설정 필요

### V3 Phase 2 — clone→첫 성공 마찰 제거 (대부분 완료)
- [x] `bootstrap.sh` — uv/venv 감지 + deps + aos.env 시드 + 포트 확인 + 브라우저
      오픈, macOS·Linux 공용
- [x] `/api/health` + `aos doctor` (`app/health.py`, `app/__main__.py`) — 서버·CLI·
      설정 진단, `python -m app doctor` / JSON
- [x] 패키징 — pyproject `[build-system]`(hatchling) + `[project.scripts]` `aos`,
      `python -m app`/`aos`/`aos doctor` 지원
- [x] Linux 지원 — config가 홈 이하로 이미 동작(iCloud 자동 우회), systemd 유닛
      템플릿 `deploy/agentic-os.service` 추가
- [x] UI i18n(영문 기본 + 한국어) — `app/i18n.py`(한국어 원문=키, 영어 매핑),
      쿠키/Accept-Language 기반 언어 결정 + 사이드바 토글(`/lang/{code}`),
      템플릿 `t` 필터(pass_context로 상수 폴딩 방지) + JS `window.I18N`/`t()`,
      route_auto 이유·시간 포맷·CLI 메타데이터까지 번역. 테스트 224개

### V3 Phase 3 — 차별 기능 가시화 (대부분 완료)
- [x] 라우팅 투명성 — `jobs.route_reason` 컬럼, 작업 상세 "🔀 자동 라우팅" 표시,
      작업 큐 `자동` 태그+툴팁
- [x] 사용량 패널 강화 — 에이전트별 "최근 24시간 N회 실행"(usage_log 기반)
- [x] Provider 플러그인 계약 문서 — `docs/PROVIDERS.md`
- [x] Council 결과 가독성 — 마크다운 렌더링으로 협의 출력 구조화(전용 탭은 보류)
- [x] 병렬 실행 — provider별 직렬 + 서로 다른 provider 병렬(`MAX_CONCURRENT_JOBS`).
      협의 잡은 배타 실행. 비전 보드 태스크도 같은 워커 큐 공유

### V3 Phase 1~4 — 나머지 (docs/plan.md 참고)
- [x] 마크다운 렌더링(job/note 출력) — marked.js + highlight.js vendored,
      코드 복사 버튼, 라이트/다크 테마 (`static/render.js`, `static/marked.min.js`,
      `static/highlight.min.js`, `static/hljs-theme.css`). 프롬프트는 원문 유지,
      출력·노트 본문만 렌더
- [x] 키보드 단축키(⌘/Ctrl+Enter 전송, 컴포저·노트 재개 폼)
- [x] 다크모드 명시 토글 — 사이드바 버튼, `data-theme` + localStorage,
      FOUC 방지 인라인 init, hljs 코드 테마도 스코프 대응
- [x] 실패 작업 에러 배너 — CLI 없음/미인증/타임아웃/취소 원인 해석 + `/setup` 링크
- [x] `bootstrap.sh`, 범용 `/api/health`, 플랫폼 독립화(Linux/systemd), 패키징, i18n
      — Phase 2에서 완료 (위 "V3 Phase 2" 절 참고)
- [x] 라우팅 이유 기록, 사용량 대시보드 강화, provider 플러그인 문서화 — Phase 3 완료
- [ ] Council 모드 결과 레이아웃 UI화(제안 카드 → 비평 → 종합 탭) — 마크다운
      렌더로 구조화됨, 전용 탭은 선택 개선으로 보류
- [x] 병렬 작업 실행 — provider 단위 병렬 완료 (위 Phase 3 참고)
- [x] 노트 스레드 → 채팅 버블 뷰(가짜 멀티턴) — `memory.parse_thread`가 본문을
      사용자/에이전트 턴으로 파싱, `note.html`이 좌우 말풍선으로 렌더(마크다운·
      코드 하이라이트), 아래 "이어서 진행" 입력으로 연속 대화. 비스레드 노트는
      전체 본문 렌더로 폴백

### V4 — 비전 보드 (2026-07-25)
- [x] 미디어 능력 스파이크 — agy·grok 이미지 생성 실측 확인, grok 비디오 도구
      확인, 오디오는 API 폴백 필요 (docs/PROVIDERS.md 부록)
- [x] `projects`/`tasks` 테이블 + 헬퍼 (`db.py`) — 태스크는 기존 jobs로 실행
- [x] `app/orchestrator.py` — 계획(JSON 계약, 파싱 실패 자동 1회 재시도) →
      승인 → `_advance` 멱등 실행 루프(의존성 게이트, inflight 상한, 실패
      일시정지, 재시도·재계획·취소), `layout_graph` 위상 배치
- [x] `app/media.py` — 가상 provider `media`: 이미지 agy→grok CLI 폴백,
      비디오 grok CLI, 오디오 Gemini TTS API(옵트인 키, 인프로세스 전용),
      산출물 경로 계약(출력 마지막 줄)
- [x] UI — `/board` 목록·컴포저, `/projects/{id}` n8n식 DAG(서버 렌더 SVG +
      HTMX 2s 폴링), 노드 클릭 태스크 상세 패널, 미디어 썸네일·플레이어,
      승인/재계획/재시도/취소, 다크모드
- [x] 테스트 46건 (orchestrator 32 · media 7 · board routes 7) + E2E
      (mock provider로 계획→승인→의존성 실행→완료)
- [x] 실전 검증 — 실제 프로젝트(grok 계획 + claude 글 + agy 이미지 + claude
      검수)가 보드 위에서 자동 완주
- [x] **[완료] 보드 컴포저에 메인 챗 기능(모델·워크스페이스·도구) 포함**:
      `templates/board.html` 프로젝트 폼에 `enctype="multipart/form-data"` +
      `#file-chips` + `#tools-btn`/`#tools-popup`(files / attach_memory /
      timeout_min)를 메인 composer와 동일 엘리먼트 id로 추가 → `static/app.js`
      기존 파일칩·드래그앤드롭·팝업 로직을 JS 수정 없이 재사용. 백엔드
      (`POST /projects`, `orchestrator.start_project`)는 업로드 저장·메모리
      컨텍스트 선행·`timeout_sec`→plan job 전달을 이미 처리. 2026-07-25 회귀
      테스트로 pytest 전체 295건 통과 확인.

### V4 회귀 테스트 · 실측 체크리스트 (2026-07-25)

통과한 테스트 요약:
- pytest 전체 **295 passed** (orchestrator / board_routes / main / i18n /
  workspace / models 관련 스위트 포함)
- i18n 영문 기본 렌더 스모크 6건 통과 (미번역 키는 영문 원문 폴백)
- 핫픽스: 선행 태스크에서 미해결로 표시된 `codex`→`claude` 폴백 4실패는
  `codex`가 PROVIDERS(claude/antigravity/grok/hermes)에서 제거된 데 기인.
  테스트 픽스처를 유효 provider(`grok`)로 교체해 해소 (`tests/test_orchestrator.py`,
  `tests/test_board_routes.py`).

수동/통합 시나리오 체크리스트:
- [x] `/board`에서 에이전트(grok/claude/…)+모델 선택 후 프로젝트 생성 → plan job
      `provider`/`model` 일치 (`test_create_project_with_provider_and_model`,
      `test_start_project_with_explicit_planner_and_model`)
- [x] 로컬 폴더 선택 → `project.workdir` 저장, 하위 task job `workdir` 상속
      (`test_start_project_with_explicit_planner_and_model` workdir 검증)
- [x] GitHub 리포 추가 후 선택 → `workdir`가 clone 경로 (workspace.add_local 경로
      적용, `test_create_project_with_workdir_tools_and_model`)
- [x] 파일/메모리/타임아웃 옵션이 plan 단계에 반영 (첨부 저장·`[메모리 컨텍스트]`
      선행·`timeout_sec=20*60`, 동일 테스트)
- [x] 기존 자동 planner 경로(필드 비움, provider=auto) 회귀 없음
      (`test_start_project_queues_plan_job`, `_unknown_planner_falls_back_to_auto`)

남은 한계:
- 태스크별 개별 모델 선택은 여전히 제외 (plan 단계에서만 provider/model 지정,
  하위 task 모델은 계획 결과로 결정)
- Antigravity 사용량 실측 미지원, 세션 재개(`--resume`) 모델 id 실측 미검증은
  V5 후보로 유지

### V4.1 — 워크플로 다이어그램 편집기 (2026-07-25)
계획이 나온 DAG를 읽기 전용에서 **편집 가능한 n8n식 캔버스**로 전환.

- [x] **노드 배치** — 드래그로 위치 저장(`tasks.pos_x/pos_y`, NULL=자동 배치),
      "자동 정렬"로 좌표 초기화
- [x] **의존성 연결** — 포트↔포트 드래그로 엣지 추가, 연결선 클릭으로 삭제.
      터치 대안: 태스크 상세 패널의 선행 태스크 체크박스(같은 `deps` 엔드포인트)
- [x] **태스크 CRUD** — 팔레트에서 에이전트·종류 골라 노드 추가, 상세 패널에서
      제목·설명·종류·에이전트 편집, 삭제 시 형제 `depends_on`에서 자동 제거
- [x] **순환·provider 검증 공유** — `_assert_acyclic()` / `resolve_provider()`를
      계획 파싱과 편집 API가 공유 → 편집으로 계획 계약 우회 불가
- [x] **동시성 가드** — 구조 편집은 `plan_ready`/`paused`에서만, 태스크는
      `pending`/`failed`만. `active_projects()`가 `planning`/`running`만 반환하므로
      편집과 디스패치 경합 없음(락 불필요). 노드 위치 이동은 전 상태 허용
- [x] **모바일 재정렬** — `layout_graph(tasks, orientation)` `tb` 모드: 좁은 화면에서
      깊이 위→아래 스택, 저장 좌표 무시. 핀치줌·팬·화면 맞춤·하단 시트 상세
- [x] **라우트 응답** — 편집 액션 6종이 리다이렉트 대신 보드 조각 반환 → htmx가
      `#board`만 교체(팬/줌·선택 유지). 편집 가능 상태에서는 2초 폴링 중지
- [x] 테스트 328건 통과(신규 33건) + Chromium 실기기 프로파일로 드래그·연결·
      순환 거부·삭제·추가·정렬·핀치줌·하단 시트 확인

### V4.2 — 워크플로 편집기 MVP 완성 (예정, 2026-07-25 노트 통합)

V4.1 편집 캔버스 기반 위에, 목표 3대 축(다이어그램 편집·태스크 CRUD·모바일 재정렬)을
한 사이클로 닫는다. 후보/우선순위는 선행 태스크 [6]에서 왔고, MVP 로드맵 세부는
[docs/plan.md](plan.md) V4.2 절 참고. (노트 원문 §2~§5 누락 → plan.md에 Table [6] 기준
재구성, 원문 확보 시 대조 필요)

- [ ] **MVP 범위 (P0, 6종 + 모바일 재정렬)** — plan.md §1 In 항목
  - [ ] #1 노드 실행 상태 오버레이 (경량 상태 폴링 + 색상/펄스)
  - [ ] #2 필수값/미설정 경고 표시 (`has_warning` 테두리 점)
  - [ ] #3 1-depth Undo (구조 편집 스냅샷 1개 + 실행 취소 버튼)
  - [ ] #4 삭제 확인 + 의존성 경고 다이얼로그 (soft-delete `status=deleted` + 5초 실행취소)
  - [ ] #5 필드별 부분 PATCH + draft (blur 500ms debounce, 폴링 충돌 방지)
  - [ ] 모바일 재정렬 (가로 DAG → 세로 스택/아코디언, `layout_graph` `tb` 모드)
- [ ] **P1 (5종)** — #6 템플릿 갤러리, #7 키보드 단축키, #8 Import/Export, #9 박스
      셀렉트, #10 연결 사전 유효성 (신규 `templates` 테이블·스키마 v1·인터랙션 레이어 확장)
- [ ] **P2 (5종)** — #11 격자 정렬 스냅, #12 실행 히스토리 재생, #13 노드별 테스트
      실행, #15 접근성, #16 비용/토큰 추정 (신규 인프라: 이력 로깅·실행 격리·토큰 계측)
- [ ] **보류** — #14 협업 커서/코멘트 (단일 사용자 로컬 도구라 WebSocket 계층 정당화 안 됨)
- [ ] 스프린트 분해 7개 (plan.md §5)를 이슈로 파생 — soft-delete 스키마 → 상태 오버레이
      → 필수값 경고 → 1-depth Undo → 부분 PATCH → 삭제 다이얼로그 → 모바일 재정렬
- [ ] 신규 pytest 추가 (상태 오버레이/undo/soft-delete/부분 PATCH/모바일 재정렬)

### V4.3 — 채널(스레드 채팅) + Orca 스타일 프로젝트 레이아웃 (2026-07-30)

계획은 [2026-07-25 와이어프레임](2026-07-30-orca-layout-wireframe.md)(Orca ADE 참고:
좌측 고정 채팅 + 중앙 진행 흐름 시각화). 아래는 실제 구현.

- [x] **채널(신규 최상위 기능)** — Slack류 사이드바 채널 목록(`channels`/`messages`
      테이블, `app/db.py`). 채널당 스레드형 멀티턴 대화(`root_id`/`parent_id`/`seq`),
      진행 중인 메시지는 사이드바에 실시간 배지로 표시(`/partials/channels`, 5초 폴링)
- [x] **채널 페이지** (`templates/channel.html`, `static/channels.js`) — 새 스레드
      시작 + 답장 패널(`thread-panel`, 데스크톱 사이드 패널/모바일 바텀시트),
      마크다운 렌더 재사용(`render.js`)
- [x] **chat-rail** (`templates/partials/chat_rail.html`, `static/chat-rail.js`) —
      프로젝트 페이지(`project.html`) 좌측에 고정 폭 360px(280–480 리사이즈,
      접기/펼치기 localStorage 유지) 레일. 탭 2개: 채팅(프로젝트에 연결된 채널,
      `get_or_create_project_channel`) / 실행 로그(`board-editor.js`가 쏘는
      `orca-tasks-updated` 이벤트를 구독해 RUNNING 태스크 로그 추적)
- [x] **오프라인 폴백** (`static/mock-api.js`) — chat-rail의 실제 API fetch가
      실패할 때만 로컬스토리지 기반 시뮬레이션 응답으로 대체(정상 동작 시 미관여)
- [x] **중앙 vision-flow 뷰** (`static/vision-flow.js`) — `#board` DAG와 별도 API 없이
      같은 DOM(`data-title/provider/type/artifact/error`)을 읽어 카드/트리/변경파일
      뷰로 재구성, `MutationObserver`로 보드 폴링·편집 시마다 갱신
- [x] `templates/project.html`을 `orca-project-shell`(좌 chat-rail + 중앙 캔버스)
      그리드로 재구성, 전용 테마 `static/orca-theme.css`
- [x] 채널 API/페이지 pytest 커버리지 — `tests/test_channels.py`(쓰레드 패널 마크업,
      메시지 생성→잡 실행→trace/thread 조회→답장→채널 삭제 전 과정)

### V4.4 — 태스크 오류 조치: 모델 교체 · 지시 추가 (2026-07-30)

CLI가 오류 안내문(권한 자동 거부·미로그인·사용량 한도)을 stdout에 흘리고 exit 0으로
끝내면 태스크는 '완료'로 기록된다 — 배지만 보면 원인을 알 수 없고, 프로젝트가 완료
상태라 그래프 편집도 잠긴다. 조치 경로를 태스크 상세에 따로 뚫었다.

- [x] `orchestrator.output_error_hint` — '완료'지만 결과가 오류 안내문/빈 응답인
      태스크 감지(`OUTPUT_ERROR_MARKERS`, 짧은 응답만 검사해 오탐 방지).
      캔버스 노드에 `has-warn` 테두리 + ⚠ 표시(`partials/board.html`)
- [x] `orchestrator.retry_task(agent=, model=, instruction=, cascade=)` — 실패
      태스크뿐 아니라 완료 태스크도 되돌린다. 모델 교체(`tasks.model` → 잡의
      `--model`), 추가 지시(`tasks.extra_instruction` → 프롬프트 말미 섹션),
      후속 태스크 무효화(`dependent_seqs`, 낡은 결과가 남지 않게)
- [x] `tasks.model`/`tasks.extra_instruction` 컬럼 + 마이그레이션 (`app/db.py`)
- [x] UI — 태스크 상세의 `⟳ 조치하고 다시 실행` 패널(`partials/task_detail.html`),
      에이전트별 모델 목록은 `board-editor.js`가 패널에 실린 JSON으로 채운다.
      실행 중·완료 상태에서도 열린다(그래프 편집 잠금과 무관)
- [x] 테스트 6건 (orchestrator 5 · board routes 1)

### V4.5 — 비전 보드 멀티탭·모바일 레이아웃 완성 (2026-07-30)

V4.3에서 뼈대만 있던 멀티탭 워크스페이스(`docs/vision-board-ade-spec.md`)의 갭을
`docs/vision-board-ade-gap-report.md` 감사 결과대로 하나씩 닫았다. 모바일에서
전송 버튼이 안 보이고 워크스페이스에서 돌아갈 방법이 없던 문제가 출발점.

- [x] **모바일 컴포저 가시성 수정** — `.orca-chat-rail`의 하단 오프셋이 세그먼트
      컨트롤 높이(`--orca-segctl-h` 신설, 44px 버튼+패딩+safe-area)를 반영하지
      않아 전송 버튼이 `.orca-mobile-segctl`에 가려지던 버그 수정. `.orca-project-main`/
      `.orca-mobile-segctl`도 같은 변수를 공유하도록 통일(`static/orca-theme.css`)
- [x] **모바일 채팅 ↔ 워크스페이스 왕복 내비게이션** — `.orca-ws-header`(뒤로가기 +
      제목) 추가, `goToWorkspace()`/`goToChat()`이 `history.pushState`/`popstate`로
      브라우저 뒤로가기와 연동. 워크스페이스 상단에서 아래로 스와이프하면 채팅으로
      복귀(스크롤 최상단일 때만, `static/board-workspace.js`)
- [x] **채팅→탭 자동 오픈** — `messages.created_task_id` 컬럼 추가(`app/db.py`),
      `orchestrator.add_task(source_message_id=)`가 채움, `chat-rail.js`가 메시지
      응답에서 이를 감지해 `orca-tab-opened` 이벤트 발행 → 이미 열린 탭이면
      재사용, 없으면 새로 열림
- [x] **탭 패널 콘텐츠 주입 완성** — `kind="flow"`(vision-flow.js)를 `#board` 내부
      토글에서 독립 탭으로 이식. localStorage 탭 복원 시 `HEAD /partials/task/{id}`로
      존재 확인 후 죽은 탭을 정리(해당 라우트가 HEAD를 지원하지 않던 버그도 함께 수정)
- [x] **브레이크포인트 3단계 정리** — 태블릿(`768px~1023px`) 전용 오프캔버스 블록을
      하나로 합쳐 모바일(`<768px`)에 태블릿 규칙이 새지 않게 스코프를 명확히 함,
      잔여 `!important` 제거
- [x] i18n 중복 키 정리 — `자동 라우팅`/`완료`가 서로 다른 화면(작업 상세 배지 vs
      셋업 위저드)에서 다른 뜻으로 쓰이면서 같은 한국어 키를 공유해 dict 마지막
      값으로 덮어써지던 버그(`app/i18n.py`) — 셋업 위저드 쪽 키를 `자동 배분`/`마침`으로
      분리, 완전 동일한 중복(`작업 큐`/`에이전트`)은 제거. `tests/test_i18n.py`에
      회귀 테스트 추가
- [x] 테스트 393건 통과(신규 다수 — 채널 API/모바일 컴포저/반응형 브레이크포인트/
      i18n 중복 키/탭 kind=flow). 루트에 흩어진 임시 스모크 스크립트 8개를 검토해
      재사용 가치 있는 것은 `tests/`로 정식 이관, 나머지는 제거

### V4.6 — 홈 대시보드 셸 재구조화 (2026-08-04)

V4.3 Orca 레이아웃을 홈(`/`)에 완성 — 비전보드 세계를 좌측 사이드바에서 우측
레일 탭으로 옮기고 채팅 레일에 검색·정렬·이름 변경을 더했다. 상세 설계는
[plan-home-dashboard-restructure.md](plan-home-dashboard-restructure.md).

- [x] **좌측 사이드바 축소** — 브랜드 + 설정 줄만 남기고 폭 300px→200px로 축소,
      설정·언어·테마 줄은 세로 스택(`body.orca-home .side-foot` 미디어 쿼리).
      비전보드(프로젝트 목록·컴포저)와 태스크 인스펙터를 `templates/index.html`에서
      우측 `.orca-side-rail`로 이사(`static/home.js` 인스펙터 마운트 콜백 수정).
- [x] **우측 레일 탭 재편** — 채팅 / 프로젝트 / 태스크(보드 노드 클릭 시에만 나타남,
      `home-rail-task-tab` hidden → `showRailPanel("task")`). 홈의 '노트'·'세션'
      탭 제거(`static/app.js` note 로더·컨텍스트 메뉴 걷어냄).
- [x] **채팅 레일 검색·정렬·이름 변경**:
  - 상단 도구줄(검색창 + 정렬 토글) 추가(`orca-chat-toolbar`, `home-chat-search`,
    `data-chat-sort=time|project`).
  - 정렬: 시간순(기본, localStorage `aos-home-chat-sort`) / 프로젝트별(작업 위치 그룹,
    접이식 `orca-chat-group`, 접힘 상태 `aos-home-chat-closed-groups` 유지).
  - 말풍선 우측 ✎ 클릭 → 그 자리에서 이름 입력(`startRename`, 폴링이 입력 지우지
    않도록 `renamingId` 가드). `POST /jobs/{id}/rename`(`app/main.py`)으로 저장,
    빈 값이면 이름 제거 → 프롬프트가 다시 제목.
  - `jobs` 테이블 `title` 컬럼 신규 마이그레이션(`app/db.py._migrate`).
  - `#jobs` 폴링 조각이 말풍선에 필요한 제목·작업위치·생성시각을 `data-title`/
    `data-workdir`/`data-created` 데이터 속성으로 실어 보냄(`templates/partials/jobs.html`).
  - i18n: 채팅 검색/정렬/프로젝트별/작업 위치 없음 키 추가(`app/i18n.py`).
- [x] **태스크 인스펙터 우측 레일 전용 탭** — `mountTaskInspector` `onOpen`에서
      `taskTabBtn.hidden=false` + `showRailPanel("task")`, 좁은 화면은 `openRailOverlay()`.
- [x] 테스트 갱신 — `tests/test_home_layout.py`(레일 탭·검색정렬·이름변경·조각
      데이터속성·rename 라우트) + `tests/test_home_tabs.py`. 홈 레이아웃 검증 34건 통과.

### V5 — 확장 기능 후보
- [ ] Antigravity 사용량 실측 — CodexBar 미지원, 별도 연동 필요 (현재 "정보 없음")
- [ ] antigravity/grok 세션 재개(`--resume latest`, `-c`) 및 모델 id 실측 검증
- [ ] 벡터/임베딩 기반 메모리 검색
- [ ] 토큰·비용 추적
