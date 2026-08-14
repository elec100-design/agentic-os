# Grok Bot 기능 이식 검토 — Agentic OS 적용안

> **진행 상황 (2026-08-13)**: 제안 A(커스텀 에이전트)·B1(채널 @멘션·에이전트 간
> 대화)·B2(워크플로 동적 위임)·C(승인 게이트)를 구현했다 — `docs/plan.md` V4.7·V4.8.
> 남은 것은 D(루틴)와 E(에이전트별 기억). 아래 본문은 착수 시점의 검토 내용이며,
> 구현하며 달라진 점은 각 절 끝에 적어 둔다.

2026-08-13. Grok Bot(2026-08-11 베타)이 내세우는 기능을 이 리포의 실제 구조에
비추어 "이미 있는 것 / 얹을 수 있는 것 / 얹으면 안 되는 것"으로 나눈다.
핵심 요구는 **사용자가 에이전트를 만들고, 에이전트끼리 대화하고 일을 넘기는 것**.

## 요약

- 이 리포는 이미 Grok Bot 기능의 절반을 갖고 있다 — 비동기 큐 실행, 세션 재개,
  전용 워크스페이스, MCP 도구 연결, 결과 축적(노트). 새로 지을 것은 **에이전트
  정체성**, **에이전트 간 메시징**, **승인 게이트**, **루틴** 네 가지다.
- 참고 분석문의 "OAuth 못 쓰니 Console API 키로 가라"는 **이 리포에 해당하지
  않는다.** 우리는 Agent SDK를 쓰지 않고 구독 CLI를 헤드리스로 spawn 한다
  (`app/worker.py:_clean_env` 가 API 키 환경변수를 일부러 제거한다). 방향을
  바꿀 이유가 없고, 바꾸면 "추가 과금 없음"이라는 제품 전제가 깨진다.
- 대신 그 선택의 대가가 **승인 게이트**다. SDK의 `defer` 훅을 못 쓰므로 도구
  단위 가로채기는 CLI가 열어 준 만큼만 가능하다 → 벤더 중립 대안을 1순위로 둔다.

## 참고 분석문에서 정정할 전제

| 분석문 주장 | 이 리포의 실제 |
|---|---|
| OAuth 토큰 금지 → Console API 키 필요 | 무관. Agent SDK가 아니라 `claude -p` 등 CLI를 spawn 한다. 구독 CLI 헤드리스 실행은 정상 경로 |
| Phase 1: 큐 + SQLite + SSE 백엔드 신규 구축 | **이미 있음** — `app/db.py`(jobs/messages/execution_steps), `app/worker.py`, `app/stream_hub.py`, `/jobs/{id}/stream` |
| Phase 3: PWA 프론트 신규 | 이미 모바일 대응 웹 UI가 있다. PWA 껍데기(manifest/SW)만 남았고 우선순위 낮음 |
| Phase 4: Tailscale 배선 | **이미 문서화·운영 중** (`README.md:317`, 127.0.0.1 바인딩 원칙) |
| Phase 6: launchd KeepAlive | 이미 있음 (`launchd/agentic-os.plist.template`) |
| 이벤트 로그 append-only + `from=<seq>` 커서 재생 | `execution_steps(seq)` 가 사실상 그 로그다. 스트림은 재접속마다 DB를 처음부터 다시 읽어 재생하므로 멀티디바이스 이어보기는 이미 성립. 커서는 대역폭 최적화일 뿐 |
| 프로젝트명 "Hermes" | **이름 충돌** — `hermes` 는 이미 로컬 CLI provider 이름이다(`app/providers.py:427`) |

즉 분석문의 Phase 0·1·3·4·6은 대부분 이미 끝났거나 불필요하고, **Phase 2(승인)와
그 문서가 다루지 않은 "에이전트 정체성·에이전트 간 협업"이 실제 남은 일**이다.

## Grok Bot 5기능 × 현황

