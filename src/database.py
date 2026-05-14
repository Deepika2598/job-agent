"""SQLite layer: dedup tracking + Hunter.io cache + daily-summary support."""
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime, timedelta, timezone

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
                hiring_manager_email TEXT,
                salary TEXT
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_seen_at ON seen_jobs(seen_at)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_status ON seen_jobs(status)")

        # Hunter.io cache so we don't burn lookups on the same company twice
        c.execute("""
            CREATE TABLE IF NOT EXISTS hiring_manager_cache (
                domain TEXT PRIMARY KEY,
                email TEXT,
                name TEXT,
                title TEXT,
                confidence TEXT,
                cached_at TEXT NOT NULL
            )
        """)

        # Defensive: add the `salary` column if upgrading from an older DB
        cols = [r[1] for r in c.execute("PRAGMA table_info(seen_jobs)").fetchall()]
        if "salary" not in cols:
            c.execute("ALTER TABLE seen_jobs ADD COLUMN salary TEXT")


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
              cold_email_path: str = None, hiring_manager_email: str = None,
              salary: str = None):
    with conn() as c:
        c.execute("""
            INSERT OR REPLACE INTO seen_jobs
            (id, source, company, title, location, url, posted_at, seen_at, score,
             status, resume_path, cold_email_path, hiring_manager_email, salary)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            job["id"], job.get("source", ""), job.get("company", ""),
            job.get("title", ""), job.get("location", ""), job.get("url", ""),
            job.get("posted_at", ""), datetime.utcnow().isoformat(),
            score, "notified", resume_path, cold_email_path,
            hiring_manager_email, salary
        ))


def cleanup_old(days: int = 60):
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    with conn() as c:
        c.execute("DELETE FROM seen_jobs WHERE seen_at < ?", (cutoff,))
        # Refresh hiring-manager cache every 90 days — emails change
        cache_cutoff = (datetime.utcnow() - timedelta(days=90)).isoformat()
        c.execute("DELETE FROM hiring_manager_cache WHERE cached_at < ?",
                  (cache_cutoff,))


# ---------- Hiring-manager cache ----------

def get_cached_hm(domain: str) -> dict | None:
    """Look up a cached Hunter.io result for a company domain."""
    if not domain:
        return None
    with conn() as c:
        r = c.execute(
            "SELECT email, name, title, confidence FROM hiring_manager_cache "
            "WHERE domain = ?", (domain,)
        ).fetchone()
        if r:
            return {"email": r["email"], "name": r["name"],
                    "title": r["title"], "confidence": r["confidence"]}
        return None


def cache_hm(domain: str, hm: dict | None):
    if not domain or not hm:
        return
    with conn() as c:
        c.execute("""
            INSERT OR REPLACE INTO hiring_manager_cache
            (domain, email, name, title, confidence, cached_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            domain, hm.get("email"), hm.get("name"),
            hm.get("title"), hm.get("confidence"),
            datetime.utcnow().isoformat()
        ))


# ---------- Daily summary support ----------

def jobs_in_window(hours: int = 24) -> list[dict]:
    """Return all jobs seen in the last N hours, newest first."""
    cutoff = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
    with conn() as c:
        rows = c.execute("""
            SELECT company, title, location, url, score, salary,
                   hiring_manager_email, seen_at
            FROM seen_jobs
            WHERE seen_at >= ?
            ORDER BY score DESC, seen_at DESC
        """, (cutoff,)).fetchall()
        return [dict(r) for r in rows]
