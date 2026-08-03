# Agentic OS — 개발 계획

Mac Mini에서 Claude / Antigravity / SuperGrok / Hermes 유료 구독 CLI를 하나의 로컬 웹
대시보드로 통합하는 프로젝트의 로드맵. 완료 항목은 [task.md](task.md) 참고.

## 비전

여러 AI CLI를 한 화면에서 쓰고, 각 에이전트의 **실제 남은 사용량**에 따라 작업을
가장 여유 있는 에이전트로 자동 배분한다. 결과는 Obsidian 지식베이스에 축적되고,
외부(아이폰·맥북)에서도 Tailscale로 안전하게 접근한다.

V3부터는 이 도구를 "내 Mac Mini 전용"에서 **다른 사람들도 GitHub에서 받아 편하게
쓸 수 있는 범용 로컬 웹앱**으로 넓히는 것이 목표다. 한 줄 포지션:

> *One local dashboard for all your paid AI CLIs — routes by real remaining quota,
> queues work, resumes after rate limits, and files results into notes.*

**전략: Claude/Grok UI와 정면 승부하지 않는다.** Claude Desktop·Grok은 단일 제공자
채팅 제품이고, Agentic OS는 **여러 구독 CLI를 실측 사용량으로 묶는 로컬
오케스트레이터**다. UI 개선의 성공 기준은 "Claude처럼 예쁘다"가 아니라
**작업을 던지고 → 누가 도는지 보이고 → 결과가 읽히고 → 이어가기 쉽다**이다.

**배포 모델: 로컬 셀프호스트 유지.** `127.0.0.1` 바인딩 + Tailscale 외부 접속이라는
현재 보안 모델을 그대로 공개한다. 호스팅 SaaS(인증·멀티테넌시)는 아키텍처 전면
재설계가 필요하므로 **비목표**로 명시한다. 데스크톱 앱(Tauri) 패키징은 채택이 늘고
코드서명 여력이 생긴 뒤 재검토하는 장기 옵션.

## 단계별 계획

### V1 — 기반 (완료)
- 통합 디스패치 + SQLite 작업 큐 + SSE 실시간 출력
- rate limit 자동 감지 후 세션 재개
- Obsidian 노트 저장 / ripgrep 검색 / 메모리 컨텍스트 첨부
- launchd 자동 기동

### V2 — Claude Desktop UI + 실측 사용량 (완료)
- Claude Desktop 컨셉의 UI 리디자인 (사이드바 레이아웃, 웜 페이퍼 톤)
- 사이드바: 메모리(위) + 사용량(아래), 노트 hover 컨텍스트 메뉴(고정/이름변경/그룹/보관/삭제)
- 노트 클릭 → 해당 세션 이어가기
- 채팅창 에이전트별 **모델 선택 팝업**
- **CodexBar 연동**으로 실제 사용률·리셋 시각 표시
- 자동 모드: 실측 잔여 사용량 기준 라우팅 + 실시간 코칭 힌트
- 파일 첨부(드래그앤드롭) / 작업 위치(로컬 폴더·GitHub 리포) 연동
- 메모리 ↔ 작업큐 양방향 연동
- Tailscale serve로 tailnet 내 HTTPS 외부 접속

### V2.1 — Antigravity·모바일 UI (완료)
- Gemini → **Antigravity CLI(`agy`)** 교체: `agy -p`/`-c`, 구글 OAuth, 모델 선택·자동 라우팅 반영 (CodexBar 미지원 → 사용량 "정보 없음")
- 사용량 패널: 기본 접힘(요약 칩), 헤더 터치 시 펼침(localStorage 유지)
- 모바일 가로 슬라이딩(러버밴드) 방지
- 대화창 하단 정리(Claude Desktop 스타일): ＋도구 메뉴(파일·메모리·타임아웃), 에이전트 칩+모델 팝업 통합

### V2.2 — 세션 파편화 해소 (완료)
- 세션 이어가기(`-p` 호출 특성상 매번 새 노트가 생기던 문제) → **스레드당 노트 1개**로 통합, `## 결과 (N차)` 섹션으로 append
- 노트를 **워크스페이스 이름으로 자동 그룹핑**(수동 그룹이 항상 우선), 기존 노트도 서버 기동 시 1회 소급 그룹핑
- 노트 frontmatter에 `workdir`/`workspace` 기록, 이어가기 시 같은 작업 위치 유지
- 모델 목록을 CLI에서 동적으로 수집(하드코딩 제거)
- 메모리 사이드바를 **접이식 폴더 UI**로 개편 — 그룹 기본 접힘, 클릭 시 롤링 펼침, 펼침 상태 localStorage 유지

### V2.3 — Council(협의) 모드 (완료, PR #10)
- 여러 에이전트가 제안 → 상호 비평 → 종합하는 3단계 협의 오케스트레이션 (`app/council.py`)
- `asyncio.gather`로 CLI들을 내부 병렬 실행 (세션 충돌 없음 — 각 호출 stateless)
- 서버 재시작 시 협의 잡이 무한 재시도되던 버그 수정