| # | Grok Bot 기능 | 현황 | 남은 일 |
|---|---|---|---|
| 1 | 비동기 백그라운드 실행 | **있음** — SQLite 큐 + 워커, 클라이언트가 끊겨도 계속, 재시작 복구(`db.recover_running`) | 없음 |
| 2 | 승인 게이트 | **거의 없음** — 계획 승인(`plan_ready`→`approve`)뿐. claude 는 `acceptEdits` + Bash 사전 허용으로 무인 실행(`app/providers.py:76`), 사후 되돌리기만 있음(`app/gitcheckpoint.py`) | 제안 C |
| 3 | 봇마다 전용 작업 환경 | **있음** — 워크스페이스 등록·workdir 격리·git 체크포인트 | 에이전트에 워크스페이스를 고정 귀속(제안 A) |
| 4 | 도구 로그인 | **있음** — 워크스페이스별 MCP(`app/mcp_servers.py`, claude 전용) | 에이전트별 MCP 프로필로 좁히기(제안 A) |
| 5 | 루틴 저장·기억 | 기억은 있음(노트·`app/memory.py`), **루틴은 없음** | 제안 D·E |
| ★ | 여러 봇의 협업 | **부분적** — Council(병렬 제안→상호 비평→종합)과 비전 보드 DAG(계획자→작업자 단방향 인계). 둘 다 프롬프트 텍스트로 컨텍스트를 넘기는 stateless 구조 | 제안 A·B |

**이 리포가 Grok Bot보다 이미 앞선 지점**: 여러 벤더 CLI를 잔여 쿼터로 라우팅한다.
Grok Bot의 봇은 전부 같은 모델이다. 커스텀 에이전트를 얹을 때 이 강점을 죽이지
않는 것이 설계의 제약이다(아래 A-3).

---

## 제안 A — 커스텀 에이전트 (모든 것의 전제)

지금 "에이전트"는 곧 벤더 CLI다(`PROVIDERS` 7종). 사용자가 만든 역할이라는 개념이
없어서, 에이전트끼리 대화시키려 해도 부를 이름이 없다. 여기부터 시작해야 한다.

```sql
CREATE TABLE agents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  slug TEXT NOT NULL UNIQUE,      -- @멘션 이름 (a-z0-9-)
  name TEXT NOT NULL,
  persona TEXT NOT NULL DEFAULT '',   -- 역할·문체·금지사항
  provider TEXT NOT NULL,             -- claude|codex|…|auto
  model TEXT,
  workdir TEXT,                       -- 이 에이전트의 "자기 컴퓨터"
  allowed_tools TEXT,                 -- JSON, claude 한정
  mcp_profile TEXT,                   -- mcp_servers.json 프로필 키
  approval_policy TEXT NOT NULL DEFAULT 'default',
  memory_note TEXT,                   -- 이 에이전트 전용 노트 경로
  status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL
);
ALTER TABLE jobs ADD COLUMN agent_id INTEGER;   -- 기존 잡은 NULL
```

**A-1. 페르소나 주입 지점은 이미 있다.** `app/worker.py` 의 `run_job` 이
`instructions.build_context()` 결과를 프롬프트 앞에 붙이는 자리가 그대로 쓰인다.
`agent.persona` 를 그 프리픽스 앞단에 한 덩이 더 얹으면 끝이고, 벤더 중립이다
(claude·codex·gemini·grok 전부 동일하게 먹는다).

**A-2. claude 네이티브 경로는 나중에.** `claude --agents '<json>'` 으로 커스텀
에이전트를 CLI에 직접 넘길 수 있다(이 빌드의 `--help` 확인). 다만 claude 전용이라
라우팅이 다른 CLI로 튀는 순간 페르소나가 사라진다. **기본은 프롬프트 프리픽스,
`--agents` 는 claude 최적화로 후순위.**

**A-3. `provider = "auto"` 를 반드시 허용할 것.** 페르소나는 고정하되 실행 CLI는
잔여 쿼터 라우팅(`route_auto`)에 맡기는 모드다. "이 리포만의" 기능이고, 특정 CLI가
쿼터를 소진해도 에이전트가 계속 살아 있게 하는 실용적 장치이기도 하다.

