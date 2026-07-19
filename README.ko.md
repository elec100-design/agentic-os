# Agentic OS

> [English README](README.md)

구독형 AI CLI(Claude·Antigravity·SuperGrok·Hermes)를 하나의 로컬 웹 대시보드로 통합하는 레이어입니다. Claude Desktop 컨셉의 UI에서, 각 에이전트의 **실제 남은 사용량**(CodexBar 연동)에 따라 작업을 자동 배분합니다. 결과는 로컬 노트(또는 Obsidian 볼트)에 자동 저장되고, rate limit에 걸린 작업은 제한이 풀리면 중단 지점부터 자동으로 재개합니다.

API 키를 쓰지 않고 이미 구독 중인 CLI의 헤드리스 모드만 호출하므로 추가 과금이 없습니다.

![대시보드](docs/screenshots/dashboard.png)

## ⚠️ 보안 주의

이 앱은 본질적으로 **웹 UI에서 입력한 프롬프트를 로컬 CLI로 실행**시키는 도구입니다. 접근할 수 있는 사람은 당신의 구독 계정으로 명령을 돌리고, 허용된 폴더의 파일을 읽고 쓸 수 있습니다.

- 기본 바인딩은 `127.0.0.1`(로컬 전용)입니다. **그대로 두세요.**
- **공용 네트워크(0.0.0.0)에 노출하지 마세요.** 내장 인증이 없습니다.
- 외부 기기에서 써야 하면 [Tailscale](https://tailscale.com) 같은 사설 네트워크로만 노출하세요(아래 참고).

## 주요 기능

- **첫 실행 셋업 위저드** — `/setup`에서 보유한 CLI를 감지·선택하고, 각 CLI의 로그인 절차를 안내받습니다. 선택한 에이전트만 이후 UI·라우팅에 노출됩니다
- **통합 디스패치** — 프롬프트 하나로 여러 AI CLI를 선택하거나 자동 라우팅
- **실측 사용량 기반 자동 모드** — CodexBar로 읽은 실제 잔여 사용량이 가장 많은 에이전트로 복잡 작업을 배분, 단순 작업은 로컬 Hermes로. 실시간 추천 힌트 제공
- **에이전트·모델 선택** — CLI에서 수집한 **최신 모델 목록**을 별도 칩에서 선택 (하드코딩 없음, 주기 자동 갱신)
- **협의(Council) 모드** — 어려운 문제를 여러 에이전트가 병렬로 제안 → 서로의 답을 익명 라벨로 비평·개선 → 잔여 사용량이 가장 많은 에이전트가 최종 종합 (Hermes MoA·OpenRouter Fusion 방식). 에이전트 칩에서 "협의" 선택. 사용량 소진·실패한 에이전트는 자동 제외, `AOS_COUNCIL_*` 환경변수로 참여자·종합자·라운드 조정
- **작업 큐** — SQLite 기반 순차 처리, 실시간 출력(SSE) 스트리밍, 취소·삭제
- **자동 재개** — rate limit 감지 후 `resume_at` 시각까지 대기, CLI 세션으로 이어서 실행
- **노트 ↔ 작업큐 연동** — 노트에 hover하면 고정/이름변경/그룹/보관/삭제, 노트에서 세션 이어가기(같은 작업 위치·같은 노트로 이어짐). 이어갈 때 에이전트·모델을 바꾸거나 파일을 첨부할 수 있습니다. 한쪽을 지우면 반대쪽도 함께 정리
- **스레드 단위 노트 + 자동 그룹핑** — 세션을 이어가도 노트가 파편화되지 않고 원본 노트에 이어 쓰임. 노트는 실행된 작업 위치(워크스페이스) 이름으로 자동 그룹핑되고, 사이드바에서 접이식 폴더로 표시
- **작업 위치 연동** — 팝업으로 맥 폴더를 탐색해 고르거나(Finder식), `gh` 로그인 계정의 GitHub 리포·브랜치를 골라 클론해 그 디렉터리에서 작업 실행
- **파일 첨부** — 드래그앤드롭으로 사진·파일을 프롬프트에 첨부(크기 한도 설정 가능)
- **사용량 패널** — 에이전트별 실제 사용률(%)과 리셋까지 남은 시간 표시
- **launchd 자동 시작(선택)** — 로그인 시 백그라운드 기동, 비정상 종료 시 재시작

## 설계 원칙

| 항목 | 내용 |
|------|------|
| 호출 방식 | 구독 CLI 헤드리스 모드만 사용 (`claude -p`, `agy -p`, `grok -p`, `hermes -z`) |
| API 키 | 사용하지 않음 — 추가 과금 없음 |
| 동시 실행 | 1개 (메모리·CLI 세션 충돌 방지) |
| 프론트엔드 | Jinja2 + HTMX (빌드 도구 없음, CDN 의존 없음) |
| 데이터 | SQLite WAL 모드 (`data/aos.db`) |

## 사전 요구사항

- macOS (launchd 서비스·iCloud 폴더 탐색은 macOS 전용. 수동 실행은 다른 OS에서도 가능하나 미검증)
- Python 3.11+, [uv](https://github.com/astral-sh/uv) 권장
- 쓰려는 CLI 중 **최소 1개**(예: Claude Code 하나만)만 있어도 바로 시작할 수 있습니다. 나머지는 나중에 `/setup`에서 추가하면 됩니다:

| 서비스 | CLI | 용도 |
|--------|-----|------|
| Claude | `claude` | 코딩, 리팩토링 |
| Antigravity | `agy` | 대용량 문서, 멀티모달 (구글 OAuth 로그인) |
| SuperGrok | `grok` | 검색, 최신 정보 |
| Hermes | `hermes` | 로컬·개인 데이터 작업 |

- 노트 검색용 [ripgrep](https://github.com/BurntSushi/ripgrep) (`rg`) — 선택
- 사용량 실측용 [CodexBar](https://github.com/steipete/CodexBar) (`codexbar` CLI) — 선택. 없으면 사용량은 "정보 없음"으로 표시되고 자동 라우팅은 우선순위로 폴백
- 외부 접속용 [Tailscale](https://tailscale.com) — 선택

## 설치

### 빠른 시작 (한 명령)

```bash
git clone https://github.com/elec100-design/agentic-os.git
cd agentic-os
./bootstrap.sh
```

`bootstrap.sh`는 가상환경 생성(`uv` 있으면 uv, 없으면 `python -m venv`), 의존성 설치, `aos.env` 시드, 서버 실행 + 브라우저 열기까지 한 번에 합니다. macOS·Linux 모두 동작하며, 설치만 하려면 `./bootstrap.sh --no-run`.

### 수동 설치

```bash
uv venv .venv
uv pip install -r requirements.txt --python .venv/bin/python3
.venv/bin/python3 -m app          # 또는: .venv/bin/uvicorn app.main:app --port 8899
```

> 시스템 `python3 -m venv`는 일부 환경에서 ensurepip가 실패할 수 있어 `uv`를 권장합니다.

브라우저에서 [http://localhost:8899](http://localhost:8899) 접속하면 **첫 실행 셋업 위저드**로 안내됩니다.

### 진단 (doctor)

```bash
.venv/bin/python3 -m app doctor    # 또는 `pip install -e .` 후 `aos doctor`
```

Python·플랫폼·포트·데이터 디렉터리 쓰기 가능 여부, 감지된 에이전트 CLI·보조 도구를 출력합니다 — "왜 안 되지?"를 가장 빠르게 확인하는 방법입니다. 같은 정보를 `GET /api/health`로도 제공합니다.

### 로그인 시 자동 시작 (선택)

**macOS (launchd):**

```bash
./install.sh
```

[템플릿](launchd/agentic-os.plist.template)을 현재 사용자 환경으로 치환해 `~/Library/LaunchAgents/`에 plist를 생성·로드합니다.

**Linux (systemd 사용자 서비스):** 유닛 템플릿과 설정 절차는 [`deploy/agentic-os.service`](deploy/agentic-os.service) 참고.

두 경우 모두 볼트·origin 등 앱 설정은 서비스 파일이 아니라 `aos.env`로 관리되므로 **재설치해도 유지됩니다.**

## 첫 실행 셋업

처음 접속하면 `/setup`으로 안내됩니다:

1. **환영** — 핵심 기능 소개
2. **에이전트 선택** — 설치된 CLI가 자동 감지되어 선택되고, 미설치 CLI는 안내와 함께 선택 가능
3. **로그인 안내** — 선택한 각 CLI의 로그인 절차(터미널에서 직접 진행) 안내. 앱이 대신 로그인할 수는 없습니다
4. **완료** — 선택한 에이전트만 이후 사용량 사이드바·자동 라우팅·협의 모드에 노출됩니다

나중에 사이드바의 **⚙︎ 에이전트 설정**에서 언제든 다시 방문할 수 있습니다.

## 설정 (`aos.env`)

기본값으로 바로 동작합니다. 개인 설정(볼트 경로·tailnet origin 등)은 저장소 루트의 **`aos.env`** 파일에 두세요 — `config.py`가 기동 시 읽으며, **수동실행·launchd·재설치 어느 방식이든 동일하게 적용**됩니다.

```bash
cp aos.env.example aos.env   # 편집해서 값 채우기
```

`aos.env`는 git에 커밋되지 않습니다(`.gitignore`). 실제 환경변수(`export`)를 쓰면 그쪽이 우선합니다. 지원 키:

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `AOS_VAULT_PATH` | (없음) | Obsidian 볼트(또는 임의 폴더) 루트. 지정 시 노트는 볼트의 `Agentic OS/` 하위에 저장되고, 컨텍스트 노트는 볼트 전체에서 읽힘 |
| `AOS_NOTES_DIR` | (없음) | 노트 저장 폴더를 직접 지정(볼트 규칙 무시) |
| `AOS_PORT` | `8899` | 웹 서버 포트 |
| `AOS_HOST` | `127.0.0.1` | 바인딩 호스트. **로컬 전용을 유지하는 것을 강력 권장** |
| `AOS_MAX_UPLOAD_MB` | `25` | 파일 첨부 1건 최대 크기(MB) |
| `AOS_EXTRA_ORIGINS` | (없음) | 신뢰하는 프록시 호스트(콤마 구분). 예: `myhost.tailnet.ts.net` |
| `AOS_SERVICE_LABEL` | `com.agentic-os.dashboard` | launchd 서비스 레이블 |
| `AOS_DISABLE_WORKER` | (없음) | `1`이면 백그라운드 워커 비활성(테스트·개발용) |
| `AOS_COUNCIL_MEMBERS` | 4개 전체 | 협의 모드 참여 에이전트(콤마 구분) |
| `AOS_COUNCIL_AGGREGATOR` | (자동) | 협의 모드 종합자 고정 |
| `AOS_COUNCIL_ROUNDS` | `2` | 1=제안만, 2=제안+상호비평 |

`AOS_VAULT_PATH`를 지정하지 않으면 노트는 저장소 안 `data/notes/`에 저장됩니다. Obsidian이 없어도 그대로 동작합니다.

앱 상수(타임아웃·재시도·새로고침 주기 등)는 [`app/config.py`](app/config.py)에서 조정할 수 있습니다.

## 사용법

1. 브라우저에서 대시보드 접속 (첫 방문은 셋업 위저드를 먼저 거칩니다)
2. 컴포저에 프롬프트 입력 후 **에이전트 칩**으로 에이전트를, 그 옆 **모델 칩**으로 모델을 선택(자동 모드면 생략)
3. 필요 시 **작업 위치**(로컬 폴더·GitHub 리포)를 고르고, 컴포저 **＋ 메뉴**에서 **파일 첨부**(드래그앤드롭도 가능)·**메모리 첨부**·**타임아웃**을 지정
4. 자동 모드면 컴포저 아래에 어느 에이전트로 갈지 실시간 추천이 표시됩니다
5. **작업 큐**에서 상태 확인, 클릭 시 실시간 출력 스트리밍
6. 완료된 작업은 노트로 저장되고 사이드바 메모리와 연동됩니다

### 자동 라우팅 규칙

- **단순 작업**(짧고 키워드 없음) → 로컬 **Hermes** (클라우드 사용량 절약, 비활성 시 클라우드로 폴백)
- **복잡 작업** → 활성 에이전트 중 소진되지 않은 클라우드 에이전트 중 **실측 잔여 사용량이 가장 많은 곳**
- 모두 소진 → Hermes 폴백 (Hermes도 비활성이면 활성 에이전트 중 첫 번째로 시도)
- 사용량은 CodexBar에서 읽으며, 알 수 없는 에이전트는 우선순위(claude > antigravity > grok)로 처리
- 라우팅은 `/setup`에서 활성화한 에이전트로만 제한됩니다

### 모델 목록 (동적)

버전 ID를 코드에 박아 두지 않습니다. 기동 시·주기적으로 각 CLI에서 읽어 `data/models_cache.json`에 캐시합니다. CLI 조회 실패 시 [`config.FALLBACK_PROVIDER_MODELS`](app/config.py)(별칭·기본값)로 폴백합니다.

### 노트 정리 (스레드 · 자동 그룹)

- **스레드당 노트 1개** — 노트에서 "이 세션 이어서 진행"으로 만든 작업은 새 노트를 만들지 않고 원본 노트에 `## 프롬프트 (2차)` / `## 결과 (2차)` 섹션으로 이어 씁니다. `session_id`·`model`은 항상 최신 값으로 갱신됩니다.
- **세션 이어가기 옵션** — 이어갈 때 작업 위치는 유지되고, 원하면 에이전트·모델을 바꾸거나 파일을 첨부할 수 있습니다. 같은 에이전트면 진짜 세션 재개, 다른 에이전트면 노트를 컨텍스트로 붙여 같은 위치에서 이어갑니다.
- **워크스페이스 자동 그룹** — 노트 저장 시 실행된 작업 위치 이름이 자동으로 그룹이 됩니다. 사이드바에서 "그룹으로 이동"으로 수동 지정하면 그 이후로는 수동 값이 우선합니다.
- 사이드바의 각 그룹은 **접이식 폴더**로 표시됩니다(기본 접힘). 펼친 상태는 브라우저에 기억됩니다.

### 외부 접속 (Tailscale, 선택)

앱은 `127.0.0.1`에만 바인딩한 채 두고, Tailscale serve가 tailnet 안에서만 HTTPS로 프록시합니다:

```bash
tailscale serve --bg 8899        # → https://<host>.<tailnet>.ts.net
tailscale serve --https=443 off  # 해제
```

프록시 호스트명을 `aos.env`의 `AOS_EXTRA_ORIGINS`에 추가하세요(POST 요청의 origin 검증 통과용). 접속 기기도 같은 tailnet에 로그인되어 있어야 하며, 로컬 와이파이엔 노출되지 않습니다.

### 파일 접근 권한 (macOS TCC)

작업은 launchd로 도는 프로세스와 자식 CLI가 수행하므로, 파일 읽기/쓰기는 그 프로세스의 macOS 권한을 따릅니다. 원격에서 접근하면 권한이 없는 폴더는 macOS 승인창을 띄울 수 없어 조용히 실패합니다. 어떤 위치든 확실히 쓰려면 **시스템 설정 → 개인정보 보호 및 보안 → 전체 디스크 접근 권한**에 `.venv/bin/python3`의 실제 경로를 추가하세요.

## 아키텍처

단일 Python(FastAPI) 프로세스가 웹 대시보드, 백그라운드 큐 워커, 사용량 추적을 모두 담당합니다.

```
agentic-os/
├── app/
│   ├── main.py        # FastAPI: 대시보드 + API + SSE + 워크스페이스/노트/셋업 엔드포인트
│   ├── worker.py      # 백그라운드 큐 워커 (선택한 cwd에서 CLI 실행)
│   ├── providers.py   # CLI 어댑터 + 모델 플래그 + 사용량 기반 자동 라우팅
│   ├── council.py     # 협의(Council) 모드 — 다중 에이전트 제안·비평·종합
│   ├── settings.py    # 사용자 설정(활성 에이전트) — data/settings.json
│   ├── setup.py       # 첫 실행 셋업 — CLI·보조도구 설치 감지
│   ├── health.py      # /api/health·`aos doctor` 진단 정보
│   ├── __main__.py    # `python -m app`/`aos` 진입점 (서버 실행 + doctor)
│   ├── models.py      # CLI 모델 목록 동적 수집 + 캐시
│   ├── codexbar.py    # CodexBar 실측 사용량 조회 + 캐시
│   ├── workspace.py   # 작업 위치(로컬 폴더 / GitHub 리포) 관리
│   ├── github_cli.py  # gh CLI로 리포·브랜치 조회
│   ├── memory.py      # 노트 읽기/쓰기 + 상태(고정/그룹/보관) + 스레드 append/자동 그룹핑
│   ├── db.py           # SQLite 접근 계층 + 마이그레이션
│   └── config.py      # 설정값 (환경변수, 폴백 모델, 새로고침 주기 등)
├── templates/         # Jinja2 + HTMX 화면 (사이드바, 컴포저, 셋업, 노트, 작업)
├── static/            # style.css, app.js, setup.js, htmx (vendored)
├── data/              # SQLite, 캐시, 노트(기본), 업로드, 워크스페이스, 셋업 설정 (git 제외)
├── tests/             # 유닛 테스트
├── launchd/           # macOS launchd plist 템플릿
├── deploy/            # Linux systemd 유닛 템플릿
├── bootstrap.sh       # 원스톱 설치+실행 (macOS/Linux)
└── docs/              # 로드맵·작업 내역·설계 문서
```

### 작업 상태 머신

```
queued → running → done | failed | rate_limited → queued (재개)
```

- rate limit 감지 시 `resume_at` 저장 후 대기, 제한 해제 후 CLI 세션으로 재개
- 최대 시도 10회, 기본 타임아웃 30분, 기본 재개 지연 60분
- 앱 재시작 시 `running` 상태 작업은 `queued`로 복구

## 테스트

```bash
.venv/bin/pytest -q
```

CI(`.github/workflows/ci.yml`)가 push·PR마다 Python 3.11/3.12에서 전체 테스트를 돌립니다.

## 라이선스

[MIT](LICENSE). 저작권 표기(holder)는 자유롭게 바꿔 쓰세요.

## 문서

- [개발 계획 (plan.md)](docs/plan.md)
- [작업 내역 (task.md)](docs/task.md)
- [기여 가이드 (CONTRIBUTING.md)](CONTRIBUTING.md)
- [V1 설계 명세](docs/2026-07-05-agentic-os-v1-design.md)
- [V1 구현 계획](docs/2026-07-05-agentic-os-v1.md)

## 알려진 한계 / 로드맵

- macOS 외 환경은 미검증(launchd·iCloud 탐색은 macOS 전용). 로드맵 참고.
- Antigravity 사용량 실측 연동, 멀티턴 채팅 UI, 병렬 실행, 토큰·비용 추적 등
  공개 배포 로드맵은 [plan.md](docs/plan.md)의 V3~V4 참고.
