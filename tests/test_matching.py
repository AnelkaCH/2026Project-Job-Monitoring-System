import concurrent.futures
import json
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from job_monitor import check_company, log_company_result, matches_filters, load_config
from db import repository
from utils.matching import keyword_matches, has_date_range_signal

FIXTURE_CONFIG = str(Path(__file__).resolve().parent / "fixtures" / "test_config.json")

FIXTURE_CONFIG = Path(__file__).parent / "fixtures" / "test_config.json"

def _job(title, location="Singapore", posted_days_ago=5):
    return {
        "id": title,
        "title": title,
        "location": location,
        "posted": "2026-08-01T00:00:00Z",
        "posted_days_ago": posted_days_ago,
        "link": f"https://jobs/{title}",
    }


def test_keyword_matches_respects_word_boundaries():
    assert keyword_matches("SR ENGINEER IT", ["sr"]) == "sr"
    assert keyword_matches("senior engineer", ["sr"]) is None
    assert keyword_matches("Engineer", ["engineer"]) == "engineer"
    assert keyword_matches("Software Engineering", ["engineer"]) is None
    print("PASS: word-boundary matching blocks partial overlaps")


def test_keyword_matches_strips_trailing_spaces():
    # Older config entries like "sr " faked a boundary with a trailing space;
    # stripping it and letting \b handle the edge must keep the same behavior.
    assert keyword_matches("SR ENGINEER", ["sr "]) == "sr "
    assert keyword_matches("SR. ENGINEER", ["sr "]) == "sr "
    assert keyword_matches("senior engineer", ["sr "]) is None
    assert keyword_matches("VP Engineering", ["vp "]) == "vp "
    assert keyword_matches("Staff Analog Design Engineer", ["staff "]) == "staff "
    print("PASS: trailing-space workaround entries still match via \\b")


def test_keyword_matches_is_case_insensitive():
    assert keyword_matches("Cyber Resilience", ["cyber"]) == "cyber"
    assert keyword_matches("CYBERSECURITY ANALYST", ["cybersecurity"]) == "cybersecurity"
    print("PASS: matching is case-insensitive")


def test_date_range_signal_paren_style():
    assert has_date_range_signal("Internship (January to June 2027)")
    assert has_date_range_signal("Placement (May 2026)")
    print("PASS: parentheses month-year ranges detected")


def test_date_range_signal_bracket_style():
    assert has_date_range_signal("Internship: Group Ops [August - December 2026]")
    print("PASS: bracket month-year ranges detected")


def test_date_range_signal_ignores_bare_years():
    assert not has_date_range_signal("2027 Internship Program")
    assert not has_date_range_signal("Strategy and Business Excellence")
    print("PASS: bare year without a month is not a date-range signal")


def test_false_positives_no_longer_match():
    # Load the repo-safe test fixture so the suite runs without config.json.
    _, filters = load_config(FIXTURE_CONFIG)
    assert matches_filters(_job("Process Support Engineer (Diffusion)"), filters) == "no_match"
    assert matches_filters(_job("Staff Digital IC Design Engineer"), filters) == "no_match"
    assert matches_filters(_job("SR ENGINEER IT"), filters) == "no_match"
    assert matches_filters(_job("IC Layout Engineer"), filters) == "no_match"
    print("PASS: generic titles without a role hit are filtered out")


def test_concurrent_workers_aggregate_safely():
    # Exercises the ThreadPoolExecutor path: N companies are fetched and
    # classified concurrently, and the main-thread aggregation folds every
    # worker's results into the shared lists and the database without loss.
    filters = {
        "locations": ["singapore"],
        "role_keywords": ["intern"],
        "domain_keywords": ["cyber"],
        "exclude_keywords": [],
        "max_age_days": 30,
    }
    companies = [{"name": f"Company{i}", "ats": "greenhouse", "slug": f"c{i}"} for i in range(20)]
    db_path = str(Path(tempfile.mkdtemp()) / "test.db")

    def fake_fetch(company):
        time.sleep(0.005)
        return [{
            "id": company["name"],
            "title": "Intern, Cyber Lab",
            "location": "Singapore",
            "posted": "",
            "posted_days_ago": 5,
            "link": "",
        }]

    all_new_jobs = []
    all_ambiguous_jobs = []
    with patch("job_monitor.CONNECTORS", {"greenhouse": fake_fetch}):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            future_to_name = {
                executor.submit(check_company, c, filters, db_path): c["name"]
                for c in companies
            }
            for future in concurrent.futures.as_completed(future_to_name):
                log_company_result(future.result(), all_new_jobs, all_ambiguous_jobs, db_path)

    assert len(all_new_jobs) == len(companies)
    assert all_ambiguous_jobs == []
    for company in companies:
        assert repository.is_job_seen(company["name"], company["name"], tier="match", db_path=db_path)
    print("PASS: concurrent company checks aggregate into shared state safely")


if __name__ == "__main__":
    test_keyword_matches_respects_word_boundaries()
    test_keyword_matches_strips_trailing_spaces()
    test_keyword_matches_is_case_insensitive()
    test_date_range_signal_paren_style()
    test_date_range_signal_bracket_style()
    test_date_range_signal_ignores_bare_years()
    test_false_positives_no_longer_match()
    test_concurrent_workers_aggregate_safely()
    print("\nAll tests passed.")