**A-4. 셋업 연동.** `settings.enabled_providers()` 에서 빠진 CLI를 provider 로 쓰는
에이전트는 UI에서 비활성 표시 + 실행 시 400 (채널 메시지 경로가 이미 이 검사를 한다).

---

## 제안 B — 에이전트 간 대화와 작업 인계 (핵심 요구)

`channels`/`messages` 스키마에 이미 `role`, `author`, `parent_id`, `root_id`,
`reply_count` 가 있다. **멀티 에이전트 대화의 뼈대가 이미 깔려 있고, 지금은 사람만
말을 걸 수 있을 뿐이다.** 두 층으로 나눠 붙인다.

### B-1. 대화 층 — @멘션

```sql
CREATE TABLE channel_members (
  channel_id INTEGER NOT NULL REFERENCES channels(id),
  agent_id   INTEGER NOT NULL REFERENCES agents(id),
  PRIMARY KEY (channel_id, agent_id)
);
ALTER TABLE messages ADD COLUMN author_agent_id INTEGER;
ALTER TABLE messages ADD COLUMN hop_depth INTEGER NOT NULL DEFAULT 0;
```

흐름:

1. 채널 메시지 생성 경로(`app/main.py:632`)에서 본문의 `@slug` 를 파싱해 지목된
   에이전트 앞으로 잡을 만든다. 멘션이 없으면 지금처럼 채널 기본 에이전트.
2. **잡이 끝날 때 응답 본문도 같은 파서를 태운다.** 응답에 `@slug` 가 있으면
   그 에이전트에게 새 잡을 만들고 `hop_depth = 부모 + 1`. → 에이전트끼리 대화가
   성립한다. 컨텍스트는 지금과 같이 프롬프트 텍스트로 넘긴다(쓰레드 히스토리
   + 직전 발화). 세션 공유는 하지 않는다 — 벤더가 섞이면 성립하지 않고,
   `council.py`·`orchestrator.py` 가 이미 이 원칙으로 서 있다.

**안전장치가 이 기능의 본체다.** 무한 루프는 곧 구독 쿼터 소각이다:

| 장치 | 기본값 | 동작 |
|---|---|---|
| `AOS_AGENT_HOP_MAX` | 3 | 사용자 발화 0부터 세어 초과하면 중단하고 "사용자 확인 필요"로 표시 |
| `AOS_AGENT_CHAIN_MAX` | 8 | 한 쓰레드에서 에이전트 발화 총량 상한 |
| 자기 멘션 무시 | — | A→A 금지 |
| 왕복 상한 | 2 | 같은 페어(A↔B) 반복 2회 초과 시 중단 |
| 쿼터 필터 | — | `council.select_members` 의 가용성 판정을 그대로 재사용 |
| 킬 스위치 | — | 채널 단위 "에이전트 자동 응답 정지" 토글 |

한도에 걸리면 실패가 아니라 **일시정지**로 두고 사람이 "계속" 을 누르면 이어간다
(비전 보드의 `paused`/`resume` 패턴과 동일하게).

### B-2. 실행 층 — 동적 위임(handoff)

비전 보드는 계획 시점에 그래프가 확정되고 실행 중에는 자라지 않는다. Grok Bot의
"봇이 다른 봇에게 일을 넘긴다"에 대응하려면 실행 중 태스크 추가가 필요하다.

```sql
ALTER TABLE tasks ADD COLUMN agent_id INTEGER;          -- 담당 에이전트
ALTER TABLE tasks ADD COLUMN origin_task_id INTEGER;    -- 위임한 태스크
```

태스크 프롬프트에 "직접 하기 어렵거나 다른 역할이 맡아야 하면 아래 JSON을 붙여라"
규약을 넣고, 결과 회수 지점(`orchestrator._sync_tasks`)에서 파싱한다:

