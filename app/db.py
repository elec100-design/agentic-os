import re
import sqlite3
from datetime import datetime, timezone

from app import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  prompt TEXT NOT NULL,
  provider TEXT NOT NULL,
  model TEXT,
  status TEXT NOT NULL DEFAULT 'queued',
  session_id TEXT,
  output TEXT NOT NULL DEFAULT '',
  error TEXT,
  resume_at TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  timeout_sec INTEGER,
  note_path TEXT,
  workdir TEXT,
  created_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT
);
CREATE TABLE IF NOT EXISTS usage_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  provider TEXT NOT NULL,
  ts TEXT NOT NULL,
  duration_sec REAL,
  outcome TEXT NOT NULL,
  job_id INTEGER
);
CREATE TABLE IF NOT EXISTS projects (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  goal TEXT NOT NULL,
  title TEXT,
  status TEXT NOT NULL DEFAULT 'planning',
  plan_job_id INTEGER,
  planner TEXT,
  planner_model TEXT,
  workdir TEXT,
  error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT
);
CREATE TABLE IF NOT EXISTS tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL,
  seq INTEGER NOT NULL,
  title TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  task_type TEXT NOT NULL DEFAULT 'text',
  provider TEXT NOT NULL,
  depends_on TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'pending',
  job_id INTEGER,
  output TEXT NOT NULL DEFAULT '',
  artifact_path TEXT,
  error TEXT,
  model TEXT,
  extra_instruction TEXT,
  pos_x REAL,
  pos_y REAL,
  created_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT
);
CREATE TABLE IF NOT EXISTS channels (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  slug TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  topic TEXT NOT NULL DEFAULT '',
  workdir TEXT,
  default_provider TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL,
  updated_at TEXT
);
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  channel_id INTEGER NOT NULL REFERENCES channels(id),
  parent_id INTEGER REFERENCES messages(id),
  root_id INTEGER REFERENCES messages(id),
  seq INTEGER NOT NULL,
  role TEXT NOT NULL,
  author TEXT NOT NULL DEFAULT '',
  body TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'done',
  provider TEXT,
  model TEXT,
  session_id TEXT,
  job_id INTEGER REFERENCES jobs(id),
  reply_count INTEGER NOT NULL DEFAULT 0,
  last_reply_at TEXT,
  error TEXT,
  created_task_id INTEGER REFERENCES tasks(id),
  created_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_messages_channel_seq ON messages(channel_id, seq);
CREATE INDEX IF NOT EXISTS idx_messages_parent ON messages(parent_id);
CREATE INDEX IF NOT EXISTS idx_messages_root ON messages(root_id);
CREATE TABLE IF NOT EXISTS execution_steps (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  message_id INTEGER NOT NULL REFERENCES messages(id),
  job_id INTEGER,
  seq INTEGER NOT NULL,
  kind TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT '',
  detail TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'done',
  created_at TEXT NOT NULL,
  finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_steps_message_seq ON execution_steps(message_id, seq);
CREATE TABLE IF NOT EXISTS test_goals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  result TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT
);
CREATE TABLE IF NOT EXISTS board_tabs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  board_id INTEGER NOT NULL,
  title TEXT NOT NULL,
  kind TEXT NOT NULL,
  ref_id INTEGER,
  status TEXT NOT NULL DEFAULT 'pending',
  order_index INTEGER NOT NULL DEFAULT 0,
  is_active INTEGER NOT NULL DEFAULT 0,
  deleted_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_board_tabs_board_order
  ON board_tabs(board_id, deleted_at, order_index);
CREATE UNIQUE INDEX IF NOT EXISTS idx_board_tabs_active_ref
  ON board_tabs(board_id, kind, ref_id) WHERE deleted_at IS NULL AND ref_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS task_messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id INTEGER NOT NULL REFERENCES tasks(id),
  role TEXT NOT NULL,
  content TEXT NOT NULL DEFAULT '',
  suggested_description TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_task_messages_task ON task_messages(task_id, id);
CREATE TABLE IF NOT EXISTS workflow_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL REFERENCES projects(id),
  status TEXT NOT NULL DEFAULT 'running',
  started_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  heartbeat_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_workflow_runs_project ON workflow_runs(project_id, id);
CREATE TABLE IF NOT EXISTS task_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id INTEGER NOT NULL REFERENCES tasks(id),
  run_id INTEGER NOT NULL REFERENCES workflow_runs(id),
  status TEXT NOT NULL DEFAULT 'pending',
  attempt INTEGER NOT NULL DEFAULT 1,
  output TEXT NOT NULL DEFAULT '',
  agent TEXT,
  error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT,
  finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_task_runs_run ON task_runs(run_id, task_id);