### V3 — 공개 배포 (진행 중)

4개 CLI(claude / antigravity / grok / hermes) 협의(Council) 세션의 제안·비평을
실제 코드 검증과 대조해 세운 로드맵. Phase 0~4로 진행하며, 완료 이력은
[task.md](task.md) 참고.

#### 현재 상태 진단

**이미 강한 점 (차별 모트)**
- 실측 사용량 라우팅 (CodexBar 연동, `app/codexbar.py`) + auto 코칭 힌트
- API 키 없음 — 워커가 API 키 환경변수를 제거하고 구독 CLI만 spawn (`app/worker.py`)
- 작업 큐 + rate limit 감지·자동 재개 (`provider.detect_rate_limit` → `resume_at` 재클레임)
- 스레드당 노트 1개 + N차 append + 워크스페이스 자동 그룹핑 (`app/memory.py`)
- Council(협의) 모드 — 제안→비평→종합 3단계, 내부 병렬 (`app/council.py`)
- **첫 실행 셋업 위저드(`/setup`) + 활성 에이전트 필터링** — CLI 설치 감지,
  선택한 에이전트만 UI·라우팅·협의에 반영 (`app/settings.py`, `app/setup.py`)
- **비전 보드 (V4/V4.1)** — 목표 → 메인 에이전트 계획 → n8n식 편집 DAG →
  하위 에이전트·미디어 자동 실행 (`app/orchestrator.py`, `app/media.py`,
  `/board`, `static/board-editor.js`)
- provider별 병렬 실행 + 협의 배타 (`MAX_CONCURRENT_JOBS`)
- 빌드 도구 없는 경량 스택 (FastAPI + Jinja2 + HTMX + vanilla JS)
- 테스트 328개, MIT 라이선스

**검증된 갭**

| # | 항목 | 근거 | 상태 |
|---|---|---|---|
| G1 | `CONTRIBUTING.md`가 다른 프로젝트 문서 ("Awesome Design MD") | 파일 1행 | ✅ **완료** — 실제 기여 가이드로 교체 |
| G2 | CI·이슈/PR 템플릿 없음 | `.github/` 디렉토리 자체 부재 | ✅ **완료** — `ci.yml`(3.11/3.12) + 이슈·PR 템플릿 |
| G3 | README 스크린샷 0장, 한국어 중심 | 영문은 상단 태그라인뿐 | ✅ **완료** — 영문 기본 README + 스크린샷 4장, 한국어는 `README.ko.md` |
| G4 | 작업/노트 출력이 평문 `<pre>` | `templates/job.html`, `templates/note.html` | ✅ **완료** — marked.js + highlight.js vendored, 코드 복사 버튼 |
| G5 | SSE가 1초 간격 DB 폴링 | `app/main.py` `stream_job` | ✅ **완료** — `app/stream_hub.py` 인메모리 신호 허브. 워커가 DB append 직후 구독자를 깨워 즉시 재조회(프로세스 내 fast-path), 신호 없으면 1초 폴링으로 자연 폴백. DB는 여전히 내용의 단일 출처(복원·다중 워커 안전). 지연 최대 1초 → 실측 ~0ms |
| G6 | 동시 실행 1개 | `app/worker.py` `current` 싱글톤 (의도된 설계) | ✅ **완료** — provider별 직렬 · 서로 다른 provider 병렬(전역 상한 `MAX_CONCURRENT_JOBS`) · 협의 배타 실행. `current` 싱글톤 → `running_procs` 잡별 레지스트리로 교체(취소 정확성). 단일 디스패처가 유일 클레임 주체 + `claim_next_job` 원자성(SELECT~UPDATE 무 await)으로 이중 클레임 없음. 실측 0.6s×2 → 0.66s(병렬) |
| G7 | macOS 종속 | `config.py` iCloud 경로·firmlink·`is_browse_allowed`(홈/iCloud만), `install.sh`=launchd 전용 | ✅ **대부분 완료** — 홈 이하 browse + systemd 유닛. iCloud·launchd는 macOS 전용 유지 |
| G8 | 패키지 설치 불가 | pyproject에 `[build-system]`/`[project.scripts]` 없음 | ✅ **완료** — hatchling + `aos` 엔트리포인트 (`pip install -e .`) |
| G9 | 미설치 CLI가 UI에서 그대로 선택 가능 → 작업 실패 | `models.py` `shutil.which` | ✅ **완료** — `/setup` 위저드가 설치 감지 후 활성 필터링 (`app/settings.py`) |
| G10 | 헬스/진단 엔드포인트 없음 | `/health` 류 라우트 부재 | ✅ **완료** — `/api/health` + `aos doctor` (`app/health.py`) |
| G11 | 멀티턴 채팅·병렬 실행 미구현 | Phase 3·4 후보 | ✅ **완료** — 가짜 멀티턴(노트 버블) + provider 병렬. 진짜 멀티턴 UI는 후속 |
| G12 | Council 모드 결과가 평문 `<pre>`로만 표시 | `app/council.py`는 완성, UI 레이아웃만 부족 | ✅ **부분 완료** — 마크다운 렌더로 구조화. 전용 탭 레이아웃은 보류 |

