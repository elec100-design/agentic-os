# Agentic OS

Mac Mini에서 Claude, Gemini, SuperGrok, Hermes 유료 구독 CLI를 하나의 로컬 웹 대시보드로 통합하는 레이어입니다. Claude Desktop 컨셉의 UI에, 각 에이전트의 **실제 남은 사용량**(CodexBar 연동)에 따라 작업을 자동 배분합니다. 결과는 Obsidian(LLM WIKI 볼트)에 자동 저장되고, rate limit에 걸린 작업은 제한이 풀리면 중단 지점부터 자동으로 재개합니다.

**접속 주소**
- 로컬: [http://localhost:8899](http://localhost:8899)
- 외부(tailnet): `https://macmini.tail22aa0a.ts.net` — Tailscale serve, 같은 tailnet 기기만

## 주요 기능

- **통합 디스패치** — 프롬프트 하나로 4개 AI CLI를 선택하거나 자동 라우팅
- **실측 사용량 기반 자동 모드** — CodexBar로 읽은 실제 잔여 사용량이 가장 많은 에이전트로 복잡 작업을 배분, 단순 작업은 로컬 Hermes로. 실시간 추천 힌트 제공
- **모델 선택** — 에이전트별 모델을 팝업에서 선택(예: Claude Fable/Opus/Sonnet/Haiku)
- **작업 큐** — SQLite 기반 순차 처리, 실시간 출력(SSE) 스트리밍, 취소·삭제
- **자동 재개** — rate limit 감지 후 `resume_at` 시각까지 대기, CLI 세션으로 이어서 실행
- **메모리 ↔ 작업큐 연동** — 노트에 hover하면 고정/이름변경/그룹/보관/삭제 메뉴, 노트 클릭 시 해당 세션 이어가기. 한쪽을 지우면 반대쪽도 함께 정리
- **작업 위치 연동** — 로컬 폴더나 GitHub 리포를 등록해 그 디렉터리에서 작업 실행
- **파일 첨부** — 드래그앤드롭으로 사진·파일을 프롬프트에 첨부
- **사용량 패널** — 에이전트별 실제 사용률(%)과 리셋까지 남은 시간 표시
- **외부 접속** — Tailscale serve로 tailnet 내 HTTPS 접근 (아이폰·맥북)
- **launchd 자동 시작** — 로그인 시 백그라운드 기동, 비정상 종료 시 재시작

## 설계 원칙

| 항목 | 내용 |
|------|------|
| 호출 방식 | 유료 구독 CLI 헤드리스 모드만 사용 (`claude -p`, `gemini -p`, `grok -p`, `hermes -z`) |
| API 키 | 사용하지 않음 — 추가 과금 없음 |
| 동시 실행 | 1개 (메모리·CLI 세션 충돌 방지) |
| 프론트엔드 | Jinja2 + HTMX (빌드 도구 없음, CDN 의존 없음) |
| 데이터 | SQLite WAL 모드 (`data/aos.db`) |

## 사전 요구사항

- Python 3.11+
- macOS (launchd 서비스)
- 아래 CLI가 PATH에 설치·인증되어 있어야 합니다:

| 서비스 | CLI | 용도 |
|--------|-----|------|
| Claude | `claude` | 코딩, 리팩토링 (기본값) |
| Gemini | `gemini` | 대용량 문서, 멀티모달 |
| SuperGrok | `grok` | 검색, 최신 정보 |
| Hermes | `hermes` | 로컬·개인 데이터 작업 |

- Obsidian 볼트 검색용 [ripgrep](https://github.com/BurntSushi/ripgrep) (`rg`)

- 사용량 실측용 [CodexBar](https://github.com/steipete/CodexBar) (`codexbar` CLI) — 선택. 없으면 사용량은 "정보 없음"으로 표시되고 자동 라우팅은 우선순위로 폴백
- 외부 접속용 [Tailscale](https://tailscale.com) — 선택

## 설치

```bash
cd /Users/macmini/Documents/agentic-os
uv pip install -r requirements.txt --python .venv/bin/python3
```

> venv는 `uv`로 생성합니다 (`uv venv .venv`). 시스템 `python3 -m venv`는 샌드박스에서 ensurepip가 실패할 수 있습니다.

### launchd 서비스 (권장)

로그인 시 자동으로 시작하려면:

```bash
./install.sh
```

`install.sh`는 plist를 `~/Library/LaunchAgents/`에 복사하고 서비스를 로드합니다. 8899 포트를 점유한 잔여 프로세스도 정리합니다.

### 수동 실행 (개발용)

```bash
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8899
```

## 사용법

1. 브라우저에서 [http://localhost:8899](http://localhost:8899) 접속
2. 컴포저에 프롬프트 입력 후 에이전트(자동/Claude/Gemini/Grok/Hermes) 선택 — 에이전트를 누르면 모델도 고를 수 있습니다
3. 필요 시 **작업 위치**(로컬 폴더·GitHub 리포)와 **파일 첨부**(드래그앤드롭), **메모리 첨부**를 지정
4. 자동 모드면 컴포저 아래에 어느 에이전트로 갈지 실시간 추천이 표시됩니다
5. **작업 큐**에서 상태 확인, 클릭 시 실시간 출력 스트리밍
6. 완료된 작업은 Obsidian `Agentic OS/` 폴더에 노트로 저장되고 사이드바 메모리와 연동됩니다

### 자동 라우팅 규칙

- **단순 작업**(짧고 키워드 없음) → 로컬 **Hermes** (클라우드 사용량 절약)
- **복잡 작업** → 소진되지 않은 클라우드 에이전트 중 **실측 잔여 사용량이 가장 많은 곳**
- 모두 소진 → Hermes 폴백
- 사용량은 CodexBar에서 읽으며, 알 수 없는 에이전트는 우선순위(claude > gemini > grok)로 처리

컴포저에 지금 이 프롬프트가 어디로 갈지 실시간 추천 힌트가 표시됩니다.

### 외부 접속 (Tailscale)

앱은 `127.0.0.1`에만 바인딩된 채로 두고, Tailscale serve가 tailnet 안에서 HTTPS로 프록시합니다:

```bash
tailscale serve --bg 8899        # → https://<host>.<tailnet>.ts.net
tailscale serve --https=443 off  # 해제
```

접속할 아이폰·맥북도 같은 tailnet에 로그인되어 있어야 합니다. 로컬 와이파이엔 노출되지 않습니다.

## 아키텍처

단일 Python(FastAPI) 프로세스가 웹 대시보드, 백그라운드 큐 워커, 사용량 추적을 모두 담당합니다.

```
agentic-os/
├── app/
│   ├── main.py        # FastAPI: 대시보드 + API + SSE + 워크스페이스/노트 엔드포인트
│   ├── worker.py      # 백그라운드 큐 워커 (선택한 cwd에서 CLI 실행)
│   ├── providers.py   # 4개 CLI 어댑터 + 모델 플래그 + 사용량 기반 자동 라우팅
│   ├── codexbar.py    # CodexBar 실측 사용량 조회 + 캐시
│   ├── workspace.py   # 작업 위치(로컬 폴더 / GitHub 리포) 관리
│   ├── memory.py      # Obsidian 볼트 읽기/쓰기 + 노트 상태(고정/그룹/보관)
│   ├── db.py          # SQLite 접근 계층 + 마이그레이션
│   └── config.py      # 설정값 (모델 목록, 사용량 새로고침 주기 등)
├── templates/         # Jinja2 + HTMX 화면 (사이드바, 컴포저, 노트, 작업)
├── static/            # style.css, app.js, htmx (vendored)
├── data/              # SQLite, 사용량 캐시, 노트 상태, 업로드, 워크스페이스 (git 제외)
├── tests/             # 유닛 테스트 (108개)
├── launchd/           # launchd plist
└── docs/              # 설계 문서
```

### 작업 상태 머신

```
queued → running → done | failed | rate_limited → queued (재개)
```

- rate limit 감지 시 `resume_at` 저장 후 대기, 제한 해제 후 CLI 세션으로 재개
- 최대 시도 10회, 기본 타임아웃 30분, 기본 재개 지연 60분
- 앱 재시작 시 `running` 상태 작업은 `queued`로 복구

## 설정

`app/config.py`에서 변경할 수 있습니다:

| 항목 | 기본값 | 설명 |
|------|--------|------|
| `PORT` | 8899 | 웹 서버 포트 |
| `JOB_TIMEOUT_SEC` | 1800 | 작업 타임아웃 (초) |
| `DEFAULT_RESUME_DELAY_MIN` | 60 | rate limit 시 기본 재개 지연 (분) |
| `MAX_ATTEMPTS` | 10 | 최대 재시도 횟수 |
| `MEMORY_DIR` | `…/LLM WIKI/Blogging/Agentic OS/` | Obsidian 노트 저장 경로 |

## 테스트

```bash
.venv/bin/pytest -v
```

## 문서

- [개발 계획 (plan.md)](plan.md)
- [작업 내역 (task.md)](task.md)
- [V1 설계 명세](docs/superpowers/specs/2026-07-05-agentic-os-v1-design.md)
- [V1 구현 계획](docs/superpowers/plans/2026-07-05-agentic-os-v1.md)

## 다음 계획

Gemini 사용량 실측(CodexBar OAuth), 세션 재개 실측 보정, 멀티턴 채팅 UI, 병렬 작업 실행, 벡터/임베딩 검색, 토큰·비용 추적. 자세한 로드맵은 [plan.md](plan.md) 참고.