```json
{"handoff": {"to": "researcher", "title": "…", "description": "…", "reason": "…"}}
```

파싱하면 새 태스크를 만들고 `depends_on = 현재 태스크 seq` 로 붙인다 → 그래프가
실행 중에 자란다. **상한 `AOS_ORCH_MAX_DYNAMIC_TASKS`(기본 3)** 를 두고, 초과하면
위임을 무시하고 로그만 남긴다. 구조화 JSON 회수는 `orchestrator.parse_task_chat_reply`
가 이미 쓰는 패턴이라 그대로 따라 쓰면 된다.

### B-3. 안 하는 것

- **에이전트 간 세션(대화 컨텍스트) 공유.** 벤더가 다르면 불가능하고, 같아도
  `--resume` 은 단일 스레드 가정이라 교차 오염이 난다. 텍스트 인계를 유지한다.
- **자율 채널 개설·자율 멘션 확산.** 에이전트는 이미 존재하는 채널의 멤버에게만
  말을 걸 수 있다.

---

## 제안 C — 승인 게이트

Grok Bot의 차별점이자 이 리포의 가장 큰 구멍이다. 지금은 claude 가 `acceptEdits`
+ Bash 사전 허용으로 돌고(`app/providers.py:76`), 사후 git diff 되돌리기로만
방어한다. 파일 변경엔 충분하지만 **메일 발송·시트 쓰기·결제 같은 비가역 동작엔
아무 방어가 없다.**

분석문의 Agent SDK `defer` 훅은 쓸 수 없다(SDK를 안 쓴다). 세 경로가 있고,
**권장 순서는 1 → 2 → 3** 이다.

**1. 벤더 중립 "제안 → 승인 → 실행" (1순위, 지금 바로 가능)**
비가역 도구는 애초에 에이전트에 붙이지 않는다(MCP 프로필에서 제외 = `agents.mcp_profile`).
에이전트는 실행 대신 **행동 제안**을 구조화해 내놓고, 앱이 approvals 행으로 만든다.
사람이 승인하면 앱이 별도 잡으로 실행한다. 비전 보드의 계획 승인과 같은 패턴이고
모든 CLI에 동일하게 먹는다.

```sql
CREATE TABLE approvals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id INTEGER, agent_id INTEGER,
  kind TEXT NOT NULL,               -- action | tool_use | plan
  title TEXT NOT NULL, payload TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'pending',
  requested_at TEXT NOT NULL, decided_at TEXT, decided_by TEXT
);
```
`GET /api/approvals`, `POST /api/approvals/{id}/approve|reject` + 홈 대시보드
"승인 인박스" 탭. 알림은 SSE(`stream_hub`) 재사용.

**2. claude 훅 기반 도구 승인 (스파이크 필요)**
`--include-hook-events` 로 훅 이벤트를 stream-json 에 실을 수 있다. 다만 지금은
사용자 전역 훅이 결과를 덮는 문제 때문에 `disableAllHooks: true` 로 훅을 통째로
끄고 있다(`app/providers.py:77`). 앱 훅만 살리고 전역 훅은 죽일 수 있는지 **실측
확인 필요**. 되면 claude 한정으로 진짜 도구 단위 게이트가 생긴다.

**3. `--permission-mode manual` (미검증, 위험)**
이 빌드에 존재하지만, 헤드리스에서 승인 대기가 어떻게 표면화되는지(또는 그냥 행이
나는지) 확인 전에는 채택 불가. 잡 타임아웃으로 죽을 가능성이 크다.

정책 테이블은 에이전트 단위(`agents.approval_policy`)로 두어, 읽기 전용 리서치
에이전트는 자동 승인, 외부에 뭔가 보내는 에이전트는 전건 승인으로 나눈다.