#### 로드맵

**Phase 0 — 신뢰·첫인상 (대부분 완료)**

코드 기능보다 **"받기 전 설득"**이 먼저다. 전부 저비용·고효과.

- [x] **CONTRIBUTING.md 교체** (G1): 개발 환경 셋업, 테스트 실행, provider 추가 가이드 포함
- [x] **`.github/` 생성** (G2):
  - `workflows/ci.yml` — push/PR 시 pytest (Python 3.11/3.12)
  - Issue 템플릿(버그·기능제안), PR 템플릿
- [x] **README 개편** (G3):
  - `README.md`를 영문 기본으로 전환, 한국어는 `README.ko.md`로 분리
  - 스크린샷 4장 (대시보드·사용량 패널·셋업 위저드 2장)
  - 최상단 가치 제안 블록 + **"Claude Code 하나만 있어도 시작 가능"** 명시
  - CI·라이선스 배지
- [ ] **데모 GIF** — 정적 스크린샷만 있음, 15–30초 동작 GIF는 미착수
- [ ] **GitHub 메타**: description / topics(`ai`, `claude-code`, `multi-agent`, `fastapi`,
      `local-first`, `usage-routing`) / homepage 설정 — **API 도구로 설정 불가, 저장소
      Settings 페이지에서 수동으로 해야 함**
- [x] Security 섹션 유지·강조 (localhost 바인딩 + Tailscale — 이미 좋았음, 그대로 유지)

**검증:** 처음 보는 사람이 README 스크롤 없이 "구독 CLI 여러 개 + 사용량 라우팅"을
이해하는가.

**Phase 1 — UX 완성도 ("부족하다" 체감 해소)**

| 항목 | 내용 | 상태 |
|---|---|---|
| **미설치 CLI 감지** (G9) | 기동 시 `shutil.which` 스캔 → UI 반영, `route_auto`는 활성 provider만 대상 | ✅ **완료** — `/setup` 위저드 + `app/settings.py` |
| **마크다운 렌더** (G4) | job/note 출력에 marked.js + highlight.js **vendored** 적용 (빌드 무관 원칙 유지), 코드 블록 복사 버튼 | ✅ **완료** — `static/render.js`, 라이트/다크 hljs 테마, `.md-body` 스타일 |
| **키보드/테마** | ⌘/Ctrl+Enter 전송, 다크모드 명시 토글(현재 `prefers-color-scheme`만) | ✅ **완료** — 컴포저·재개 폼 ⌘Enter, 사이드바 테마 토글(`data-theme` + localStorage, FOUC 방지 인라인 init) |
| **작업 상태 카드** | 큐에서 running 시 provider/model/경과시간 미니 프리뷰 | ✅ **완료(기존)** — 작업 큐에 provider/model 표시 + running 배지 펄스 애니메이션 |
| **에러 UX** | CLI 미설치/미인증/TCC 실패를 친화적 배너로 — 조용한 실패 제거 | ✅ **완료** — job 상세에 실패 원인 해석 배너(CLI 없음/미인증/타임아웃/취소) + `/setup` 링크 |
| **인라인 스트리밍 UX** | 전송 후 페이지 이동 최소화; stdout→SSE 직통 검토(G5, 현 1초 DB 폴링 대체) | ✅ **완료** — `stream_hub` 인메모리 신호로 stdout→SSE 직통(1초 폴링 폴백 유지, 설계 원칙 무손상) |

**의도적으로 미룸:** React 전환, 디자인 시스템 전면 교체, 모바일 네이티브 앱.
HTMX + vanilla 스택은 이 제품 규모에 맞다.

**Phase 2 — "clone → 첫 성공" 마찰 제거 (대부분 완료)**

- [x] **CLI 설치·보조도구 감지 API** — `/api/setup/status`(`app/setup.py`)가
      claude/agy/grok/hermes 설치 여부 + codexbar/gh/rg 감지 (재확인 버튼으로 재조회)
- [x] **`bootstrap.sh`**: uv/venv 감지 + deps + `aos.env` 시드 + 포트 확인 + 브라우저
      오픈까지 한 번에. macOS·Linux 공용 (기존 `install.sh`=launchd 등록과 역할 분리)
- [x] **범용 `/api/health` + `aos doctor`** (`app/health.py`, `app/__main__.py`):
      Python/플랫폼/포트/데이터 쓰기 가능 여부 + CLI·보조도구 감지 + 셋업 상태.
      `python -m app doctor`로 터미널 진단, `/api/health`로 JSON
- [x] **플랫폼 독립화** (G7):
  - Linux smoke 확인 — `is_browse_allowed`는 이미 "홈 이하"로 동작하고
    iCloud/CloudStorage는 `.is_dir()` 가드로 자동 우회(크래시 없음). 홈 제한은
    보안상 유지(임의 경로 실행 방지)
  - `deploy/agentic-os.service` systemd 사용자 서비스 템플릿 (launchd와 병행)