CREATE INDEX IF NOT EXISTS idx_task_runs_task ON task_runs(task_id, attempt);
"""


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_conn(db_path=None):
    path = db_path or config.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn):
    """기존 DB에 없는 컬럼을 더한다 (CREATE TABLE IF NOT EXISTS는 새 컬럼을 못 붙임)."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(jobs)")}
    for col in ("model", "note_path", "workdir", "route_reason"):
        if col not in cols:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} TEXT")
    # 채널/메시지 계층에서 잡을 역참조하기 위한 링크 (채널 기능 도입 전 잡에는 NULL)
    for col in ("channel_id", "message_id"):
        if col not in cols:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} INTEGER")
    project_cols = {r["name"] for r in conn.execute("PRAGMA table_info(projects)")}
    if "planner_model" not in project_cols:
        conn.execute("ALTER TABLE projects ADD COLUMN planner_model TEXT")
    # 프로젝트 상세의 chat-rail이 붙는 전용 채널 — 기존 프로젝트에는 NULL,
    # 처음 상세 페이지를 열 때 get_or_create_project_channel()이 채워준다.
    if "channel_id" not in project_cols:
        conn.execute("ALTER TABLE projects ADD COLUMN channel_id INTEGER")
    # 일시정지 원인 — "manual"(사용자가 일시정지 버튼을 누름) vs "task_failed"
    # (태스크 실패로 자동 정지). status="paused"는 둘 다 쓰지만 배너 문구와
    # 재개 가능 여부 판단에 원인 구분이 필요하다. NULL이면 일시정지 아님.
    if "pause_reason" not in project_cols:
        conn.execute("ALTER TABLE projects ADD COLUMN pause_reason TEXT")
    # 다이어그램 편집기가 저장하는 노드 좌표 (NULL이면 자동 배치)
    task_cols = {r["name"] for r in conn.execute("PRAGMA table_info(tasks)")}
    for col in ("pos_x", "pos_y"):
        if col not in task_cols:
            conn.execute(f"ALTER TABLE tasks ADD COLUMN {col} REAL")
    # 오류 조치용 — 모델 교체(model)와 추가 지시(extra_instruction).
    # 둘 다 NULL이면 계획이 정한 provider 기본값으로 실행된다.
    for col in ("model", "extra_instruction"):
        if col not in task_cols:
            conn.execute(f"ALTER TABLE tasks ADD COLUMN {col} TEXT")
    tab_cols = {r["name"] for r in conn.execute("PRAGMA table_info(board_tabs)")}
    # Early development databases may have the table without the soft-delete
    # marker. Keep the normal additive migration convention used above.
    if "deleted_at" not in tab_cols:
        conn.execute("ALTER TABLE board_tabs ADD COLUMN deleted_at TEXT")
    # 채팅 메시지가 태스크를 만들었을 때의 역참조 — 채팅→탭 자동 오픈 판별용.
    # 채널/메시지 기능 도입 전 DB에는 컬럼 자체가 없으므로 추가한다.
    message_cols = {r["name"] for r in conn.execute("PRAGMA table_info(messages)")}
    if "created_task_id" not in message_cols:
        conn.execute("ALTER TABLE messages ADD COLUMN created_task_id INTEGER REFERENCES tasks(id)")
    # 태스크 사이드바 채팅의 '적용' 제안 — 초기 배포판에는 없었을 수 있는 컬럼.
    task_msg_cols = {r["name"] for r in conn.execute("PRAGMA table_info(task_messages)")}
    if "suggested_description" not in task_msg_cols:
        conn.execute("ALTER TABLE task_messages ADD COLUMN suggested_description TEXT")
    conn.commit()


def create_job(conn, prompt, provider, timeout_sec=None, session_id=None,
               model=None, workdir=None, note_path=None, route_reason=None,
               channel_id=None, message_id=None):
    cur = conn.execute(
        "INSERT INTO jobs (prompt, provider, model, timeout_sec, session_id, "
        "workdir, note_path, route_reason, channel_id, message_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (prompt, provider, model, timeout_sec, session_id, workdir, note_path,
         route_reason, channel_id, message_id, now_iso()),
    )
    conn.commit()
    return cur.lastrowid


def delete_job(conn, job_id):
    conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
    conn.commit()


def delete_jobs_by_note(conn, note_path):
    """노트에 연결된 작업 행을 삭제 (메모리↔작업큐 연동)."""
    cur = conn.execute("DELETE FROM jobs WHERE note_path = ?", (note_path,))
    conn.commit()
    return cur.rowcount


def jobs_sharing_note(conn, note_path, exclude_id):
    """같은 노트에 연결된 다른 작업 수 (스레드 노트 보존 판단용)."""
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM jobs WHERE note_path = ? AND id != ?",
        (note_path, exclude_id),
    ).fetchone()
    return row["c"]


