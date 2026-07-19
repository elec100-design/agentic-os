# Agentic OS — 공개 배포 마스터플랜

agentic-os를 "내 Mac Mini 전용 도구"에서 **다른 사람들도 GitHub에서 받아 편하게 쓸 수
있는 범용 로컬 웹앱**으로 발전시키기 위한 전체 계획. 4개 CLI(claude / antigravity /
grok / hermes) 협의(Council) 세션의 제안·비평을 실제 코드 검증과 대조해 종합했다.
기존 로드맵은 [plan.md](plan.md), 완료 이력은 [task.md](task.md) 참고.

---

## 1. 비전·포지셔닝

**한 줄 포지션:**

> *One local dashboard for all your paid AI CLIs — routes by real remaining quota,
> queues work, resumes after rate limits, and files results into notes.*

**전략: Claude/Grok UI와 정면 승부하지 않는다.** Claude Desktop·Grok은 단일 제공자
채팅 제품이고, Agentic OS는 **여러 구독 CLI를 실측 사용량으로 묶는 로컬
오케스트레이터**다. 비교 축이 다르므로 채팅 폴리시를 복제하는 대신 고유 가치를
선명하게 만든다:

1. 여러 구독을 **한 입력창**에서
2. **실측 남은 사용량**으로 자동 배분 (API 키 불필요 → 추가 과금 없음)
3. rate limit 시 **큐에 넣고 자동 재개**
4. 결과를 **노트/Obsidian 볼트에 영구 축적**
5. (신규) 여러 에이전트가 교차검증하는 **Council 모드**

**UI 개선의 성공 기준**은 "Claude처럼 예쁘다"가 아니라:
**작업을 던지고 → 누가 도는지 보이고 → 결과가 읽히고 → 이어가기 쉽다.**

**배포 모델: 로컬 셀프호스트 유지.** `127.0.0.1` 바인딩 + Tailscale 외부 접속이라는
현재 보안 모델을 그대로 공개한다. 호스팅 SaaS(인증·멀티테넌시)는 아키텍처 전면
재설계가 필요하므로 **비목표**로 명시한다. 데스크톱 앱(Tauri) 패키징은 채택이 늘고
코드서명 여력이 생긴 뒤 재검토하는 장기 옵션.

---

## 2. 현재 상태 진단 (코드 검증 완료)

### 이미 강한 점 (차별 모트)

- 실측 사용량 라우팅 (CodexBar 연동, `app/codexbar.py`) + auto 코칭 힌트
- API 키 없음 — 워커가 API 키 환경변수를 제거하고 구독 CLI만 spawn (`app/worker.py`)
- 작업 큐 + rate limit 감지·자동 재개 (`provider.detect_rate_limit` → `resume_at` 재클레임)
- 스레드당 노트 1개 + N차 append + 워크스페이스 자동 그룹핑 (`app/memory.py`)
- 빌드 도구 없는 경량 스택 (FastAPI + Jinja2 + HTMX + vanilla JS)
- 테스트 10파일 ~2,000줄, MIT 라이선스

### 검증된 갭

| # | 항목 | 근거 | 영향 |
|---|---|---|---|
| G1 | `CONTRIBUTING.md`가 다른 프로젝트 문서 ("Awesome Design MD") | 파일 1행 | 신뢰 즉시 훼손 — **확정 버그** |
| G2 | CI·이슈/PR 템플릿 없음 | `.github/` 디렉토리 자체 부재 | 테스트가 있어도 "메인이 green인지" 알 수 없음 |
| G3 | README 스크린샷 0장, 한국어 중심 | 영문은 상단 태그라인뿐 | 글로벌 첫인상·발견성 |
| G4 | 작업/노트 출력이 평문 `<pre>` | `templates/job.html`, `templates/note.html` | "투박함" 체감 1순위 |
| G5 | SSE가 1초 간격 DB 폴링 | `app/main.py` `stream_job` | 진짜 토큰 스트리밍 아님 |
| G6 | 동시 실행 1개 | `app/worker.py` `current` 싱글톤 (의도된 설계) | 대기 체감 — 병렬은 V3 후보 |
| G7 | macOS 종속 | `config.py` iCloud 경로·firmlink·`is_browse_allowed`(홈/iCloud만), `install.sh`=launchd 전용 | Linux/WSL 사용자 배제 |
| G8 | 패키지 설치 불가 | pyproject에 `[build-system]`/`[project.scripts]` 없음 | `git clone` + 수동 uvicorn만 가능 |
| G9 | 미설치 CLI가 UI에서 그대로 선택 가능 → 작업 실패 | 경로 탐지는 있으나(`models.py` `shutil.which`) UI 반영 없음 | 신규 사용자 첫 실패 확률↑ |
| G10 | 헬스/진단 엔드포인트 없음 | `/health` 류 라우트 부재 | "왜 안 되지?" 디버깅 불가 |
| G11 | 멀티턴 채팅·병렬 실행 미구현 | plan.md V3 후보 | Claude/Grok 대비 최대 UX 갭 |
| G12 | Council 모드 **미구현** | 저장소 전체 grep 0건 | 협의 출력에서 "이미 있음"으로 언급됐으나 사실과 다름 — 신규 구현 필요 |