- [x] **패키징** (G8): pyproject에 `[build-system]`(hatchling) + `[project.scripts]`
      `aos` 엔트리포인트. `pip install -e .` 시 `aos`/`aos doctor` 명령
      (editable라 templates/static/data 경로 유지). 독립 wheel(자산 번들)은 후속
- [x] **UI i18n**: 영문 기본 + 한국어 (`app/i18n.py` — 한국어 원문을 키로 쓰고 영어
      매핑, 미번역 시 원문 폴백). 언어는 쿠키 > Accept-Language > 영어로 결정,
      사이드바 토글(`/lang/{code}`)로 전환. 템플릿은 `{{ '한국어' | t }}` 필터
      (pass_context로 상수 폴딩 방지), JS는 `window.I18N`+`t()` 헬퍼로 처리

**성공 지표:** 문서만 보고 Time-to-first-job ≤ 15분 (Claude CLI 있는 Mac 기준).

**Phase 3 — 차별 기능 가시화 (대부분 완료)**

기능은 있어도 UI에 안 보이면 없는 것과 같다.

- [x] **라우팅 투명성**: auto 라우팅 이유를 `jobs.route_reason` 컬럼에 고정 기록.
      작업 상세에 "🔀 자동 라우팅 — …" 표시, 작업 큐에 `자동` 태그 + 툴팁
- [x] **사용량 대시보드 강화**: 리셋 카운트다운(기존) + 에이전트별 "최근 24시간
      N회 실행"(usage_log 기반, `partials/usage.html`)
- [x] **Provider 플러그인 계약 문서화**: `docs/PROVIDERS.md` — `build_command` /
      `parse_output` / `detect_rate_limit` 계약 + 등록·셋업 감지·사용량 연동 가이드
      (외부 기여자가 Codex/Aider/Cursor 등 추가 가능)
- [x] **Council 모드 결과 가독성** (G12): Phase 1 마크다운 렌더링으로 협의 출력
      (`##`/`###` 헤딩·제안·비평·종합)이 이미 구조적으로 렌더됨. 전용 탭 레이아웃은
      선택 개선으로 보류(마크다운 파싱 취약성 회피)
- [x] **병렬 실행** (G6): provider별 직렬 + 서로 다른 provider 병렬
      (`MAX_CONCURRENT_JOBS`, `running_procs` 레지스트리). 협의 잡은 배타 실행.
      비전 보드 태스크도 같은 워커 큐로 돌며 프로젝트당 `ORCH_MAX_INFLIGHT` 상한

**Phase 4 — 멀티턴 채팅 (완료)**

- [x] **가짜 멀티턴**: 백엔드는 기존 노트 append(`memory.py` `append_note`,
  `## 프롬프트/결과 (N차)`) + 세션 resume 그대로 두고, **노트 뷰를 채팅 버블로
  렌더**. `memory.parse_thread`가 본문을 사용자/에이전트 턴으로 파싱하고
  `note.html`이 좌우 말풍선(마크다운·코드 하이라이트 포함)으로 표시. 바로 아래
  "이어서 진행" 입력창이 이어져 연속 대화처럼 읽힌다. 스레드 형식이 아니면
  기존 전체 본문 렌더로 폴백
- [x] **grok 진짜 세션 재개** — CLI 검증 결과 grok은 `--session-id <uuid>`(새
  대화에 우리가 UUID 부여) + `--resume <uuid>`(그 세션만 정확히 재개)를 지원.
  `-c`(최신 이어가기)의 오염(다른 대화가 끼면 엉뚱한 세션으로 이어짐)을 원천
  차단하도록 `GrokProvider`를 UUID 자가 발급 방식으로 교체. provider 직렬화
  덕에 build_command→parse_output 간 UUID 핸드오프가 동시성 안전
- [x] **agy 이어가기 비활성** (결정) — 검증 결과 agy는 헤드리스 세션 재개가
  불가능: `--session-id` 자가 발급 옵션 없음, `agy -p` 출력에 conversation ID
  미포함(`agy -p "..."` → 답변 텍스트만), JSON 출력·`sessions` 목록 서브커맨드
  없음. `-c`(cwd 무관 전역 최신)는 오염 위험이 가장 커 유지하지 않기로 결정.
  → agy는 단발 실행만: `build_command`가 session_id 무시(-c 제거), `parse_output`
  이 session_id=None(이어가기 대상 미표시), 노트 뷰 `can_resume`에서 제외,
  `create_job`이 agy의 session_id를 드롭(UI 우회 제출 방어)

#### 하지 말 것 (과투자 방지)

1. **Tauri/Electron/React 전면 재작성** — 빌드 없는 경량 스택이라는 강점 파괴,
   코드서명 비용·구독 CLI OAuth 연동 리스크. 채택이 검증된 뒤 장기 옵션으로만
2. **Docker를 주 설치 경로로** — 호스트 CLI의 GUI/OAuth 로그인·TCC·파일시스템과
   충돌. 로컬 프로세스 모델이 이 제품의 정답