def list_note_workdirs(conn):
    """노트가 연결된 잡의 (note_path, workdir) 목록 (소급 그룹핑용)."""
    return conn.execute(
        "SELECT note_path, workdir FROM jobs "
        "WHERE note_path IS NOT NULL AND workdir IS NOT NULL"
    ).fetchall()


def relink_note_path(conn, old_path, new_path):
    conn.execute("UPDATE jobs SET note_path = ? WHERE note_path = ?",
                 (new_path, old_path))
    conn.commit()


def get_job(conn, job_id):
    return conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()


def list_jobs(conn, limit=50):
    return conn.execute(
        "SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()


def update_job(conn, job_id, **fields):
    cols = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE jobs SET {cols} WHERE id = ?", (*fields.values(), job_id))
    conn.commit()


def append_output(conn, job_id, text):
    conn.execute("UPDATE jobs SET output = output || ? WHERE id = ?", (text, job_id))
    conn.commit()


def claim_next_job(conn, exclude_providers=None):
    """실행 가능한 가장 오래된 잡을 원자적으로 'running'으로 표시하고 반환한다.

    exclude_providers를 주면 그 provider의 잡은 건너뛴다 — 워커가 이미 실행
    중인 provider(같은 CLI 직렬화)나 배타 실행 중인 협의 잡을 제외하는 데 쓴다.
    SELECT~UPDATE 사이에 await가 없어 단일 이벤트 루프에서 원자적이다.
    """
    now = now_iso()
    params = [now]
    exclude_sql = ""
    excl = list(exclude_providers or ())
    if excl:
        placeholders = ", ".join("?" for _ in excl)
        exclude_sql = f" AND provider NOT IN ({placeholders})"
        params.extend(excl)
    row = conn.execute(
        "SELECT * FROM jobs WHERE (status = 'queued' "
        "OR (status = 'rate_limited' AND resume_at <= ?))" + exclude_sql +
        " ORDER BY id LIMIT 1",
        params,
    ).fetchone()
    if row is None:
        return None
    conn.execute(
        "UPDATE jobs SET status = 'running', started_at = ?, attempts = attempts + 1 "
        "WHERE id = ?",
        (now, row["id"]),
    )
    conn.commit()
    return get_job(conn, row["id"])


def recover_running(conn):
    """서버 재시작 시 'running' 상태 잡을 복구한다.
    협의(council) 잡은 재시도가 매우 비싸고(여러 CLI 재실행), 재개 시
    누적 출력이 중복 저장되므로 처음부터 다시 돌리지 않고 실패 처리한다.

    비전 보드 쪽 실행 세션/시도도 함께 '중단됨'으로 마감한다 — 세션은 여전히
    재개 대상으로 남고(get_resumable_run), 다음 시도는 새 task_runs 행이 된다."""
    mark_interrupted_task_runs(conn)
    mark_interrupted_workflow_runs(conn)
    conn.execute(
        "UPDATE jobs SET status = 'failed', error = 'interrupted: server restarted', "
        "finished_at = ? WHERE status = 'running' AND provider = 'council'",
        (now_iso(),),
    )
    conn.execute(
        "UPDATE jobs SET status = 'queued' WHERE status = 'running'")
    conn.commit()


# --- 비전 보드: 프로젝트(목표)와 태스크(분해된 작업 단위) ---
# 태스크는 실행 시점에 jobs 행으로 디스패치된다(task.job_id 링크). 프로젝트/
# 태스크 테이블은 오케스트레이터의 북키핑 전용이고 실행은 전부 worker가 한다.

def create_project(conn, goal, workdir=None, planner_model=None):
    cur = conn.execute(
        "INSERT INTO projects (goal, workdir, planner_model, created_at) "
        "VALUES (?, ?, ?, ?)",
        (goal, workdir, planner_model, now_iso()),
    )
    conn.commit()
    return cur.lastrowid


def get_project(conn, project_id):
    return conn.execute(
        "SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()


def get_or_create_project_channel(conn, project):
    """프로젝트 상세의 chat-rail이 쓸 채널을 반환, 없으면 그 자리에서 만든다."""
    if project["channel_id"]:
        channel = get_channel(conn, project["channel_id"])
        if channel is not None:
            return channel
    title = project["title"] or (project["goal"] or "")[:60]
    channel_id = create_channel(
        conn, title, workdir=project["workdir"], default_provider=project["planner"])
    conn.execute("UPDATE projects SET channel_id = ? WHERE id = ?",
                 (channel_id, project["id"]))
    conn.commit()
    return get_channel(conn, channel_id)


def list_projects(conn, limit=50):
    return conn.execute(
        "SELECT * FROM projects ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


def list_projects_by_status(conn, status):
    return conn.execute(
        "SELECT * FROM projects WHERE status = ? ORDER BY id DESC", (status,)
    ).fetchall()


def active_projects(conn):
    """오케스트레이터 루프가 전진시켜야 하는 프로젝트들."""
    return conn.execute(
        "SELECT * FROM projects WHERE status IN ('planning', 'running')"
    ).fetchall()


def update_project(conn, project_id, **fields):
    fields["updated_at"] = now_iso()
    cols = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE projects SET {cols} WHERE id = ?",
                 (*fields.values(), project_id))
    conn.commit()


def delete_project(conn, project_id):
    """프로젝트와 소속 태스크, 태스크가 만든 잡(계획 잡 포함)까지 삭제."""
    project = get_project(conn, project_id)
    if project is None:
        return
    job_ids = [r["job_id"] for r in conn.execute(
        "SELECT job_id FROM tasks WHERE project_id = ? AND job_id IS NOT NULL",
        (project_id,))]
    if project["plan_job_id"]:
        job_ids.append(project["plan_job_id"])
    for jid in job_ids:
        conn.execute("DELETE FROM jobs WHERE id = ?", (jid,))
    conn.execute("DELETE FROM tasks WHERE project_id = ?", (project_id,))
    conn.execute("DELETE FROM board_tabs WHERE board_id = ?", (project_id,))
    conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    conn.commit()


def create_task(conn, project_id, seq, title, description, task_type,
                provider, depends_on=""):
    cur = conn.execute(
        "INSERT INTO tasks (project_id, seq, title, description, task_type, "
        "provider, depends_on, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (project_id, seq, title, description, task_type, provider, depends_on,
         now_iso()),
    )
    conn.commit()
    return cur.lastrowid


def get_task(conn, task_id):
    return conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()


def task_by_job(conn, job_id):
    """잡을 만든 태스크 (미디어 잡이 산출물 경로를 정할 때 사용)."""
    return conn.execute(
        "SELECT * FROM tasks WHERE job_id = ?", (job_id,)).fetchone()


def list_tasks(conn, project_id):
    return conn.execute(
        "SELECT * FROM tasks WHERE project_id = ? ORDER BY seq", (project_id,)
    ).fetchall()


def update_task(conn, task_id, **fields):
    cols = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE tasks SET {cols} WHERE id = ?",
                 (*fields.values(), task_id))
    conn.commit()


