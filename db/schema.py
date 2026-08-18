import sqlite3


def init_db(path):
    # Creates the jobs and skip_streaks tables if they do not exist yet.
    # Idempotent and cheap, so it is safe to call at the start of every run
    # and before any repository read or write.
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                company TEXT NOT NULL,
                job_id TEXT NOT NULL,
                tier TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                location TEXT NOT NULL DEFAULT '',
                posted TEXT NOT NULL DEFAULT '',
                posted_days_ago INTEGER,
                url TEXT NOT NULL DEFAULT '',
                ats_platform TEXT NOT NULL DEFAULT '',
                first_seen_at TEXT NOT NULL,
                PRIMARY KEY (company, job_id, tier)
            );
            CREATE INDEX IF NOT EXISTS idx_jobs_company ON jobs(company);
            CREATE INDEX IF NOT EXISTS idx_jobs_title ON jobs(title);

            CREATE TABLE IF NOT EXISTS skip_streaks (
                company TEXT PRIMARY KEY,
                streak INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        conn.commit()
    finally:
        conn.close()