3. **완전한 멀티턴 올인** — CLI별 `-p`/resume 동작 불일치가 해소되기 전에는 가짜
   멀티턴으로 체감만 먼저
4. **Claude 스킨 복제** — "미완성 Claude"로 오해되고 오케스트레이터 모트가 사라짐
5. **초기 코드서명·원클릭 인스톨러 필수화** — OSS 초기에는 bootstrap + 문서가 우선

#### 성공 지표

| 지표 | 목표 |
|---|---|
| Time-to-first-job (문서만 보고, Claude CLI 있는 Mac) | ≤ 15분 |
| README 스크롤 없이 가치 이해 | 지인 3명 테스트 통과 |
| CI | 메인 브랜치 상시 green |
| 외부 이슈/스타 등 피드백 | Phase 0 완료 후 4주 내 ≥ 1건 |
| "투박하다" 피드백 중 렌더·흐름 비율 | 마크다운 + 인라인 스트림 후 감소 |

### V4 — 비전 보드 (2026-07-25 구현)

메인 오케스트레이터 에이전트가 프로젝트 목표를 태스크 DAG로 분해하고, 승인 후
하위 에이전트들이 의존성 순서대로 자동 실행한다. 진행은 n8n식 워크플로우
그래프(서버 렌더링 SVG + HTMX 폴링)로 실시간 관찰.

- **데이터**: `projects`/`tasks` 테이블 (db.py). 태스크는 기존 `jobs`로 실행 →
  레이트리밋 재개·직렬화·취소·재시작 복구를 워커에서 그대로 상속.
- **엔진**: `app/orchestrator.py` — 계획(JSON 계약 + 파싱 실패 시 자동 1회 재시도)
  → 승인 → `_advance` 멱등 루프(동기화·디스패치·일시정지·재시도·재계획).
  핸드오프는 무상태(상류 출력을 하류 프롬프트에 주입, council 방식).
- **미디어**: `app/media.py` — 가상 provider `media`. 이미지=agy/grok CLI(구독),
  비디오=grok CLI, 오디오=Gemini API TTS(옵트인 `GEMINI_API_KEY`, 인프로세스 전용).
  산출물은 `data/artifacts/{project}/`, 출력 마지막 줄 = 경로 계약.
- **UI**: `/board`(프로젝트 목록·컴포저), `/projects/{id}`(DAG 그래프 + 태스크
  상세 패널). 상태별 노드 색·펄스, 미디어 썸네일·플레이어.
- v1 제외(당시 후보): 태스크 사이 플래너 재검토 루프, 드래그앤드롭, 태스크별 모델
  선택, 보드 칸반 뷰. → **드래그앤드롭·의존성 편집은 V4.1에서 완료.** 나머지
  (플래너 재검토 루프·태스크별 모델·칸반)는 후속 후보 유지.
- **[완료] 보드 컴포저에 메인 챗 기능(모델·워크스페이스·도구) 포함**:
  - `templates/board.html` 프로젝트 폼에 `enctype="multipart/form-data"` +
    `#file-chips` + `#tools-btn`/`#tools-popup`(files / attach_memory /
    timeout_min)를 메인 composer와 동일한 엘리먼트 id로 추가 → `static/app.js`
    기존 파일칩·드래그앤드롭·팝업 로직을 JS 수정 없이 그대로 재사용(null-guard).
  - 백엔드(`POST /projects`, `orchestrator.start_project`)는 이미 업로드 저장,
    메모리 컨텍스트 선행, `timeout_sec`→plan job 전달을 처리(선행 태스크에서 확인).
  - 2026-07-25 회귀 테스트: pytest 전체 295건 통과, i18n 영문 기본 폴백 스모크 통과.
    선행 태스크에서 보고된 `codex`→`claude` 폴백 4실패는 `codex`가 더 이상 실제
    PROVIDERS(claude/antigravity/grok/hermes)에 없음에 기인 — 테스트 픽스처를
    유효 provider(`grok`)로 핫픽스해 해소.

### V4.1 — 워크플로 다이어그램 편집기 (2026-07-25 구현)

계획이 나온 DAG를 읽기 전용 그림이 아니라 편집 가능한 캔버스로 바꿨다. 계획이
조금 어긋났을 때 유일한 선택지가 "재계획"(전체 폐기)이던 문제와, 가로 레이아웃
탓에 모바일에서 흐름이 안 보이던 문제를 함께 해결한다.

- **동시성**: 구조 편집은 `plan_ready`/`paused`에서만 허용. `db.active_projects()`가
  `planning`/`running`만 반환하므로 이 상태에서는 오케스트레이터 루프가 프로젝트를
  전진시키지 않는다 — 편집과 잡 디스패치가 경합할 수 없다(락 불필요).
  태스크 단위로는 `pending`/`failed`만 대상(완료 결과·실행 중 잡 보호).
  노드 위치 이동은 순수 시각 요소라 모든 상태에서 허용.