def delete_task(conn, task_id):
    conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    conn.commit()


def next_task_seq(conn, project_id):
    """새 노드에 줄 seq = 현재 최대 seq + 1. 삭제로 비는 번호가 생겨도 상관없다 —
    seq는 순서가 아니라 depends_on이 가리키는 식별자로만 쓰인다."""
    row = conn.execute(
        "SELECT MAX(seq) AS m FROM tasks WHERE project_id = ?", (project_id,)
    ).fetchone()
    return (row["m"] or 0) + 1


def create_task_message(conn, task_id, role, content, suggested_description=None):
    cur = conn.execute(
        "INSERT INTO task_messages (task_id, role, content, suggested_description, "
        "created_at) VALUES (?, ?, ?, ?, ?)",
        (task_id, role, content, suggested_description, now_iso()),
    )
    conn.commit()
    return cur.lastrowid


def list_task_messages(conn, task_id):
    return conn.execute(
        "SELECT * FROM task_messages WHERE task_id = ? ORDER BY id", (task_id,)
    ).fetchall()



# --- 워크플로우 실행 세션(재개용): workflow_runs + task_runs --------------------
# workflow_runs = 프로젝트 한 번의 "전체 실행" 세션(일시정지/재개/중단 이력).
# task_runs = 그 세션 안에서 태스크별 시도 이력(부분 출력·에이전트·에러 보존).
# 서버가 중간에 죽어도 이 두 테이블만으로 "어디까지 끝났는지"를 복원해
# 완료된 태스크는 건너뛰고 미완료 태스크부터 재개할 수 있다.

def create_workflow_run(conn, project_id):
    now = now_iso()
    cur = conn.execute(
        "INSERT INTO workflow_runs (project_id, status, started_at, updated_at, "
        "heartbeat_at) VALUES (?, 'running', ?, ?, ?)",
        (project_id, now, now, now),
    )
    conn.commit()
    return cur.lastrowid


def get_workflow_run(conn, run_id):
    return conn.execute(
        "SELECT * FROM workflow_runs WHERE id = ?", (run_id,)).fetchone()


def list_workflow_runs(conn, project_id):
    return conn.execute(
        "SELECT * FROM workflow_runs WHERE project_id = ? ORDER BY id DESC",
        (project_id,)).fetchall()


