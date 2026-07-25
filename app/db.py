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
  created_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT
);
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
    project_cols = {r["name"] for r in conn.execute("PRAGMA table_info(projects)")}
    if "planner_model" not in project_cols:
        conn.execute("ALTER TABLE projects ADD COLUMN planner_model TEXT")
    conn.commit()


def create_job(conn, prompt, provider, timeout_sec=None, session_id=None,
               model=None, workdir=None, note_path=None, route_reason=None):
    cur = conn.execute(
        "INSERT INTO jobs (prompt, provider, model, timeout_sec, session_id, "
        "workdir, note_path, route_reason, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (prompt, provider, model, timeout_sec, session_id, workdir, note_path,
         route_reason, now_iso()),
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
    누적 출력이 중복 저장되므로 처음부터 다시 돌리지 않고 실패 처리한다."""
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


def list_projects(conn, limit=50):
    return conn.execute(
        "SELECT * FROM projects ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


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
