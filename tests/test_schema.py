import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from adapters.custom_handler_example import ExampleCompanyJob
from utils.schema import (
    ALLOWED_LINK_DOMAINS,
    AshbyJob,
    GreenhouseJob,
    LeverJob,
    PersonioJob,
    RecruiteeJob,
    SapPayload,
    SmartRecruitersJob,
    WorkableJob,
    WorkdayJob,
    is_valid_job_url,
    validate_job_posting,
    validate_raw_jobs,
)


# One realistic raw response item per ATS, matching the fields each
# adapter actually reads, plus a description-ish HTML field.
RAW_SAMPLES = {
    "greenhouse": {
        "id": 123,
        "title": "Security Engineer",
        "location": {"name": "Singapore"},
        "updated_at": "2026-08-01T00:00:00Z",
        "absolute_url": "https://boards.greenhouse.io/acme/jobs/123",
        "content": "<p>Join the <b>SOC</b> team</p>",
    },
    "lever": {
        "id": "job-uuid",
        "text": "GRC Analyst",
        "categories": {"location": "Singapore"},
        "createdAt": 1754000000000,
        "hostedUrl": "https://jobs.lever.co/acme/job-uuid",
        "description": "<b>GRC</b> role",
        "descriptionPlain": "GRC role",
    },
    "ashby": {
        "id": "ashby-id",
        "title": "Network Engineer",
        "location": "Singapore",
        "publishedAt": "2026-08-01T00:00:00Z",
        "jobUrl": "https://jobs.ashbyhq.com/acme/ashby-id",
        "descriptionHtml": "<p>Network <i>security</i></p>",
    },
    "smartrecruiters": {
        "id": "sr1",
        "name": "Security Analyst",
        "location": {"city": "Singapore", "region": "", "country": "Singapore"},
        "releasedDate": "2026-08-01T00:00:00Z",
        "ref": "https://jobs.smartrecruiters.com/NCS3/743999998610123",
    },
    "recruitee": {
        "id": 5,
        "title": "SOC Analyst",
        "city": "Singapore",
        "created_at": "2026-08-01T00:00:00Z",
        "careers_url": "https://acme.recruitee.com/o/soc-analyst",
        "content": "<p>Shift work</p>",
    },
    "workable": {
        "id": "job-abc",
        "title": "DevOps Engineer",
        "location": {"city": "Singapore", "region": "", "country": "Singapore"},
        "published": "2026-08-01T00:00:00Z",
        "shortcode": "abc123",
        "description": "<p>Cloud <b>infra</b></p>",
    },
    "personio": {
        "id": 77,
        "name": "IT Analyst",
        "office": "Singapore",
        "jobDescriptions": [
            {"plain": "Plain text", "richText": "<p>Raw <b>HTML</b></p>"}
        ],
    },
    "workday": {
        "externalPath": "/wday/cxs/ensigninfosecurity/Ensign_Careers/job/123",
        "title": "Cloud Security Engineer",
        "locationsText": "Singapore",
        "postedOn": "Posted 2 Days Ago",
    },
    "sap": {
        "response": {
            "id": 42,
            "unifiedStandardTitle": "Cloud Engineer",
            "title": None,
            "jobLocationCountry": ["Singapore"],
            "urlTitle": "cloud-engineer",
            "unifiedUrlTitle": "",
            "unifiedStandardStart": "01/08/2026",
        }
    },
}

# (model, ats, required-id-key) per ATS for the rejection sweep.
MODEL_CASES = [
    (GreenhouseJob, "greenhouse", "id"),
    (LeverJob, "lever", "id"),
    (AshbyJob, "ashby", "id"),
    (SmartRecruitersJob, "smartrecruiters", "id"),
    (RecruiteeJob, "recruitee", "id"),
    (WorkableJob, "workable", "id"),
    (PersonioJob, "personio", "id"),
    (WorkdayJob, "workday", "externalPath"),
    (SapPayload, "sap", "id"),
]