def get_resumable_run(conn, project_id):
    """재개 가능한 최신 실행 세션(running/paused/interrupted 중 가장 최근).
    completed/failed로 마감된 세션은 재개 대상이 아니다."""
    return conn.execute(
        "SELECT * FROM workflow_runs WHERE project_id = ? AND status IN "
        "('running', 'paused', 'interrupted') ORDER BY id DESC LIMIT 1",
        (project_id,)).fetchone()


def update_workflow_run(conn, run_id, **fields):
    fields["updated_at"] = now_iso()
    cols = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE workflow_runs SET {cols} WHERE id = ?",
                 (*fields.values(), run_id))
    conn.commit()


def heartbeat_workflow_run(conn, run_id):
    now = now_iso()
    conn.execute(
        "UPDATE workflow_runs SET heartbeat_at = ?, updated_at = ? WHERE id = ?",
        (now, now, run_id))
    conn.commit()


def mark_interrupted_workflow_runs(conn):
    """서버 재시작 시 'running'으로 남은 세션을 'interrupted'로 표시한다.
    재시작 후 get_resumable_run()이 이 세션을 여전히 재개 대상으로 찾는다."""
    now = now_iso()
    conn.execute(
        "UPDATE workflow_runs SET status = 'interrupted', updated_at = ? "
        "WHERE status = 'running'", (now,))
    conn.commit()


def get_pending_tasks_for_run(conn, run_id):
    """이 세션에서 아직 완료(done)로 기록되지 않은 태스크를 seq 순으로 반환한다.
    완료된 태스크는 건너뛰고 여기서부터 재개하면 된다."""
    run = get_workflow_run(conn, run_id)
    if run is None:
        return []
    return conn.execute(
        "SELECT t.* FROM tasks t WHERE t.project_id = ? AND t.id NOT IN "
        "(SELECT task_id FROM task_runs WHERE run_id = ? AND status = 'done') "
        "ORDER BY t.seq", (run["project_id"], run_id),
    ).fetchall()


def create_task_run(conn, task_id, run_id, agent=None):
    """태스크의 새 시도를 기록한다. attempt는 이 태스크 전체 이력에서 이어진다
    (세션이 바뀌어도 몇 번째 시도인지 연속으로 셀 수 있도록)."""
    row = conn.execute(
        "SELECT MAX(attempt) AS m FROM task_runs WHERE task_id = ?",
        (task_id,)).fetchone()
    attempt = (row["m"] or 0) + 1
    now = now_iso()
    cur = conn.execute(
        "INSERT INTO task_runs (task_id, run_id, status, attempt, agent, "
        "created_at, updated_at) VALUES (?, ?, 'running', ?, ?, ?, ?)",
        (task_id, run_id, attempt, agent, now, now),
    )
    conn.commit()
    return cur.lastrowid


def get_task_run(conn, task_run_id):
    return conn.execute(
        "SELECT * FROM task_runs WHERE id = ?", (task_run_id,)).fetchone()


def get_active_task_run(conn, task_id, run_id):
    """해당 세션에서 그 태스크의 가장 최근 시도(진행 중이든 마감됐든)."""
    return conn.execute(
        "SELECT * FROM task_runs WHERE task_id = ? AND run_id = ? "
        "ORDER BY id DESC LIMIT 1", (task_id, run_id)).fetchone()


def list_task_runs(conn, task_id):
    """태스크의 전체 시도 이력(세션 무관), attempt 순."""
    return conn.execute(
        "SELECT * FROM task_runs WHERE task_id = ? ORDER BY attempt",
        (task_id,)).fetchall()


def append_task_run_output(conn, task_run_id, text):
    now = now_iso()
    conn.execute(
        "UPDATE task_runs SET output = output || ?, updated_at = ? WHERE id = ?",
        (text, now, task_run_id))
    conn.commit()


def mark_task_run_done(conn, task_run_id):
    now = now_iso()
    conn.execute(
        "UPDATE task_runs SET status = 'done', finished_at = ?, updated_at = ? "
        "WHERE id = ?", (now, now, task_run_id))
    conn.commit()


def mark_task_run_failed(conn, task_run_id, error=None):
    now = now_iso()
    conn.execute(
        "UPDATE task_runs SET status = 'failed', error = ?, finished_at = ?, "
        "updated_at = ? WHERE id = ?", (error, now, now, task_run_id))
    conn.commit()


def mark_interrupted_task_runs(conn):
    """서버 재시작 시 'running'으로 남은 태스크 시도를 'interrupted'로 표시한다.
    다음 재개는 새 create_task_run() 호출(attempt+1)로 이어간다."""
    now = now_iso()
    conn.execute(
        "UPDATE task_runs SET status = 'interrupted', "
        "error = COALESCE(error, 'interrupted: server restarted'), "
        "finished_at = ?, updated_at = ? WHERE status = 'running'",
        (now, now))
    conn.commit()


