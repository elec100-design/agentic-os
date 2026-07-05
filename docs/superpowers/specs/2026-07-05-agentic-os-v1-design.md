# Agentic OS V1 설계 (2026-07-05)

## 목적

Mac Mini M4(16GB)에서 유료 구독 중인 Claude, Gemini, SuperGrok과 Hermes agent를
하나의 로컬 웹 대시보드에서 호출·실행하고, 결과를 Obsidian(LLM WIKI 볼트)에
자동 저장하며, 사용 제한(rate limit)에 걸린 작업은 제한이 풀리는 즉시 중단
지점부터 자동으로 이어서 완료하는 통합 레이어를 만든다.

핵심 제약: 각 서비스는 유료 구독 CLI의 헤드리스 모드로 호출한다 — API 키
추가 과금 없음. OpenClaw는 사용하지 않는다.

## 아키텍처

단일 Python(FastAPI) 프로세스 하나가 웹 대시보드, 백그라운드 큐 워커,
사용량 추적을 모두 담당한다. launchd로 로그인 시 자동 시작하고 비정상 종료
시 재시작한다. 접속 주소는 `http://localhost:8899` (localhost 바인딩만).

```
Agentic OS/
├── app/
│   ├── main.py        # FastAPI: 대시보드 페이지 + API + SSE 스트림
│   ├── worker.py      # 백그라운드 큐 워커 (asyncio 태스크)
│   ├── providers.py   # 4개 CLI 어댑터
│   ├── memory.py      # Obsidian 볼트 읽기/쓰기
│   └── db.py          # SQLite 접근 계층
├── templates/         # Jinja2 + HTMX 화면
├── static/            # CSS 등
├── data/aos.db        # SQLite (jobs, usage_log)
├── tests/             # 유닛 테스트
└── launchd/com.elec100.agentic-os.plist
```

프론트엔드는 Jinja2 서버 렌더링 + HTMX. 실행 중 작업의 출력은 SSE로
스트리밍한다. 빌드 도구 없음.

## 컴포넌트

### providers.py — CLI 어댑터

프로바이더마다 하나의 어댑터 클래스. 공통 인터페이스:

- `build_command(prompt, session_id=None) -> list[str]` — 새 실행 또는 재개 명령 조립
- `parse_output(stdout, stderr, exit_code) -> Result` — 결과 텍스트, 세션 ID 추출
- `detect_rate_limit(output) -> ResumeAt | None` — 제한 감지 + 리셋 시각 파싱

| 서비스 | 새 실행 | 재개 |
|---|---|---|
| Claude | `claude -p <prompt> --output-format json` | `claude -p --resume <session_id> <continue-prompt>` |
| Gemini | `gemini -p <prompt>` | `gemini -p --resume latest <continue-prompt>` |
| SuperGrok | `grok -p <prompt>` (single-turn, stdout 출력 후 종료) | `grok -c -p <continue-prompt>` (최근 세션 이어서) |
| Hermes | `hermes -z <prompt>` | `hermes --resume <session> -z <continue-prompt>` |

제한 감지 패턴(구현 시 실제 출력으로 보정):

- Claude: "usage limit reached", "resets at HH:MM" 류 메시지에서 리셋 시각 파싱
- Gemini: 429 / quota exceeded 메시지
- Grok: rate limit 메시지
- Hermes: 로컬(Ollama) 사용 시 제한 없음; 클라우드 프로바이더 오류는 일반 실패로 처리

리셋 시각을 파싱하지 못하면 기본 60분 후로 설정한다(설정값으로 조정 가능).

"자동" 라우팅 규칙(단순 키워드 기반, V1에서는 최소한으로):
코딩/리팩토링 → Claude, 검색/최신 정보 → Grok, 대용량 문서/멀티모달 → Gemini,
로컬·개인 데이터 작업 → Hermes. 매칭 실패 시 기본값 Claude.

### db.py — SQLite 스키마

```sql
jobs(
  id INTEGER PRIMARY KEY,
  prompt TEXT NOT NULL,
  provider TEXT NOT NULL,          -- claude|gemini|grok|hermes|auto(해석 후 확정)
  status TEXT NOT NULL,            -- queued|running|rate_limited|done|failed
  session_id TEXT,                 -- 재개용 CLI 세션 ID
  output TEXT DEFAULT '',          -- 누적 stdout
  error TEXT,
  resume_at TEXT,                  -- ISO8601, rate_limited일 때만
  attempts INTEGER DEFAULT 0,
  created_at TEXT, started_at TEXT, finished_at TEXT
)

usage_log(
  id INTEGER PRIMARY KEY,
  provider TEXT, ts TEXT, duration_sec REAL,
  outcome TEXT,                    -- ok|failed|rate_limited
  job_id INTEGER
)
```