- **검증 공유**: `parse_plan`의 Kahn 순환 검사를 `_assert_acyclic()`으로,
  타입↔provider 해소 규칙을 `resolve_provider()`로 추출해 계획 파싱과 편집 API가
  같은 코드를 쓴다 — 편집으로 계획 계약을 우회할 수 없다.
- **데이터**: `tasks.pos_x/pos_y`(nullable, `_migrate` ALTER). NULL = 자동 배치.
  `depends_on`은 콤마 문자열 유지(스키마 변경 최소화).
- **레이아웃**: `layout_graph(tasks, orientation)` — `lr`은 저장 좌표 우선,
  `tb`는 깊이를 아래로 쌓고 저장 좌표를 무시(모바일은 항상 재정렬).
  캔버스 크기는 실제 노드 경계에서 계산.
- **라우트**: 편집 액션 6종이 리다이렉트 대신 보드 조각을 되돌려 준다 →
  htmx가 `#board`만 교체해 캔버스 팬/줌·선택이 유지된다. 검증 실패는 400 + 배너.
- **캔버스**: `static/board-editor.js` — Pointer Events(마우스·터치 단일 경로),
  viewBox 팬/줌·핀치, 노드 드래그, 포트↔포트 연결, 연결선 클릭 삭제, 팔레트.
  편집 가능 상태에서는 2초 폴링을 끈다(갱신할 상태가 없고 드래그를 끊는다).
- **접근 경로 이중화**: 캔버스에서 선을 긋기 어려운 터치 환경을 위해 태스크
  상세 패널에 선행 태스크 체크박스를 함께 둔다(같은 `deps` 엔드포인트).
- 2026-07-25 검증: pytest 328건 통과(신규 33건), Chromium 실기기 프로파일로
  드래그·연결·순환 거부·삭제·추가·정렬·핀치줌·하단 시트까지 확인.

### V4.2 — 워크플로 편집기 MVP 완성 (다이어그램 편집 + 태스크 CRUD + 모바일 재정렬, 예정)

V4.1이 "편집 가능한 캔버스" 기반을 닦았다면, V4.2는 목표 3대 축 중 아직 미완인
**태스크 선택·수정/삭제의 실제 완성**과 **실행 가시성**을 한 사이클로 묶는다.
후보 풀과 우선순위(P0~P2)는 V4.1 구현 직후 수집한 선행 태스크 [6]에서 왔다.

> 원문 각주: 이 섹션은 2026-07-25 노트(태스크 6 우선순위 → MVP 로드맵)를 통합한
> 것. 노트 원문이 "## M"에서 잘려 **§1(MVP in/out)만 온전**하고 §2~§5는 누락돼,
> 아래 §2~§5는 동 노트의 **완전한 우선순위 표(Table [6])를 근거**로 재구성했다
> (값/난이도/의존성/P0 여부는 원표 그대로). 추후 원본 §2~§5 확보 시 대조 필요.

#### 1. MVP 범위 (in / out)

**In (MVP)**
- #1 노드 실행 상태 오버레이 — pending/running/done/failed 색상 + pulse, 상태만
  diff 적용하는 경량 폴링 엔드포인트 (`layout_graph` 확장, 의존성 없음)
- #2 필수값/미설정 경고 표시 — agent 미지정·description 공백 시 노드 테두리 경고 점
  (`has_warning`, 의존성 없음)
- #3 1-depth Undo — 구조 편집(이동/연결/삭제) 직후 직전 스냅샷 1개 캐싱, "실행 취소" 버튼
- #4 삭제 확인 + 의존성 경고 다이얼로그 — 하위 태스크 존재 시 cascade/unlink 선택,
  soft-delete + 5초 실행취소 토스트 (신규 `status=deleted` 스키마 필요)
- #5 필드별 부분 PATCH + draft 저장 흐름 — title/description 인라인 편집, blur 500ms
  debounce, 폴링과 draft 충돌 방지 (현 `/tasks/{id}/edit` 확장)
- 모바일 재정렬 (목표 3대 축 중 하나, 후보 표에는 없었으나 목표에 명시) — 기존 diagram
  뷰의 반응형 전환(가로 DAG → 세로 스택/아코디언, `layout_graph`의 `tb` 모드 활용)

**Out (MVP 제외, 후속 페이즈로)**
- P1 전체: #6 템플릿 갤러리, #7 키보드 단축키, #8 Import/Export, #9 박스 셀렉트, #10 연결 사전 유효성
- P2 전체: #11 격자 정렬 스냅, #12 실행 히스토리 재생, #13 노드별 테스트 실행, #15 접근성, #16 비용/토큰 추정
- 명시적 보류: #14 협업 커서/코멘트 (단일 사용자 로컬 도구라 WebSocket 실시간 계층 정당화 안 됨)
- 미니맵은 벤치마크([2])에서 이미 후순위 보류 확정 → 후보에서 제외

#### 2. 페이즈별 기능 목록 (Table [6] 기준)