def delete_tasks(conn, project_id, statuses=None):
    """프로젝트의 태스크 삭제 (statuses를 주면 그 상태만 — 재계획용)."""
    if statuses:
        placeholders = ", ".join("?" for _ in statuses)
        conn.execute(
            f"DELETE FROM tasks WHERE project_id = ? AND status IN ({placeholders})",
            (project_id, *statuses))
    else:
        conn.execute("DELETE FROM tasks WHERE project_id = ?", (project_id,))
    conn.commit()


# --- 비전 보드 워크스페이스 탭 ----------------------------------------------

def list_board_tabs(conn, board_id, include_closed=False):
    where = "board_id = ?"
    if not include_closed:
        where += " AND deleted_at IS NULL"
    return conn.execute(
        f"SELECT * FROM board_tabs WHERE {where} ORDER BY order_index, id",
        (board_id,),
    ).fetchall()


def get_board_tab(conn, tab_id, include_closed=False):
    sql = "SELECT * FROM board_tabs WHERE id = ?"
    if not include_closed:
        sql += " AND deleted_at IS NULL"
    return conn.execute(sql, (tab_id,)).fetchone()


def get_or_create_board_tab(conn, board_id, title, kind, ref_id=None,
                            status="pending", activate=True):
    """연결 대상(kind/ref_id)별 탭을 하나만 유지하고, 닫힌 탭은 재사용한다."""
    if ref_id is not None:
        tab = conn.execute(
            "SELECT * FROM board_tabs WHERE board_id = ? AND kind = ? "
            "AND ref_id = ? ORDER BY id LIMIT 1", (board_id, kind, ref_id),
        ).fetchone()
        if tab is not None:
            fields = {"title": title, "status": status, "updated_at": now_iso()}
            if tab["deleted_at"] is not None:
                fields["deleted_at"] = None
            _update_board_tab_fields(conn, tab["id"], fields, commit=False)
            if activate:
                set_active_board_tab(conn, board_id, tab["id"], commit=False)
            conn.commit()
            return tab["id"]

    next_index = conn.execute(
        "SELECT COALESCE(MAX(order_index), -1) + 1 AS n FROM board_tabs "
        "WHERE board_id = ? AND deleted_at IS NULL", (board_id,)
    ).fetchone()["n"]
    cur = conn.execute(
        "INSERT INTO board_tabs (board_id, title, kind, ref_id, status, "
        "order_index, is_active, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)",
        (board_id, title, kind, ref_id, status, next_index, now_iso(), now_iso()),
    )
    if activate:
        set_active_board_tab(conn, board_id, cur.lastrowid, commit=False)
    conn.commit()
    return cur.lastrowid


def _update_board_tab_fields(conn, tab_id, fields, commit=True):
    cols = ", ".join(f"{key} = ?" for key in fields)
    conn.execute(f"UPDATE board_tabs SET {cols} WHERE id = ?",
                 (*fields.values(), tab_id))
    if commit:
        conn.commit()


def update_board_tab(conn, tab_id, **fields):
    fields["updated_at"] = now_iso()
    _update_board_tab_fields(conn, tab_id, fields)


def set_active_board_tab(conn, board_id, tab_id, commit=True):
    """한 보드에서 열린 탭은 정확히 하나만 활성화되도록 한다."""
    conn.execute("UPDATE board_tabs SET is_active = 0, updated_at = ? "
                 "WHERE board_id = ? AND deleted_at IS NULL", (now_iso(), board_id))
    cur = conn.execute("UPDATE board_tabs SET is_active = 1, updated_at = ? "
                       "WHERE id = ? AND board_id = ? AND deleted_at IS NULL",
                       (now_iso(), tab_id, board_id))
    if cur.rowcount != 1:
        raise ValueError("탭을 찾을 수 없습니다")
    if commit:
        conn.commit()


def close_board_tab(conn, board_id, tab_id):
    tab = get_board_tab(conn, tab_id)
    if tab is None or tab["board_id"] != board_id:
        return False
    was_active = bool(tab["is_active"])
    _update_board_tab_fields(conn, tab_id, {"deleted_at": now_iso(),
                                             "is_active": 0,
                                             "updated_at": now_iso()}, commit=False)
    if was_active:
        replacement = conn.execute(
            "SELECT id FROM board_tabs WHERE board_id = ? AND deleted_at IS NULL "
            "ORDER BY order_index, id LIMIT 1", (board_id,)).fetchone()
        if replacement:
            set_active_board_tab(conn, board_id, replacement["id"], commit=False)
    conn.commit()
    return True