---

## 3. 로드맵

### Phase 0 — 신뢰·첫인상 (Week 1–2, 채택 ROI 최고)

코드 기능보다 **"받기 전 설득"**이 먼저다. 전부 저비용·고효과.

- [ ] **CONTRIBUTING.md 교체** (G1): 개발 환경 셋업, 테스트 실행, provider 추가 가이드 포함
- [ ] **`.github/` 생성** (G2):
  - `workflows/ci.yml` — push/PR 시 pytest (Python 3.11/3.12), README에 배지
  - Issue 템플릿 (bug: OS / CLI 버전 / doctor 출력 첨부), PR 템플릿
- [ ] **README 개편** (G3):
  - `README.md`를 영문 기본으로 전환, 한국어는 `README.ko.md`로 분리
  - 스크린샷 3–5장 (대시보드·사용량 패널·작업 스트리밍·노트 스레드) + 15–30초 데모 GIF
  - 최상단 "30초 가치 제안" 블록 + **"Claude Code 하나만 있어도 시작 가능"** 명시
- [ ] **GitHub 메타**: description / topics(`ai`, `claude-code`, `multi-agent`, `fastapi`,
      `local-first`, `usage-routing`) / homepage 설정
- [ ] Security 섹션 유지·강조 (localhost 바인딩 + Tailscale — 이미 좋음)

**검증:** 처음 보는 사람이 README 스크롤 없이 "구독 CLI 여러 개 + 사용량 라우팅"을
이해하는가.

### Phase 1 — UX 완성도 (Week 3–5, "부족하다" 체감 해소)

| 항목 | 내용 | 난이도 |
|---|---|---|
| **마크다운 렌더** (G4) | job/note 출력에 marked.js + highlight.js **vendored** 적용 (빌드 무관 원칙 유지), 코드 블록 복사 버튼 | 중 |
| **미설치 CLI 감지** (G9) | 기동 시 `shutil.which` 스캔(기존 `models.py` 로직 확장) → UI 비활성 칩, `route_auto`는 설치된 provider만 대상 | 중·즉시효과 |
| **키보드/테마** | ⌘/Ctrl+Enter 전송, 다크모드 명시 토글(현재 `prefers-color-scheme`만) | 하 |
| **작업 상태 카드** | 큐에서 running 시 provider/model/경과시간 미니 프리뷰 | 하 |
| **인라인 스트리밍 UX** | 전송 후 페이지 이동 최소화; stdout→SSE 직통 검토(G5, 현 1초 DB 폴링 대체 — 각 CLI `-p` 모드의 stdout 버퍼링 동작 검증 선행) | 중 |
| **에러 UX** | CLI 미설치/미인증/TCC 실패를 친화적 배너로 — 조용한 실패 제거 | 중 |

**의도적으로 미룸:** React 전환, 디자인 시스템 전면 교체, 모바일 네이티브 앱.
HTMX + vanilla 스택은 이 제품 규모에 맞다.

### Phase 2 — "clone → 첫 성공" 마찰 제거 (Week 6–8)

- [ ] **`bootstrap.sh`**: uv 확인 + venv + deps + `aos.env` 시드 + 포트 확인 + 브라우저
      오픈까지 한 번에 (기존 `install.sh`=launchd 등록과 역할 분리)
- [ ] **`aos doctor` + `/api/health`** (G10): Python/venv, 각 CLI 존재·버전·로그인 힌트,
      codexbar, `rg`, 포트, TCC 안내. UI 상단 상태 바: `claude ✓ grok ✓ agy ✗ hermes ✓`
- [ ] **플랫폼 독립화** (G7):
  - `is_browse_allowed`를 일반 폴더 탐색으로 확장, iCloud/CloudStorage는 macOS 선택 기능으로 분리
  - Linux 수동 실행 smoke test + systemd 유닛 템플릿 (launchd와 병행)
- [ ] **패키징** (G8): pyproject에 `[build-system]` + `[project.scripts]` `aos` 엔트리포인트
      → `pipx install agentic-os` 경로 검토 (PATH·워커 연동 검증 후)
- [ ] **UI i18n**: 영문 기본 + 한국어 (템플릿 `lang="ko"` 하드코딩 해소)

**성공 지표:** 문서만 보고 Time-to-first-job ≤ 15분 (Claude CLI 있는 Mac 기준).

### Phase 3 — 차별 기능 가시화 (Week 9–12)

기능은 있어도 UI에 안 보이면 없는 것과 같다.

- [ ] **라우팅 투명성**: auto 라우팅 이유 한 줄("남은 사용량 claude 62% > grok 31%")을
      잡 히스토리에 고정 기록 (현 실시간 힌트를 영속화)
