import os
import sqlite3
import threading
from contextlib import contextmanager

DB_PATH = os.path.join("data", "media_faren.db")

_local = threading.local()


def get_conn():
    conn = getattr(_local, "conn", None)
    if conn is None:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=30000")
        _local.conn = conn
    return conn


@contextmanager
def get_cursor():
    conn = get_conn()
    cur = conn.cursor()
    try:
        yield cur
        conn.commit()
    finally:
        cur.close()


def _get_user_version(cur):
    cur.execute("PRAGMA user_version")
    return cur.fetchone()[0]


def _set_user_version(cur, version):
    cur.execute(f"PRAGMA user_version = {version}")


def _column_exists(cur, table, column):
    cur.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cur.fetchall())


def _ensure_base_tables(cur):
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            status TEXT NOT NULL,
            mode TEXT NOT NULL,
            url TEXT,
            source_type TEXT NOT NULL,
            input_path TEXT,
            output_path TEXT,
            title TEXT,
            channel TEXT,
            percent REAL,
            speed TEXT,
            eta TEXT,
            size TEXT,
            message TEXT,
            options TEXT
        )
        """
    )


def _migrate(cur):
    _ensure_base_tables(cur)
    version = _get_user_version(cur)

    if version < 1:
        _set_user_version(cur, 1)
        version = 1

    if version < 2:
        if not _column_exists(cur, "jobs", "parent_job_id"):
            cur.execute("ALTER TABLE jobs ADD COLUMN parent_job_id TEXT")
        if not _column_exists(cur, "jobs", "pid"):
            cur.execute("ALTER TABLE jobs ADD COLUMN pid INTEGER")
        if not _column_exists(cur, "jobs", "last_event_at"):
            cur.execute("ALTER TABLE jobs ADD COLUMN last_event_at TEXT")
        _set_user_version(cur, 2)


def init_db():
    with get_cursor() as cur:
        _migrate(cur)