def reorder_board_tabs(conn, board_id, tab_ids):
    """전달되지 않은 열린 탭은 뒤에 유지한다. 다른 보드 탭은 거부한다."""
    open_tabs = list_board_tabs(conn, board_id)
    by_id = {tab["id"]: tab for tab in open_tabs}
    if len(tab_ids) != len(set(tab_ids)) or any(tab_id not in by_id for tab_id in tab_ids):
        raise ValueError("유효하지 않은 탭 순서입니다")
    ordered = list(tab_ids) + [tab["id"] for tab in open_tabs if tab["id"] not in tab_ids]
    for index, tab_id in enumerate(ordered):
        _update_board_tab_fields(conn, tab_id,
                                 {"order_index": index, "updated_at": now_iso()},
                                 commit=False)
    conn.commit()


def log_usage(conn, provider, duration_sec, outcome, job_id=None):
    conn.execute(
        "INSERT INTO usage_log (provider, ts, duration_sec, outcome, job_id) "
        "VALUES (?, ?, ?, ?, ?)",
        (provider, now_iso(), duration_sec, outcome, job_id),
    )
    conn.commit()


def usage_counts(conn, since_iso):
    rows = conn.execute(
        "SELECT provider, outcome, COUNT(*) AS c FROM usage_log "
        "WHERE ts >= ? GROUP BY provider, outcome",
        (since_iso,),
    ).fetchall()
    result = {}
    for r in rows:
        result.setdefault(r["provider"], {})[r["outcome"]] = r["c"]
    return result


def limit_status(conn):
    rows = conn.execute(
        "SELECT provider, MIN(resume_at) AS resume_at FROM jobs "
        "WHERE status = 'rate_limited' GROUP BY provider"
    ).fetchall()
    return {r["provider"]: r["resume_at"] for r in rows}


# --- 채널: Slack/Telegram식 주제별 지속 대화 컨테이너 ------------------------

def _slugify(text):
    base = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (base or "channel")[:60]


def create_channel(conn, title, topic="", workdir=None, default_provider=None):
    slug = _slugify(title)
    candidate, i = slug, 2
    while conn.execute(
            "SELECT 1 FROM channels WHERE slug = ?", (candidate,)).fetchone():
        candidate = f"{slug}-{i}"
        i += 1
    cur = conn.execute(
        "INSERT INTO channels (slug, title, topic, workdir, default_provider, "
        "created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (candidate, title, topic, workdir, default_provider, now_iso()),
    )
    conn.commit()
    return cur.lastrowid


def get_channel(conn, channel_id):
    return conn.execute(
        "SELECT * FROM channels WHERE id = ?", (channel_id,)).fetchone()


def list_channels(conn, status="active"):
    if status:
        return conn.execute(
            "SELECT * FROM channels WHERE status = ? "
            "ORDER BY COALESCE(updated_at, created_at) DESC",
            (status,),
        ).fetchall()
    return conn.execute(
        "SELECT * FROM channels ORDER BY COALESCE(updated_at, created_at) DESC"
    ).fetchall()


def update_channel(conn, channel_id, **fields):
    fields["updated_at"] = now_iso()
    cols = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE channels SET {cols} WHERE id = ?",
                 (*fields.values(), channel_id))
    conn.commit()


def delete_channel(conn, channel_id):
    """채널과 소속 메시지, 실행 트레이스, 연결된 잡까지 모두 삭제."""
    msg_ids = [r["id"] for r in conn.execute(
        "SELECT id FROM messages WHERE channel_id = ?", (channel_id,))]
    job_ids = [r["job_id"] for r in conn.execute(
        "SELECT job_id FROM messages WHERE channel_id = ? AND job_id IS NOT NULL",
        (channel_id,))]
    if msg_ids:
        placeholders = ", ".join("?" for _ in msg_ids)
        conn.execute(
            f"DELETE FROM execution_steps WHERE message_id IN ({placeholders})",
            msg_ids)
    for jid in job_ids:
        conn.execute("DELETE FROM jobs WHERE id = ?", (jid,))
    conn.execute("DELETE FROM messages WHERE channel_id = ?", (channel_id,))
    conn.execute("DELETE FROM channels WHERE id = ?", (channel_id,))
    conn.commit()


# --- 메시지: 채널 타임라인의 발화 단위(루트) / 쓰레드 답장 -------------------

def next_message_seq(conn, channel_id):
    row = conn.execute(
        "SELECT MAX(seq) AS m FROM messages WHERE channel_id = ?",
        (channel_id,)).fetchone()
    return (row["m"] or 0) + 1