| 페이즈 | 포함 기능 | 가치/난이도 | 비고 |
|---|---|---|---|
| **MVP (V4.2)** | #1 상태 오버레이, #2 경고, #3 Undo, #4 soft-delete, #5 부분 PATCH, 모바일 재정렬 | 5/2·4/1·4/2·4/2·4/2·- | 모두 기존 스키마/API 소폭 확장 수준 |
| **P1** | #6 템플릿 갤러리, #7 키보드 단축키, #8 Import/Export, #9 박스 셀렉트, #10 연결 사전 유효성 | 4/3·3/1·4/3·3/3·3/2 | 신규 테이블(#6,#8)·인터랙션 레이어 확장(#9,#10) |
| **P2** | #11 격자 정렬, #12 실행 히스토리 재생, #13 노드별 테스트 실행, #15 접근성, #16 비용/토큰 추정 | 2/2·3/4·3/3·2/3·3/3 | 신규 인프라(이력 로깅·실행 격리·토큰 계측) 요구 |
| **보류** | #14 협업 커서/코멘트 | 2/5 | 단일 사용자 전제라 보류 권장 |

#### 3. 각 페이즈 완료 기준 (수용 기준)

- **MVP 완료 기준**
  - 편집 중에 노드 상태 오버레이로 pending/running/done/failed 진행 상황을 실시간 확인
  - agent 미지정·description 공백 노드에 경고 점 표시 (`has_warning`)
  - 구조 편집(이동/연결/삭제) 실수 시 1-depth Undo로 즉시 복구
  - 태스크 삭제 시 하위 의존성 경고 다이얼로그 → cascade/unlink 선택, soft-delete +
    5초 실행취소 토스트로 오삭제 방지
  - title/description 인라인 편집이 blur 500ms debounce로 즉시 반영되고, 상태 폴링과
    draft가 충돌하지 않음
  - 모바일(좁은 화면)에서 DAG가 위→아래 세로 흐름으로 재정렬되어 가독
  - 신규 pytest 추가, 2초 폴링 중단 상태에서 드래그/연결/undo/삭제 정상 동작 확인
- **P1 완료 기준**: 템플릿 저장/재사용, 키보드 단축키(Delete/Ctrl+Z/Space+드래그/+/-/Esc),
  Graph Document JSON(`agentic-os.workflow` 스키마) 내보내기/재도입, 박스 셀렉트 다중
  이동/삭제, 연결 드래그 중 순환·완료노드 시도 시 즉시 빨강 피드백
- **P2 완료 기준**: "정렬" 버튼 격자 스냅, 완료 프로젝트 타임라인 스크러버 재생, 노드별
  격리 재실행(상류 output mock), SVG 노드 `role`/`aria-label`+Tab 순회, 태스크별
  비용/토큰 배지 표시

#### 4. 리스크 · 오픈 이슈

| # | 항목 | 근거 / 영향 | 상태 |
|---|---|---|---|
| R1 | `soft-delete` 스키마 신규 마이그레이션 | #4에 `tasks.status=deleted` 컬럼/필드 추가 필요 (`_migrate` ALTER) | 미착수 |
| R2 | 폴링↔draft 충돌 설계 | #5에서 상태 폴링(2s)과 인라인 draft 저장이 같은 노드를 두고 경합 — 충돌 해소 규칙 필요 | 미착수 |
| R3 | `agentic-os.workflow` 스키마 v1 확정 | #8 Import/Export가 스키마 확정([5] 산출물)에 선행 종속 | 미착수 |
| R4 | 실행 이력 로깅 인프라 | #12 타임라인 재생이 태스크 상태변경 이력 신규 테이블 필요 | 미착수 |
| R5 | 노드별 실행 격리 로직 | #13이 orchestrator 실행 격리(상류 output mock) 필요 | 미착수 |
| R6 | 토큰/비용 계측 파이프라인 부재 | #16이 provider별 토큰·비용 계측 인프라 선행 필요 — 의존성 큼 | 미착수 |
| R7 | 협업 커서 보류 | #14는 WebSocket 실시간 계층 신설이 필요한데 현재 단일 사용자 로컬 도구라 정당화 안 됨 | 보류 |

#### 5. 다음 구현 스프린트 분해 초안 (MVP, 7개)

1. **soft-delete 스키마** — `tasks`에 `status=deleted` 마이그레이션 + 5초 실행취소 토스트
2. **상태 오버레이 엔드포인트** — 경량 폴링(상태만 diff) + pending/running/done/failed 색상·펄스
3. **필수값 경고** — `has_warning` 계산(agent 미지정·description 공백) → 노드 테두리 경고 점
4. **1-depth Undo** — 구조 편집 직후 스냅샷 1개 캐싱 + "실행 취소" 버튼
5. **부분 PATCH + draft** — title/description 인라인 편집, blur 500ms debounce, 폴링 충돌 방지
6. **삭제 확인 + 의존성 경고** — 하위 태스크 존재 시 cascade/unlink 선택 다이얼로그
7. **모바일 반응형 재정렬** — diagram 가로 DAG → 세로 스택/아코디언 전환 (`layout_graph` `tb` 모드)