REQUIRED_TEXT_KEY = {
    "greenhouse": "title",
    "lever": "text",
    "ashby": "title",
    "smartrecruiters": "name",
    "recruitee": "title",
    "workable": "title",
    "personio": "name",
    "workday": "title",
}


def _audit_events(mock):
    return [
        (c.args[0], c.kwargs) for c in mock.call_args_list
    ]


@pytest.mark.parametrize(
    "model,ats,_", MODEL_CASES, ids=[c[1] for c in MODEL_CASES]
)
def test_raw_model_accepts_valid_response(model, ats, _):
    sample = dict(RAW_SAMPLES[ats])
    with patch("utils.schema.log_audit_event") as mock_log:
        result = validate_raw_jobs([sample], model, ats, "Acme")
    assert len(result) == 1
    assert not mock_log.called
    print(f"PASS: {ats} valid raw item accepted")


@pytest.mark.parametrize(
    "model,ats,id_key", MODEL_CASES, ids=[c[1] for c in MODEL_CASES]
)
def test_raw_model_rejects_missing_required_field(model, ats, id_key):
    sample = dict(RAW_SAMPLES[ats])
    if ats == "sap":
        # SAP wraps the job under "response"; malform the nested job itself.
        sample["response"] = dict(sample["response"])
        sample["response"].pop(id_key)
    else:
        sample.pop(id_key)
    with patch("utils.schema.log_audit_event") as mock_log:
        result = validate_raw_jobs([sample], model, ats, "Acme")
    assert result == []
    events = _audit_events(mock_log)
    assert any(
        event == "VALIDATION_REJECTED" and kwargs.get("reason") == "response_shape"
        for event, kwargs in events
    )
    assert any(kwargs.get("ats") == ats and kwargs.get("company") == "Acme" for _, kwargs in events)
    print(f"PASS: {ats} item missing {id_key} dropped and audited")


@pytest.mark.parametrize(
    "model,ats,_", MODEL_CASES, ids=[c[1] for c in MODEL_CASES]
)
def test_raw_model_rejects_missing_title(model, ats, _):
    text_key = REQUIRED_TEXT_KEY.get(ats)
    if text_key is None:
        pytest.skip("sap title is optional by design")
    sample = dict(RAW_SAMPLES[ats])
    sample.pop(text_key)
    with patch("utils.schema.log_audit_event") as mock_log:
        result = validate_raw_jobs([sample], model, ats, "Acme")
    assert result == []
    events = _audit_events(mock_log)
    assert any(
        event == "VALIDATION_REJECTED" and kwargs.get("reason") == "response_shape"
        for event, kwargs in events
    )
    print(f"PASS: {ats} item missing title dropped and audited")


def test_html_stripped_from_raw_description_fields():
    # Greenhouse content, Ashby descriptionHtml, Recruitee content, Workable
    # description, Lever description, Personio richText are all cleaned.
    with patch("utils.schema.log_audit_event"):
        gh = validate_raw_jobs([RAW_SAMPLES["greenhouse"]], GreenhouseJob, "greenhouse", "Acme")
        ashby = validate_raw_jobs([RAW_SAMPLES["ashby"]], AshbyJob, "ashby", "Acme")
        rec = validate_raw_jobs([RAW_SAMPLES["recruitee"]], RecruiteeJob, "recruitee", "Acme")
        workable = validate_raw_jobs([RAW_SAMPLES["workable"]], WorkableJob, "workable", "Acme")
        lever = validate_raw_jobs([RAW_SAMPLES["lever"]], LeverJob, "lever", "Acme")
        personio = validate_raw_jobs([RAW_SAMPLES["personio"]], PersonioJob, "personio", "Acme")

    assert gh[0]["content"] == "Join the SOC team"
    assert gh[0]["title"] == "Security Engineer"
    assert ashby[0]["descriptionHtml"] == "Network security"
    assert rec[0]["content"] == "Shift work"
    assert workable[0]["description"] == "Cloud infra"
    assert lever[0]["description"] == "GRC role"
    assert personio[0]["jobDescriptions"][0]["richText"] == "Raw HTML"
    print("PASS: raw HTML-bearing fields are tag-stripped at response time")


