import sqlite3
from datetime import datetime, timezone

from app import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  prompt TEXT NOT NULL,
  provider TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued',
  session_id TEXT,
  output TEXT NOT NULL DEFAULT '',
  error TEXT,
  resume_at TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  timeout_sec INTEGER,
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
    return conn


def create_job(conn, prompt, provider, timeout_sec=None):
    cur = conn.execute(
        "INSERT INTO jobs (prompt, provider, timeout_sec, created_at) VALUES (?, ?, ?, ?)",
        (prompt, provider, timeout_sec, now_iso()),
    )
    conn.commit()
    return cur.lastrowid


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


def claim_next_job(conn):
    now = now_iso()
    row = conn.execute(
        "SELECT * FROM jobs WHERE status = 'queued' "
        "OR (status = 'rate_limited' AND resume_at <= ?) ORDER BY id LIMIT 1",
        (now,),
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
    conn.execute("UPDATE jobs SET status = 'queued' WHERE status = 'running'")
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
