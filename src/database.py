"""SQLite layer for deduplication and job tracking."""
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime, timedelta

DB_PATH = Path(__file__).parent.parent / "data" / "jobs.db"


def init_db():
    """Create tables if they don't exist."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS seen_jobs (
                id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                company TEXT,
                title TEXT,
                location TEXT,
                url TEXT,
                posted_at TEXT,
                seen_at TEXT NOT NULL,
                score INTEGER,
                status TEXT DEFAULT 'new',
                resume_path TEXT,
                cold_email_path TEXT,
                hiring_manager_email TEXT
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_seen_at ON seen_jobs(seen_at)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_status ON seen_jobs(status)")


@contextmanager
def conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def is_seen(job_id: str) -> bool:
    with conn() as c:
        r = c.execute("SELECT 1 FROM seen_jobs WHERE id = ?", (job_id,)).fetchone()
        return r is not None


def mark_seen(job: dict, score: int = 0, resume_path: str = None,
              cold_email_path: str = None, hiring_manager_email: str = None):
    with conn() as c:
        c.execute("""
            INSERT OR REPLACE INTO seen_jobs
            (id, source, company, title, location, url, posted_at, seen_at, score,
             status, resume_path, cold_email_path, hiring_manager_email)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            job["id"], job.get("source", ""), job.get("company", ""),
            job.get("title", ""), job.get("location", ""), job.get("url", ""),
            job.get("posted_at", ""), datetime.utcnow().isoformat(),
            score, "notified", resume_path, cold_email_path, hiring_manager_email
        ))


def cleanup_old(days: int = 60):
    """Drop entries older than N days to keep DB lean."""
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    with conn() as c:
        c.execute("DELETE FROM seen_jobs WHERE seen_at < ?", (cutoff,))