def test_local_handler_model_validates_with_own_domain():
    # Custom handlers define their response model locally (no shared schema
    # entry) and supply their own allowed domain, mirroring the template.
    sample = {
        "requisitionId": "R1234",
        "title": "Security Consultant",
        "location": "Singapore",
        "postedDateText": "",
        "updateDate": "2026-08-01T00:00:00Z",
        "jobDetailUrl": "https://www.example.com/jobs/R1234",
    }
    with patch("utils.schema.log_audit_event") as mock_log:
        validated = validate_raw_jobs([sample], ExampleCompanyJob, "example_company", "Acme")
    assert len(validated) == 1
    posting = validate_job_posting({
        "id": validated[0]["requisitionId"],
        "title": validated[0]["title"],
        "location": validated[0]["location"],
        "posted": validated[0]["postedDateText"],
        "posted_days_ago": None,
        "link": validated[0]["jobDetailUrl"],
    }, ats="example_company", company="Acme", allowed_domains=("www.example.com",))
    assert posting is not None
    assert not mock_log.called
    print("PASS: locally-defined handler model validated with its own domain")


def test_job_posting_sanitizes_stored_text():
    with patch("utils.schema.log_audit_event"):
        posting = validate_job_posting({
            "id": "1",
            "title": "<b>Security</b> <script>alert(1)</script>Engineer",
            "location": "<i>Singapore</i>",
            "posted": "",
            "posted_days_ago": 2,
            "link": "https://boards.greenhouse.io/acme/jobs/1",
        }, ats="greenhouse", company="Acme")
    # bleach strips tags and keeps the text, so no angle brackets survive
    # into storage; the script body is inert plain text afterwards.
    assert "<" not in posting["title"]
    assert posting["title"] == "Security alert(1)Engineer"
    assert posting["location"] == "Singapore"
    print("PASS: stored title and location are tag-stripped")


def test_valid_https_expected_domain_accepted():
    with patch("utils.schema.log_audit_event"):
        posting = validate_job_posting({
            "id": "1", "title": "Analyst", "location": "SG",
            "posted": "", "posted_days_ago": None,
            "link": "https://boards.greenhouse.io/acme/jobs/1",
        }, ats="greenhouse", company="Acme")
    assert posting is not None
    print("PASS: https link on the expected domain accepted")


@pytest.mark.parametrize("bad_link", [
    "http://boards.greenhouse.io/acme/jobs/1",
    "javascript:alert(1)",
    "https://evil.example.com/acme/jobs/1",
    "https://boards.greenhouse.io.evil.example.com/x",
])
def test_job_posting_rejects_bad_url(bad_link):
    with patch("utils.schema.log_audit_event") as mock_log:
        posting = validate_job_posting({
            "id": "1", "title": "Analyst", "location": "SG",
            "posted": "", "posted_days_ago": None,
            "link": bad_link,
        }, ats="greenhouse", company="Acme")
    assert posting is None
    events = _audit_events(mock_log)
    assert any(
        event == "VALIDATION_REJECTED" and kwargs.get("reason") == "url_rejected"
        for event, kwargs in events
    )
    print(f"PASS: url {bad_link!r} rejected and audited")


def test_subdomain_suffix_match_for_workday():
    with patch("utils.schema.log_audit_event"):
        posting = validate_job_posting({
            "id": "/wday/cxs/x", "title": "Analyst", "location": "SG",
            "posted": "Posted Today", "posted_days_ago": 0,
            "link": "https://visa.wd5.myworkdayjobs.com/Visa/jobs/x",
        }, ats="workday", company="Visa")
    assert posting is not None
    print("PASS: *.myworkdayjobs.com subdomain accepted for workday")


def test_empty_link_allowed():
    with patch("utils.schema.log_audit_event"):
        posting = validate_job_posting({
            "id": "1", "title": "Analyst", "location": "SG",
            "posted": "", "posted_days_ago": None,
            "link": "",
        }, ats="greenhouse", company="Acme")
    assert posting is not None
    print("PASS: empty link still allowed (pre-v3.1 behavior)")


