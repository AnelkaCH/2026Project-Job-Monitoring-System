import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api import main as api_main
from api.routes_companies import CONFIG_FILE
from db import repository


@pytest.fixture
def api_config(tmp_path, monkeypatch):
    # Points the API at a temp config so tests never touch the gitignored
    # real config.json. Returns the path for direct file assertions.
    cfg = {
        "filters": {
            "locations": ["singapore"],
            "max_age_days": 30,
            "role_keywords": ["intern"],
            "domain_keywords": ["cyber"],
            "exclude_keywords": [],
        },
        "companies": [
            {"name": "Acme", "ats": "greenhouse", "slug": "acme"},
            {"name": "Globex", "ats": "workday", "workday_url": "https://x"},
        ],
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setattr("api.routes_companies.CONFIG_FILE", str(path))
    return str(path)


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    # DB_PATH env var is how repository.get_db_path() resolves the store, so
    # pointing it at a temp file keeps the API tests fully isolated.
    path = str(tmp_path / "api_test.db")
    monkeypatch.setenv("DB_PATH", path)
    return path


@pytest.fixture
def client():
    return TestClient(api_main.app)


def _seed(db_path, company="Acme", job_id="a", title="Security Engineer Intern", tier="match"):
    day = (int(job_id) + 1) if job_id.isdigit() else 1
    repository.mark_job_seen(
        job_id=job_id,
        company=company,
        title=title,
        url=f"https://jobs/{job_id}",
        ats_platform="greenhouse",
        tier=tier,
        first_seen_at=f"2026-08-{day:02d}T00:00:00+00:00",
        db_path=db_path,
    )


def test_list_jobs_empty(db_path, client):
    resp = client.get("/jobs")
    assert resp.status_code == 200
    assert resp.json() == []
    print("PASS: /jobs returns an empty list when nothing is stored")


def test_list_jobs_filters_by_company_and_keyword(db_path, client):
    _seed(db_path, company="Acme", job_id="1", title="Security Engineer Intern")
    _seed(db_path, company="Acme", job_id="2", title="IT Analyst", tier="ambiguous")
    _seed(db_path, company="Globex", job_id="3", title="Security Engineer Intern")

    by_company = client.get("/jobs", params={"company": "Acme"}).json()
    assert {r["job_id"] for r in by_company} == {"1", "2"}

    by_keyword = client.get("/jobs", params={"keyword": "analyst"}).json()
    assert [r["job_id"] for r in by_keyword] == ["2"]

    both = client.get("/jobs", params={"company": "Acme", "keyword": "security"}).json()
    assert [r["job_id"] for r in both] == ["1"]
    print("PASS: /jobs filters by company and title keyword")


def test_get_job_found(db_path, client):
    _seed(db_path, job_id="9")
    resp = client.get("/jobs/9")
    assert resp.status_code == 200
    body = resp.json()
    assert body["job_id"] == "9"
    assert body["company"] == "Acme"
    assert body["tier"] == "match"
    assert body["url"] == "https://jobs/9"
    print("PASS: /jobs/{job_id} returns a single stored posting")


def test_get_job_not_found(db_path, client):
    resp = client.get("/jobs/missing")
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"]
    print("PASS: /jobs/{job_id} returns 404 with a clear message")


def test_list_companies_status(db_path, api_config, client):
    _seed(db_path, job_id="1")
    repository.record_skip("Acme", db_path=db_path)
    repository.record_skip("Acme", db_path=db_path)

    resp = client.get("/companies")
    assert resp.status_code == 200
    by_name = {c["name"]: c for c in resp.json()}
    assert by_name["Acme"]["ats"] == "greenhouse"
    assert by_name["Acme"]["skip_streak"] == 2
    assert by_name["Acme"]["last_checked"] == "2026-08-02T00:00:00+00:00"
    assert by_name["Globex"]["skip_streak"] == 0
    assert by_name["Globex"]["last_checked"] is None
    print("PASS: /companies merges config list with skip streak and last checked")


def test_get_company_found(db_path, api_config, client):
    resp = client.get("/companies/Acme")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Acme"
    assert body["ats"] == "greenhouse"
    print("PASS: /companies/{name} returns a tracked company status")


def test_get_company_not_tracked(api_config, client):
    resp = client.get("/companies/NopeCorp")
    assert resp.status_code == 404
    assert "not tracked" in resp.json()["detail"]
    print("PASS: /companies/{name} returns 404 for an untracked company")


def test_update_keywords_writes_all_lists(api_config, client):
    resp = client.post("/config/keywords", json={"keywords": ["intern", "grad"]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["role_keywords"] == ["intern", "grad"]
    assert body["domain_keywords"] == ["intern", "grad"]
    assert body["exclude_keywords"] == ["intern", "grad"]

    with open(api_config, "r", encoding="utf-8") as f:
        saved = json.load(f)
    assert saved["filters"]["role_keywords"] == ["intern", "grad"]
    assert saved["filters"]["domain_keywords"] == ["intern", "grad"]
    assert saved["filters"]["exclude_keywords"] == ["intern", "grad"]
    print("PASS: POST /config/keywords persists the keywords to all three lists")


def test_update_keywords_empty_list_rejected(api_config, client):
    resp = client.post("/config/keywords", json={"keywords": []})
    assert resp.status_code == 422
    print("PASS: an empty keyword list is rejected with 422")


def test_update_keywords_non_string_rejected(api_config, client):
    resp = client.post("/config/keywords", json={"keywords": ["intern", 42]})
    assert resp.status_code == 422
    print("PASS: non-string keyword entries are rejected with 422")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
