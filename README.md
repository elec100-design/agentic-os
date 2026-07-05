# Agentic OS

Mac Mini에서 Claude, Gemini, SuperGrok, Hermes 유료 구독 CLI를 하나의 로컬 웹 대시보드로 통합하는 레이어입니다. 작업 결과는 Obsidian(LLM WIKI 볼트)에 자동 저장되고, rate limit에 걸린 작업은 제한이 풀리면 중단 지점부터 자동으로 재개합니다.

**접속 주소:** [http://localhost:8899](http://localhost:8899) (localhost 전용)

## 주요 기능

- **통합 디스패치** — 프롬프트 하나로 4개 AI CLI를 선택하거나 자동 라우팅
- **작업 큐** — SQLite 기반 순차 처리, 실시간 출력(SSE) 스트리밍, 작업 취소
- **자동 재개** — rate limit 감지 후 `resume_at` 시각까지 대기, CLI 세션으로 이어서 실행
- **Obsidian 연동** — 완료된 작업을 마크다운 노트로 저장, ripgrep 검색, 관련 메모리 컨텍스트 첨부
- **사용량 패널** — 서비스별 24시간 호출 수와 제한 상태 표시
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

## 설치

```bash
cd "/Users/macmini/Documents/Agentic OS"
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

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
2. **디스패치** 패널에 프롬프트 입력, 모델 선택(자동/Claude/Gemini/Grok/Hermes)
3. 필요 시 **관련 메모리 첨부** 체크 — 프롬프트 키워드로 검색된 상위 3개 노트가 컨텍스트로 삽입됩니다
4. **작업 큐**에서 상태 확인, 클릭 시 실시간 출력 스트리밍
5. 완료된 작업은 Obsidian `Agentic OS/` 폴더에 자동 저장

### 자동 라우팅 규칙

| 키워드 예시 | 라우팅 |
|-------------|--------|
| 검색, 최신, 뉴스, search | Grok |
| 문서, 요약, PDF, 번역 | Gemini |
| 로컬, 개인, local | Hermes |
| 그 외 | Claude |

## 아키텍처

단일 Python(FastAPI) 프로세스가 웹 대시보드, 백그라운드 큐 워커, 사용량 추적을 모두 담당합니다.

```
Agentic OS/
├── app/
│   ├── main.py        # FastAPI: 대시보드 + API + SSE
│   ├── worker.py      # 백그라운드 큐 워커
│   ├── providers.py   # 4개 CLI 어댑터 + 자동 라우팅
│   ├── memory.py      # Obsidian 볼트 읽기/쓰기
│   ├── db.py          # SQLite 접근 계층
│   └── config.py      # 설정값
├── templates/         # Jinja2 + HTMX 화면
├── static/            # CSS, htmx (vendored)
├── data/aos.db        # SQLite (jobs, usage_log)
├── tests/             # 유닛 테스트
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

- [V1 설계 명세](docs/superpowers/specs/2026-07-05-agentic-os-v1-design.md)
- [V1 구현 계획](docs/superpowers/plans/2026-07-05-agentic-os-v1.md)

## V2 이후 계획

Dreaming 자기개선 엔진, 벡터 DB/임베딩 검색, Tailscale 외부 접속, 멀티턴 채팅 UI, 병렬 작업 실행, 토큰 단위 비용 추적