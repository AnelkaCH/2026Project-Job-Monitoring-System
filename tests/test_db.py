import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import repository
from db.schema import init_db


def _tables(path):
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        return {row[0] for row in rows}
    finally:
        conn.close()


def test_init_db_creates_tables(tmp_path):
    path = str(tmp_path / "test.db")
    init_db(path)
    assert {"jobs", "skip_streaks"} <= _tables(path)
    print("PASS: init_db creates the jobs and skip_streaks tables")


def test_init_db_is_idempotent(tmp_path):
    path = str(tmp_path / "test.db")
    init_db(path)
    init_db(path)
    assert {"jobs", "skip_streaks"} <= _tables(path)
    print("PASS: init_db can be called on every run without error")


def test_mark_job_seen_then_is_job_seen(tmp_path):
    path = str(tmp_path / "test.db")
    repository.mark_job_seen("a", "Acme", "Security Engineer", "https://x/a", "greenhouse", db_path=path)
    assert repository.is_job_seen("Acme", "a", tier="match", db_path=path)
    assert not repository.is_job_seen("Acme", "missing", tier="match", db_path=path)
    print("PASS: a marked job is seen; an unmarked one is not")


def test_tier_separation(tmp_path):
    path = str(tmp_path / "test.db")
    repository.mark_job_seen("a", "Acme", "IT Analyst", "", "greenhouse", tier="ambiguous", db_path=path)
    assert repository.is_job_seen("Acme", "a", tier="ambiguous", db_path=path)
    assert not repository.is_job_seen("Acme", "a", tier="match", db_path=path)
    print("PASS: match and ambiguous tiers dedup independently")


def test_first_seen_preserved_across_runs(tmp_path):
    path = str(tmp_path / "test.db")
    first = "2026-08-01T00:00:00+00:00"
    repository.mark_job_seen("a", "Acme", "Security Engineer", "https://x/a", "greenhouse", first_seen_at=first, db_path=path)
    later = "2026-08-11T00:00:00+00:00"
    repository.mark_job_seen("a", "Acme", "Security Engineer", "https://x/a", "greenhouse", first_seen_at=later, db_path=path)
    rows = repository.list_jobs(company="Acme", db_path=path)
    assert rows[0]["first_seen_at"] == first
    print("PASS: re-marking a seen job keeps its original first_seen_at")


def test_list_jobs_filters_by_company_and_keyword(tmp_path):
    path = str(tmp_path / "test.db")
    repository.mark_job_seen("1", "Acme", "Security Engineer", "https://x/1", "greenhouse", db_path=path)
    repository.mark_job_seen("2", "Acme", "IT Analyst", "https://x/2", "greenhouse", tier="ambiguous", db_path=path)
    repository.mark_job_seen("3", "Globex", "Security Engineer", "https://x/3", "workday", db_path=path)

    acme = repository.list_jobs(company="Acme", db_path=path)
    assert {r["job_id"] for r in acme} == {"1", "2"}

    keyword = repository.list_jobs(keyword="analyst", db_path=path)
    assert [r["job_id"] for r in keyword] == ["2"]

    both = repository.list_jobs(company="Acme", keyword="security", db_path=path)
    assert [r["job_id"] for r in both] == ["1"]

    assert len(repository.list_jobs(db_path=path)) == 3
    print("PASS: list_jobs filters by company and title keyword")


def test_record_skip_increments_and_get_skip_streak(tmp_path):
    path = str(tmp_path / "test.db")
    assert repository.record_skip("Acme", db_path=path) == 1
    assert repository.record_skip("Acme", db_path=path) == 2
    assert repository.get_skip_streak("Acme", db_path=path) == 2
    assert repository.get_skip_streak("Globex", db_path=path) == 0
    print("PASS: consecutive skips increment the streak")


def test_reset_skip_streak(tmp_path):
    path = str(tmp_path / "test.db")
    repository.record_skip("Acme", db_path=path)
    repository.record_skip("Acme", db_path=path)
    repository.reset_skip_streak("Acme", db_path=path)
    assert repository.get_skip_streak("Acme", db_path=path) == 0
    print("PASS: a successful cycle clears the skip streak")


def test_list_skip_streaks(tmp_path):
    path = str(tmp_path / "test.db")
    repository.record_skip("Acme", db_path=path)
    repository.record_skip("Acme", db_path=path)
    repository.record_skip("Globex", db_path=path)
    assert repository.list_skip_streaks(db_path=path) == {"Acme": 2, "Globex": 1}
    print("PASS: list_skip_streaks aggregates per-company counts")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))