### V4.3 — 채널 + Orca 스타일 프로젝트 레이아웃 (구현 완료, 2026-07-30)

[2026-07-30 와이어프레임](2026-07-30-orca-layout-wireframe.md)에서 설계한 "좌측 고정
채팅 + 중앙 진행 흐름" 레이아웃을 실제로 구현. 상세는 [task.md](task.md) V4.3 절.

- 신규 최상위 기능인 **채널**(`channels`/`messages` 테이블 + `/api/channels*`)이
  DAG 편집기와는 별도로 사이드바에 상시 노출 — 기존 "노트"(1샷 잡 기록)와 달리
  진짜 멀티턴 스레드 채팅
- 프로젝트 페이지는 chat-rail(좌, 채널 채팅 + 실행 로그 탭) + vision-flow(중앙,
  `#board` DOM을 소스로 하는 카드/트리 뷰)로 재구성
- 와이어프레임의 "우측 inspector 고정 패널" 승격은 이번 구현에 미포함 — 기존
 `task-detail` 팝오버/바텀시트 그대로 유지 (후속 후보)

### V4.6 — 홈 대시보드 셸 재구조화 (진행 중)

V4.3의 Orca 스타일 레이아웃을 홈(`/`)에 완성한다. 기존 좌측 사이드바(비전보드
세계)를 우측 레일의 탭으로 옮기고, 채팅 레일에 검색·정렬·이름 변경을 더한다.
상세 설계는 [대시보드 재구성 doc](plan-home-dashboard-restructure.md) 참고.

- **좌측 사이드바 축소** — 브랜드 + 설정 줄만 남기고 폭 300px→200px로 축소.
  비전보드(프로젝트 목록·비전보드 채팅)와 태스크 인스펙터는 우측 레일로 이동.
- **우측 레일 탭 재편** — 채팅 / 프로젝트 / 태스크(보드 노드 클릭 시에만 나타남).
  기존 '노트'·'세션' 탭은 홈에서 제거(노트 API·채널 API는 프로젝트 페이지에서 그대로).
- **채팅 레일 검색·정렬·이름 변경**:
  - 상단 도구줄(검색창 + 정렬 토글) 추가
  - 정렬: 시간순(기본) / 프로젝트별(작업 위치 그룹, 접이식)
  - 말풍선 ✎ 클릭으로 그 자리에서 세션 이름 변경(`POST /jobs/{id}/rename`,
    `jobs.title` 컬럼 신규 마이그레이션). 빈 값이면 프롬프트가 다시 제목.
  - 검색은 이름·에이전트·작업 위치를 대소문자 무시하고 매칭
  - 정렬·접힘 상태는 localStorage 유지
- **태스크 인스펙터** — 우측 레일 전용 '태스크' 탭이 보드 노드 클릭 시 통째로 열림
  (`showRailPanel("task")`, 좁은 화면은 레일 오프캔버스 자동 오픈)

### V5 — 확장 기능 후보 (예정)

멀티턴 채팅 UI·병렬 작업 실행은 V3 Phase 3·4로 이미 편입됐다. 아래는 그 외 후보.

- **Antigravity 사용량 실측**: CodexBar 또는 별도 연동 (현재 "정보 없음")
- **세션 재개 실측 보정**: antigravity/grok의 `--resume`, 모델 id 실제 검증
- **벡터 검색**: 메모리 임베딩 기반 의미 검색
- **Dreaming 자기개선 엔진**: 유휴 시 과거 작업 회고·요약
- **토큰/비용 추적**: 작업별 토큰·비용 집계

## 핵심 설계 원칙

- 유료 구독 CLI 헤드리스 모드만 사용 (API 키 미사용 → 추가 과금 없음)
- 워커가 API 키 환경변수를 제거하고 CLI를 spawn (구독 과금 보장)
- 빌드 도구 없는 프론트엔드 (Jinja2 + HTMX + vanilla JS)
- 앱은 `127.0.0.1` 바인딩 유지, 외부 접속은 Tailscale serve가 프록시
- 파괴적/외부 노출 동작은 명시적 확인 후 실행


## 2026-07-22 devlog 테스트 항목
- devlog 프롬프트 패치 검증을 위한 계획 갱신 테스트.
- 실제 계획 변경 예시: 리포별 wiki 볼트 라우팅과 코드→위키 자동 동기화 파이프라인을
  master 브랜치 기준으로 안정화. 감시 체크아웃이 죽은 기능 브랜치에 정박하는 문제를
  발견하고 master 로 전환, upstream 연결 완료.
- 다음 단계: HermesChat 개발 진행도 동일한 devlog 방식으로 log.md 에 기록되는지 확인.
- 참고 항목 1
- 참고 항목 2
- 참고 항목 3
- 참고 항목 4
- 참고 항목 5
- 참고 항목 6
- 참고 항목 7
- 참고 항목 8
- 참고 항목 9
- 참고 항목 10
- 추가 항목 11
- 추가 항목 12
- 추가 항목 13
- 추가 항목 14
- 추가 항목 15