- [ ] **사용량 대시보드 강화**: 리셋 카운트다운, 오늘 잡 수, 에이전트별 소진 추세
- [ ] **Provider 플러그인 계약 문서화**: `build_command` / `parse_output` /
      `detect_rate_limit` — `providers.py`의 기존 클래스 구조가 이미 이 형태이므로
      문서화 + 하드코딩된 `PROVIDERS` dict를 등록 기반으로 완화 → 외부 기여자가
      Codex/Aider/Cursor 등 추가 가능 (생태계 씨앗)
- [ ] **Council 모드 신규 구현** (G12): 동일 프롬프트를 N개 provider에 배분 → 제안
      수집 → 상호 비평 → 지정 provider가 종합. UI는 raw 텍스트 dump가 아닌
      **제안 카드 → 비평 → 최종 종합 탭** 레이아웃. 단일 워커 제약 하에서는 순차
      실행으로 시작, 병렬 실행(아래)과 시너지
- [ ] **병렬 실행** (G6, plan.md V3): `worker.py` 싱글톤 → **provider 단위** 동시성
      (같은 CLI 세션 충돌만 방지하면 서로 다른 CLI는 병렬 안전)

### Phase 4 — 멀티턴 채팅 (이후, 체감 최대·비용도 최대)

- **가짜 멀티턴 우선 (권장)**: 백엔드는 기존 노트 append(`memory.py` `append_note`,
  `## 프롬프트/결과 (N차)`) + 세션 resume 그대로, **프론트만 채팅 버블 뷰**로 렌더.
  구조 변경 없이 Claude 스타일 대화 체감을 얻는 가성비 최고 경로
- **진짜 대화 상태(메시지 테이블)**는 antigravity/grok의 resume 동작 검증
  (task.md 미결 항목) 이후 재평가 — 현재 agy/grok은 `-c`(최신 이어가기)만 지원해
  완전한 멀티턴 정합성이 깨질 수 있음

---

## 4. 하지 말 것 (과투자 방지)

1. **Tauri/Electron/React 전면 재작성** — 빌드 없는 경량 스택이라는 강점 파괴,
   코드서명 비용·구독 CLI OAuth 연동 리스크. 채택이 검증된 뒤 장기 옵션으로만
2. **Docker를 주 설치 경로로** — 호스트 CLI의 GUI/OAuth 로그인·TCC·파일시스템과
   충돌. 로컬 프로세스 모델이 이 제품의 정답
3. **완전한 멀티턴 올인** — CLI별 `-p`/resume 동작 불일치가 해소되기 전에는 가짜
   멀티턴으로 체감만 먼저
4. **Claude 스킨 복제** — "미완성 Claude"로 오해되고 오케스트레이터 모트가 사라짐
5. **초기 코드서명·원클릭 인스톨러 필수화** — OSS 초기에는 bootstrap + 문서가 우선

---

## 5. 성공 지표

| 지표 | 목표 |
|---|---|
| Time-to-first-job (문서만 보고, Claude CLI 있는 Mac) | ≤ 15분 |
| README 스크롤 없이 가치 이해 | 지인 3명 테스트 통과 |
| CI | 메인 브랜치 상시 green |
| 외부 이슈/스타 등 피드백 | Phase 0 완료 후 4주 내 ≥ 1건 |
| "투박하다" 피드백 중 렌더·흐름 비율 | 마크다운 + 인라인 스트림 후 감소 |

---

## 6. 실행 순서 요약 (90일)

```text
Week 1–2   P0: CONTRIBUTING 교체, CI, README 영문+스크린샷, GitHub 메타
           + P1 선행: 미설치 CLI 감지 칩 (낮은 리스크·즉시 효과)
Week 3–5   P1: 마크다운 렌더+복사, ⌘Enter, 다크모드 토글, 상태 카드, 에러 배너
Week 6–8   P2: bootstrap.sh, aos doctor, Linux/systemd, 패키징, i18n
Week 9–12  P3: 라우팅 이유 기록, 사용량 강화, provider 플러그인 문서,
           Council 모드, provider 단위 병렬
이후       P4: 노트 스레드 → 채팅 버블 뷰 (가짜 멀티턴)
```

구현 착수 시 첫 커밋 추천: **P0의 CONTRIBUTING 교체 + CI 추가** (반나절, 리스크 0)
→ 다음으로 **마크다운 렌더** (투박함 체감 1순위).

---

## 7. 핵심 설계 원칙 (기존 유지)

- 유료 구독 CLI 헤드리스 모드만 사용 (API 키 미사용 → 추가 과금 없음)
- 워커가 API 키 환경변수를 제거하고 CLI를 spawn (구독 과금 보장)
- 빌드 도구 없는 프론트엔드 (Jinja2 + HTMX + vanilla JS)
- 앱은 `127.0.0.1` 바인딩 유지, 외부 접속은 Tailscale serve가 프록시
- 파괴적/외부 노출 동작은 명시적 확인 후 실행
