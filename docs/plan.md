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
- 빌드 도구 없는 경량 스택 (FastAPI + Jinja2 + HTMX + vanilla JS)
- 테스트 200개 이상, MIT 라이선스

**검증된 갭**

| # | 항목 | 근거 | 상태 |
|---|---|---|---|
| G1 | `CONTRIBUTING.md`가 다른 프로젝트 문서 ("Awesome Design MD") | 파일 1행 | ✅ **완료** — 실제 기여 가이드로 교체 |
| G2 | CI·이슈/PR 템플릿 없음 | `.github/` 디렉토리 자체 부재 | ✅ **완료** — `ci.yml`(3.11/3.12) + 이슈·PR 템플릿 |
| G3 | README 스크린샷 0장, 한국어 중심 | 영문은 상단 태그라인뿐 | ✅ **완료** — 영문 기본 README + 스크린샷 4장, 한국어는 `README.ko.md` |
| G4 | 작업/노트 출력이 평문 `<pre>` | `templates/job.html`, `templates/note.html` | ✅ **완료** — marked.js + highlight.js vendored, 코드 복사 버튼 |
| G5 | SSE가 1초 간격 DB 폴링 | `app/main.py` `stream_job` | 미착수 |
| G6 | 동시 실행 1개 | `app/worker.py` `current` 싱글톤 (의도된 설계) | 미착수 |
| G7 | macOS 종속 | `config.py` iCloud 경로·firmlink·`is_browse_allowed`(홈/iCloud만), `install.sh`=launchd 전용 | 미착수 |
| G8 | 패키지 설치 불가 | pyproject에 `[build-system]`/`[project.scripts]` 없음 | 미착수 |
| G9 | 미설치 CLI가 UI에서 그대로 선택 가능 → 작업 실패 | `models.py` `shutil.which` | ✅ **완료** — `/setup` 위저드가 설치 감지 후 활성 필터링 (`app/settings.py`) |
| G10 | 헬스/진단 엔드포인트 없음 | `/health` 류 라우트 부재 | 부분 완료 — `/api/setup/status`가 CLI·보조도구 감지 제공, 범용 `/api/health`는 미착수 |
| G11 | 멀티턴 채팅·병렬 실행 미구현 | Phase 3·4 후보 | 미착수 |
| G12 | Council 모드 결과가 평문 `<pre>`로만 표시 | `app/council.py`는 완성, UI 레이아웃만 부족 | 미착수 |

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
| **인라인 스트리밍 UX** | 전송 후 페이지 이동 최소화; stdout→SSE 직통 검토(G5, 현 1초 DB 폴링 대체) | 미착수 (선택 — 현 SSE로도 동작) |

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
- [ ] **UI i18n**: 영문 기본 + 한국어 (템플릿 `lang="ko"` 하드코딩 해소) — **미착수,
      규모 큰 UI 번역이라 별도 진행**

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
- [ ] **병렬 실행** (G6): `worker.py` 싱글톤 → provider 단위 동시성 — **보류**.
      "동시 1개"는 CLI 세션·메모리 충돌 방지를 위한 의도된 설계 원칙이라, 안전한
      provider 단위 격리 검증 전까지는 리스크가 커 후속 과제로 남김

**Phase 4 — 멀티턴 채팅 (체감 최대·비용도 최대)**

- **가짜 멀티턴 우선 (권장)**: 백엔드는 기존 노트 append(`memory.py` `append_note`,
  `## 프롬프트/결과 (N차)`) + 세션 resume 그대로, **프론트만 채팅 버블 뷰**로 렌더.
  구조 변경 없이 Claude 스타일 대화 체감을 얻는 가성비 최고 경로
- **진짜 대화 상태(메시지 테이블)**는 antigravity/grok의 resume 동작 검증 이후
  재평가 — 현재 agy/grok은 `-c`(최신 이어가기)만 지원해 완전한 멀티턴 정합성이
  깨질 수 있음

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

### V4 — 확장 기능 후보 (예정)

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
