import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from job_monitor import build_company_record


def _job(job_id, title="Security Engineer"):
    return {
        "id": job_id,
        "title": title,
        "location": "Singapore",
        "posted": "2026-08-01T00:00:00Z",
        "posted_days_ago": 5,
        "link": f"https://jobs/{job_id}",
    }


def test_new_record_gets_first_seen_now():
    company = {"name": "Acme", "ats": "greenhouse"}
    now = "2026-08-11T12:00:00+00:00"
    record = build_company_record(company, [_job("a")], [], {}, now_iso=now)
    assert record["matched_ids"] == ["a"]
    assert record["details"]["a"]["first_seen"] == now
    print("PASS: new posting stamped with now as first_seen")


def test_first_seen_preserved_across_runs():
    company = {"name": "Acme", "ats": "greenhouse"}
    first_run = build_company_record(company, [_job("a")], [], {}, now_iso="2026-08-01T00:00:00+00:00")
    second_run = build_company_record(company, [_job("a")], [], first_run, now_iso="2026-08-11T00:00:00+00:00")
    assert second_run["details"]["a"]["first_seen"] == "2026-08-01T00:00:00+00:00"
    print("PASS: existing posting keeps its original first_seen")


def test_new_id_in_second_run_gets_later_timestamp():
    company = {"name": "Acme", "ats": "greenhouse"}
    first_run = build_company_record(company, [_job("a")], [], {}, now_iso="2026-08-01T00:00:00+00:00")
    second_run = build_company_record(company, [_job("a"), _job("b")], [], first_run, now_iso="2026-08-11T00:00:00+00:00")
    assert second_run["details"]["a"]["first_seen"] == "2026-08-01T00:00:00+00:00"
    assert second_run["details"]["b"]["first_seen"] == "2026-08-11T00:00:00+00:00"
    print("PASS: brand-new posting in a later run gets the later timestamp")


def test_ambiguous_ids_tracked_separately():
    company = {"name": "Acme", "ats": "greenhouse"}
    record = build_company_record(company, [], [_job("x")], {}, now_iso="2026-08-11T00:00:00+00:00")
    assert record["ambiguous_ids"] == ["x"]
    assert record["matched_ids"] == []
    assert record["details"]["x"]["ats"] == "greenhouse"
    print("PASS: ambiguous postings tracked separately from matches")


def test_ats_stamped_from_company():
    company = {"name": "Acme", "ats": "workday"}
    record = build_company_record(company, [_job("a")], [], {}, now_iso="now")
    assert record["details"]["a"]["ats"] == "workday"
    print("PASS: ats platform stamped into the detail record")


def test_pre_v3_0_record_backfilled():
    # Old-format previous record has no details at all - ids still in the
    # current feed must not be re-reported and get a fresh first_seen.
    company = {"name": "Acme", "ats": "workday"}
    previous = {"matched_ids": ["a"], "ambiguous_ids": []}
    record = build_company_record(company, [_job("a")], [], previous, now_iso="2026-08-11T00:00:00+00:00")
    assert record["details"]["a"]["first_seen"] == "2026-08-11T00:00:00+00:00"
    assert record["matched_ids"] == ["a"]
    print("PASS: pre-v3.0 record backfilled with a first_seen date")


if __name__ == "__main__":
    test_new_record_gets_first_seen_now()
    test_first_seen_preserved_across_runs()
    test_new_id_in_second_run_gets_later_timestamp()
    test_ambiguous_ids_tracked_separately()
    test_ats_stamped_from_company()
    test_pre_v3_0_record_backfilled()
    print("\nAll tests passed.")
