import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from db.schema import init_db

# Load .env here so every consumer (job_monitor, dashboard, adapters) resolves
# DB_PATH identically, regardless of whether another module already called
# load_dotenv.
load_dotenv()

# Anchored to this file's own location, not the working directory, so it lands
# in the repo's data/ folder regardless of where the script is invoked from.
DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "jobmonitor.db"


def get_db_path():
    # DB_PATH env var wins (see .env.example); otherwise fall back to the
    # repo-relative data/jobmonitor.db.
    env_path = os.environ.get("DB_PATH")
    if env_path:
        return Path(env_path)
    return DEFAULT_DB_PATH


def _connect(db_path=None):
    # Every call opens its own short-lived connection so concurrent workers
    # (job_monitor runs adapters via ThreadPoolExecutor) never share one.
    # init_db keeps the tables present even if an adapter's SkipTracker
    # writes before job_monitor.main() has run.
    path = str(db_path or get_db_path())
    init_db(path)
    return sqlite3.connect(path, timeout=5.0)


def mark_job_seen(
    job_id,
    company,
    title,
    url,
    ats_platform,
    tier="match",
    location="",
    posted="",
    posted_days_ago=None,
    first_seen_at=None,
    db_path=None,
):
    # Records one seen posting. The primary key is (company, job_id, tier),
    # so INSERT OR IGNORE preserves the original first_seen_at while the
    # UPDATE refreshes the mutable detail fields.
    if first_seen_at is None:
        first_seen_at = datetime.now(timezone.utc).isoformat()
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO jobs "
            "(company, job_id, tier, title, location, posted, posted_days_ago, url, ats_platform, first_seen_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (company, job_id, tier, title, location, posted, posted_days_ago, url, ats_platform, first_seen_at),
        )
        conn.execute(
            "UPDATE jobs SET title = ?, location = ?, posted = ?, posted_days_ago = ?, url = ?, ats_platform = ? "
            "WHERE company = ? AND job_id = ? AND tier = ?",
            (title, location, posted, posted_days_ago, url, ats_platform, company, job_id, tier),
        )
        conn.commit()
    finally:
        conn.close()


def is_job_seen(company, job_id, tier="match", db_path=None):
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT 1 FROM jobs WHERE company = ? AND job_id = ? AND tier = ?",
            (company, job_id, tier),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def list_jobs(company=None, keyword=None, db_path=None):
    # Returns one dict per stored posting. keyword matches against the title.
    conn = _connect(db_path)
    try:
        query = (
            "SELECT company, job_id, tier, title, location, posted, posted_days_ago, "
            "url, ats_platform, first_seen_at FROM jobs"
        )
        clauses = []
        params = []
        if company:
            clauses.append("company = ?")
            params.append(company)
        if keyword:
            clauses.append("title LIKE ?")
            params.append(f"%{keyword}%")
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY first_seen_at DESC"
        conn.row_factory = sqlite3.Row
        rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def record_skip(company, db_path=None):
    # Increments the consecutive-skip counter for one company and returns
    # the new streak.
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO skip_streaks (company, streak) VALUES (?, 1) "
            "ON CONFLICT(company) DO UPDATE SET streak = streak + 1",
            (company,),
        )
        conn.commit()
        row = conn.execute(
            "SELECT streak FROM skip_streaks WHERE company = ?", (company,)
        ).fetchone()
        return row[0]
    finally:
        conn.close()


def get_skip_streak(company, db_path=None):
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT streak FROM skip_streaks WHERE company = ?", (company,)
        ).fetchone()
        return row[0] if row else 0
    finally:
        conn.close()


def reset_skip_streak(company, db_path=None):
    # Called when a company completes a cycle successfully; the streak
    # counter row is removed entirely, matching the old JSON behavior.
    conn = _connect(db_path)
    try:
        conn.execute("DELETE FROM skip_streaks WHERE company = ?", (company,))
        conn.commit()
    finally:
        conn.close()


def list_skip_streaks(db_path=None):
    conn = _connect(db_path)
    try:
        rows = conn.execute("SELECT company, streak FROM skip_streaks").fetchall()
        return {company: streak for company, streak in rows}
    finally:
        conn.close()