> **구현 결과(V4.8)**: 1번 경로로 구현했다(`app/approvals.py`). 구현하며 분명해진
> 것 하나 — **`ask` 정책 자체는 강제가 아니라 지시다.** 프롬프트로 전달될 뿐이라
> 모델이 그냥 실행해 버리는 것을 이 플래그만으로는 막지 못한다. 실제 강제는 도구를
> 주지 않는 데서 나오므로(`read_only=1` + claude = plan 모드·Bash 미허용), UI 가
> `ask` 를 켤 때 읽기 전용을 함께 쓰라고 안내한다. 이 한계를 숨기면 사용자가 있지도
> 않은 방어를 믿게 된다. 2·3번(훅·`manual` 모드)은 그대로 미검증 과제로 남는다.

---

## 제안 D — 루틴 (반복 실행)

지금 launchd는 서버를 띄울 뿐이고 반복 작업 개념이 없다.

```sql
CREATE TABLE routines (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL, prompt TEXT NOT NULL,
  agent_id INTEGER, provider TEXT, workdir TEXT,
  schedule TEXT NOT NULL DEFAULT 'manual',   -- 5필드 cron | manual
  enabled INTEGER NOT NULL DEFAULT 1,
  last_run_at TEXT, next_run_at TEXT, created_at TEXT NOT NULL
);
```

`orchestrator_loop` 옆에 `routine_loop` 을 하나 더 lifespan 에 등록한다(60초 틱,
외부 의존성 불필요). Grok Bot의 "가르치면 저장"에 해당하는 부분은 **성공한 잡·
프로젝트에 "루틴으로 저장" 버튼**을 붙이는 것으로 대부분 달성된다 — prompt·
provider·workdir 를 그대로 복사하면 되고 새 UI가 거의 필요 없다.

## 제안 E — 에이전트별 기억

`app/memory.py` + 노트 파이프라인이 이미 있다. `agents.memory_note` 를 두고 잡
종료 시 그 노트에 요약을 append 하면, 다음 실행에서 컨텍스트로 붙일 수 있다
(메모리 첨부 경로도 이미 있다). 선호·문체 학습은 이 위에서 자연스럽게 나온다.
우선순위는 낮다 — A·B가 없으면 붙일 대상이 없다.

---

## 로드맵

| 순서 | 항목 | 규모 | 주로 건드리는 파일 |
|---|---|---|---|
| 1 | **A. agents 테이블 + 페르소나 주입 + jobs.agent_id** | 0.5~1일 | `db.py`, `worker.py`, `main.py`, 셋업 UI |
| 2 | **B-1. 채널 @멘션 + 에이전트 간 대화 + 루프 방지** | 1~2일 | `db.py`, `main.py:632`, `worker.py`, `static/channels.js` |
| 3 | **C-1. approvals + 승인 인박스 (벤더 중립)** | 1~2일 | `db.py`, `main.py`, 홈 대시보드 |
| 4 | **B-2. 워크플로 동적 위임** | 1일 | `orchestrator.py` |
| 5 | **D. routines + 루틴으로 저장** | 1일 | `db.py`, `main.py`, lifespan |
| 6 | C-2 훅 기반 도구 승인 실측 스파이크 | 0.5일 | `providers.py` |
| 7 | E. 에이전트별 기억 | 0.5일 | `memory.py`, `worker.py` |

1~2번만 끝나도 "에이전트를 만들고 서로 대화시킨다"는 요구는 충족된다. 3번까지
가면 Grok Bot 대비 실질적 기능 격차가 사라진다.

## 비목표

- **API 키 전환** — 구독 CLI 헤드리스 원칙을 유지한다(`docs/plan.md` 핵심 설계 원칙).
- **봇마다 전용 VM/컴퓨터** — 워크스페이스 디렉터리 격리로 충분하다.
- **Tailscale Funnel** — 공개 노출 + 신원 헤더 없음. 절대 켜지 않는다.
- **Web Push / PWA 껍데기** — SSE + 기존 접근으로 당장은 충분. A~D 이후 재검토.
- **에이전트 자율 채널 개설·무제한 연쇄** — 한도와 킬 스위치를 항상 사람이 쥔다.