### worker.py — 큐 워커 + 자동 재개

상태 머신: `queued → running → done | failed | rate_limited(→ queued)`

- 단일 asyncio 태스크가 큐를 순차 처리한다(동시 실행 1개 — 16GB 메모리와
  CLI 세션 충돌을 고려한 의도적 제한).
- 서브프로세스 stdout을 라인 단위로 읽어 DB에 append → 대시보드 SSE로 중계.
- 서브프로세스 타임아웃 기본 30분(작업 생성 시 조정 가능). 타임아웃/비정상
  종료 시 출력 보존 후 `failed`.
- 제한 감지 시: `rate_limited` + `resume_at` + `session_id` 저장. 워커는
  1분 간격으로 `resume_at`이 지난 작업을 확인해 재개 명령으로 다시 실행한다.
  재개 프롬프트는 "이전 작업을 중단 지점부터 계속 진행하라" 고정 문구.
  재개 후 또 제한에 걸리면 같은 과정을 반복한다 — 완료될 때까지.
- `attempts`가 10회를 넘으면 `failed`로 전환한다(무한 루프 방지).
- 앱 시작 시 `running` 상태로 남아 있는 작업(비정상 종료 흔적)은 `queued`로
  복구한다. Mac 재시작 → launchd가 앱 재기동 → 큐 자동 재개.

### main.py — 대시보드

단일 페이지, 4개 패널:

1. **디스패치**: 프롬프트 입력 + 모델 선택(자동/Claude/Gemini/Grok/Hermes) +
   "관련 메모리 첨부" 체크박스 → 작업을 큐에 등록.
2. **작업 큐**: 작업 목록(상태, 프로바이더, 재개 예정 시각), 클릭 시 실시간
   출력(SSE). 작업 취소 버튼.
3. **사용량 패널**: 서비스별 최근 24시간 호출 수, 현재 상태
   (🟢 사용 가능 / 🔴 제한 중 — HH:MM 리셋 예정).
4. **메모리 패널**: 최근 저장 노트 목록 + 키워드 검색(ripgrep).

### memory.py — Obsidian 연동

- 완료된 작업의 결과를 LLM WIKI 볼트
  (`/Users/macmini/Library/Mobile Documents/com~apple~CloudDocs/LLM WIKI/Blogging`)
  하위 `Agentic OS/` 폴더에 마크다운 노트로 저장. frontmatter: date, provider,
  prompt 요약(첫 80자), tags.
- 파일명: `YYYY-MM-DD-<slug>.md`, 충돌 시 숫자 접미사.
- 검색: ripgrep으로 볼트 내 키워드 검색(제목+본문).
- "관련 메모리 첨부" 선택 시: 프롬프트 키워드로 검색된 상위 3개 노트를
  프롬프트 앞에 컨텍스트로 삽입한다.

## 에러 처리

- CLI 미설치/인증 만료: 실행 실패 출력을 그대로 `failed` 사유로 보존, 사용량
  패널에 표시.
- Obsidian 볼트 경로 접근 불가(iCloud 미동기화 등): 작업 자체는 성공 처리하고
  노트 저장 실패만 경고 로그 + 대시보드 배지로 표시.
- DB는 WAL 모드로 열어 워커/웹 동시 접근을 안전하게 한다.

## 테스트 전략

- 유닛: 제한 감지 파서(각 프로바이더별 실제 출력 샘플 고정 픽스처), 큐 상태
  머신 전이, resume_at 계산, 메모리 노트 파일명/frontmatter 생성.
- E2E(수동): 4개 CLI 각각 짧은 프롬프트 1회 실행 → 대시보드에서 출력 확인 →
  Obsidian 노트 생성 확인. 제한 재개는 감지 패턴에 가짜 출력을 주입해 검증.

## V1 범위에서 제외 (V2 이후)

Dreaming 자기개선 엔진, 벡터 DB/임베딩 검색, 외부 접속(Tailscale), 멀티턴
채팅 UI, 병렬 작업 실행, 토큰 단위 비용 추적.