def create_message(conn, channel_id, role, body, author="", parent_id=None,
                    status="done", provider=None, model=None, session_id=None,
                    job_id=None):
    """parent_id는 항상 쓰레드 루트 메시지를 직접 가리켜야 한다(호출자가 정규화).
    parent_id가 없으면 이 메시지 자신이 채널의 새 루트가 된다."""
    seq = next_message_seq(conn, channel_id)
    root_id = None
    if parent_id:
        parent = get_message(conn, parent_id)
        if parent is not None:
            root_id = parent["root_id"] or parent["id"]
    cur = conn.execute(
        "INSERT INTO messages (channel_id, parent_id, root_id, seq, role, "
        "author, body, status, provider, model, session_id, job_id, "
        "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (channel_id, parent_id, root_id, seq, role, author, body, status,
         provider, model, session_id, job_id, now_iso()),
    )
    conn.commit()
    message_id = cur.lastrowid
    if root_id:
        conn.execute(
            "UPDATE messages SET reply_count = reply_count + 1, "
            "last_reply_at = ? WHERE id = ?", (now_iso(), root_id))
        conn.commit()
    update_channel(conn, channel_id)
    return message_id


def get_message(conn, message_id):
    return conn.execute(
        "SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()


def message_by_job(conn, job_id):
    return conn.execute(
        "SELECT * FROM messages WHERE job_id = ?", (job_id,)).fetchone()


def list_root_messages(conn, channel_id, before_seq=None, limit=50):
    """루트 메시지(parent_id IS NULL) 페이지네이션. 오래된 순으로 반환."""
    if before_seq is not None:
        rows = conn.execute(
            "SELECT * FROM messages WHERE channel_id = ? AND parent_id IS NULL "
            "AND seq < ? ORDER BY seq DESC LIMIT ?",
            (channel_id, before_seq, limit)).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM messages WHERE channel_id = ? AND parent_id IS NULL "
            "ORDER BY seq DESC LIMIT ?", (channel_id, limit)).fetchall()
    return list(reversed(rows))


def list_thread(conn, root_id):
    """쓰레드 루트 + 그 답장 전체를 seq 순으로."""
    return conn.execute(
        "SELECT * FROM messages WHERE id = ? OR root_id = ? ORDER BY seq",
        (root_id, root_id)).fetchall()


def update_message(conn, message_id, **fields):
    cols = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE messages SET {cols} WHERE id = ?",
                 (*fields.values(), message_id))
    conn.commit()


def latest_thread_session(conn, root_id):
    """쓰레드(root_id + 그 답장들) 안에서 가장 최근 session_id/provider를 찾는다.

    root_id 정규화(모든 답장이 루트를 직접 가리킴) 때문에 parent_id를
    거슬러 올라가는 방식으론 형제 메시지의 세션을 못 찾는다 — 쓰레드 전체를
    seq 역순으로 스캔해 가장 최근에 세션을 남긴 메시지를 찾는다."""
    for row in reversed(list_thread(conn, root_id)):
        if row["session_id"]:
            return row["session_id"], row["provider"]
    return None, None


# --- 실행 트레이스: 메시지 하나의 실행 중간 과정(생각/도구 호출/로그) --------

def next_step_seq(conn, message_id):
    row = conn.execute(
        "SELECT MAX(seq) AS m FROM execution_steps WHERE message_id = ?",
        (message_id,)).fetchone()
    return (row["m"] or 0) + 1


def create_execution_step(conn, message_id, kind, title="", detail="",
                           status="running", job_id=None):
    seq = next_step_seq(conn, message_id)
    cur = conn.execute(
        "INSERT INTO execution_steps (message_id, job_id, seq, kind, title, "
        "detail, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (message_id, job_id, seq, kind, title, detail, status, now_iso()),
    )
    conn.commit()
    return cur.lastrowid


def append_execution_step_detail(conn, step_id, text):
    conn.execute(
        "UPDATE execution_steps SET detail = detail || ? WHERE id = ?",
        (text, step_id))
    conn.commit()


def update_execution_step(conn, step_id, **fields):
    if fields.get("status") in ("done", "failed") and "finished_at" not in fields:
        fields["finished_at"] = now_iso()
    cols = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE execution_steps SET {cols} WHERE id = ?",
                 (*fields.values(), step_id))
    conn.commit()


def list_execution_steps(conn, message_id):
    return conn.execute(
        "SELECT * FROM execution_steps WHERE message_id = ? ORDER BY seq",
        (message_id,)).fetchall()


def create_test_goal(conn, name):
    cur = conn.execute(
        "INSERT INTO test_goals (name, created_at) VALUES (?, ?)",
        (name, now_iso()),
    )
    conn.commit()
    return cur.lastrowid


def get_test_goal(conn, goal_id):
    return conn.execute(
        "SELECT * FROM test_goals WHERE id = ?", (goal_id,)).fetchone()


def update_test_goal(conn, goal_id, **fields):
    fields["updated_at"] = now_iso()
    cols = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE test_goals SET {cols} WHERE id = ?",
                 (*fields.values(), goal_id))
    conn.commit()