def test_sap_uses_config_derived_expected_domain():
    sample = dict(RAW_SAMPLES["sap"])
    with patch("utils.schema.log_audit_event") as mock_log:
        validated = validate_raw_jobs([sample], SapPayload, "sap", "Acme")
    job = validated[0].get("response") or {}
    posting = validate_job_posting({
        "id": str(job.get("id", "")),
        "title": job.get("unifiedStandardTitle") or job.get("title") or "Untitled",
        "location": ", ".join(job.get("jobLocationCountry", [])),
        "posted": job.get("unifiedStandardStart", ""),
        "posted_days_ago": None,
        "link": "https://careers.acme.com/job/cloud-engineer",
    }, ats="sap", company="Acme", allowed_domains=("careers.acme.com",))
    assert posting is not None
    assert posting["title"] == "Cloud Engineer"
    assert not mock_log.called
    print("PASS: SAP link validated against config-derived domain")


def test_sap_wrong_domain_rejected():
    with patch("utils.schema.log_audit_event") as mock_log:
        posting = validate_job_posting({
            "id": "42", "title": "Cloud Engineer", "location": "Singapore",
            "posted": "01/08/2026", "posted_days_ago": 3,
            "link": "https://evil.example.com/job/cloud-engineer",
        }, ats="sap", company="Acme", allowed_domains=("careers.acme.com",))
    assert posting is None
    events = _audit_events(mock_log)
    assert any(kwargs.get("reason") == "url_rejected" for _, kwargs in events)
    print("PASS: SAP link on a foreign domain rejected")


def test_job_posting_rejects_schema_violation():
    with patch("utils.schema.log_audit_event") as mock_log:
        posting = validate_job_posting({
            "title": "Analyst", "location": "SG",
            "posted": "", "posted_days_ago": None,
            "link": "",
        }, ats="greenhouse", company="Acme")  # id missing
    assert posting is None
    events = _audit_events(mock_log)
    assert any(
        event == "VALIDATION_REJECTED" and kwargs.get("reason") == "schema_violation"
        for event, kwargs in events
    )
    print("PASS: normalized dict missing required field dropped and audited")


def test_job_posting_rejects_non_string_location():
    # A None location (e.g. a Lever posting with no categories.location)
    # must be dropped instead of crashing the filter stage later.
    with patch("utils.schema.log_audit_event") as mock_log:
        posting = validate_job_posting({
            "id": "1", "title": "Analyst", "location": None,
            "posted": "", "posted_days_ago": None,
            "link": "",
        }, ats="lever", company="Acme")
    assert posting is None
    events = _audit_events(mock_log)
    assert any(kwargs.get("reason") == "schema_violation" for _, kwargs in events)
    print("PASS: None location rejected at the normalized gate")


class TestIsValidJobUrl:
    def test_https_exact_match(self):
        assert is_valid_job_url("https://jobs.lever.co/acme/1", ("jobs.lever.co",))

    def test_https_subdomain_suffix(self):
        assert is_valid_job_url(
            "https://visa.wd5.myworkdayjobs.com/Visa/jobs/1", (".myworkdayjobs.com",)
        )

    def test_bare_domain_does_not_match_suffix(self):
        assert not is_valid_job_url("https://myworkdayjobs.com/Visa/jobs/1", (".myworkdayjobs.com",))

    def test_http_scheme_rejected(self):
        assert not is_valid_job_url("http://boards.greenhouse.io/a/1", ("boards.greenhouse.io",))

    def test_foreign_domain_rejected(self):
        assert not is_valid_job_url("https://evil.example.com/a/1", ("boards.greenhouse.io",))

    def test_lookalike_suffix_rejected(self):
        assert not is_valid_job_url(
            "https://boards.greenhouse.io.evil.com/a/1", ("boards.greenhouse.io",)
        )

    def test_javascript_scheme_rejected(self):
        assert not is_valid_job_url("javascript:alert(1)", ("boards.greenhouse.io",))


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
