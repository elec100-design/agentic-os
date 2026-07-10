# Agentic OS — 작업 내역

완료/진행/예정 작업 추적. 로드맵은 [plan.md](plan.md) 참고.

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
- [x] Tailscale serve로 tailnet 내 HTTPS 노출 (`https://macmini.tail22aa0a.ts.net`)
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

- [ ] Antigravity 사용량 실측 — CodexBar 미지원, 별도 연동 필요 (현재 "정보 없음")
- [ ] antigravity/grok 세션 재개(`--resume latest`, `-c`) 및 모델 id 실측 검증
- [ ] 멀티턴 채팅 UI
- [ ] 병렬 작업 실행 (현재 동시 1개)
- [ ] 벡터/임베딩 기반 메모리 검색
- [ ] 토큰·비용 추적
