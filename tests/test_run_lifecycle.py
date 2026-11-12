# Tests for the v4.0 scheduled-run lifecycle in job_monitor.main():
# RUN_START / RUN_END bracketing with a run id, NOTIFY_FAILED handling, exit
# codes, and the guarantee that no credential value ever reaches a log file
# (the logs are committed to the private repo's state branch).

import json
import smtplib
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import job_monitor
from utils.audit_log import REDACTED, redact_secrets

FAKE_EMAIL = "fake.sender@example.com"
FAKE_PASSWORD = "fakeapppassword1234"  # pragma: allowlist secret (fake value for the redaction test)
FAKE_RECIPIENT = "fake.recipient@example.com"


def _write_config(tmp_path, companies):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"filters": {}, "companies": companies}), encoding="utf-8")
    return str(path)


def _audit_events(log_dir):
    lines = (log_dir / "audit.log").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


@pytest.fixture
def fake_credentials(monkeypatch):
    monkeypatch.setenv("EMAIL_ADDRESS", FAKE_EMAIL)
    monkeypatch.setenv("EMAIL_APP_PASSWORD", FAKE_PASSWORD)
    monkeypatch.setenv("RECIPIENT_EMAIL", FAKE_RECIPIENT)


def test_run_start_and_end_share_a_run_id(isolated_logs, tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_RUN_ID", raising=False)
    config = _write_config(tmp_path, [{"name": "Acme", "ats": "greenhouse", "slug": "acme"}])

    with patch("job_monitor.CONNECTORS", {"greenhouse": lambda company: []}), \
         patch("job_monitor.send_notification", lambda *a, **k: None):
        exit_code = job_monitor.main(["--config", config, "--db-path", str(tmp_path / "t.db")])

    assert exit_code == 0
    events = _audit_events(isolated_logs)
    names = [e["event"] for e in events]
    assert names[0] == "RUN_START"
    assert names[-1] == "RUN_END"
    run_ids = {e.get("run_id") for e in events}
    assert len(run_ids) == 1
    assert next(iter(run_ids)).startswith("local-")
    end = events[-1]
    assert end["status"] == "ok"
    assert end["companies_checked"] == 1
    assert "duration_seconds" in end
    print("PASS: every event in a run carries the same run id, bracketed by RUN_START/RUN_END")


def test_github_run_id_is_reused(monkeypatch):
    monkeypatch.setenv("GITHUB_RUN_ID", "987654")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    assert job_monitor.new_run_id() == "gh-987654-2"
    print("PASS: inside Actions the run id links back to the workflow run")


def test_notify_failure_is_audited_and_exits_nonzero(isolated_logs, tmp_path):
    config = _write_config(tmp_path, [{"name": "Acme", "ats": "greenhouse", "slug": "acme"}])

    def broken_email(*args, **kwargs):
        raise smtplib.SMTPException("connection dropped")

    with patch("job_monitor.CONNECTORS", {"greenhouse": lambda company: []}), \
         patch("job_monitor.send_notification", broken_email):
        exit_code = job_monitor.main(["--config", config, "--db-path", str(tmp_path / "t.db")])

    assert exit_code == 1
    events = _audit_events(isolated_logs)
    names = [e["event"] for e in events]
    assert "NOTIFY_FAILED" in names
    assert events[-1]["event"] == "RUN_END"
    assert events[-1]["status"] == "notify_failed"
    print("PASS: an email failure is audited, still ends the run cleanly, and exits 1")


def test_unexpected_crash_is_audited_and_reraised(isolated_logs, tmp_path):
    missing_config = str(tmp_path / "does_not_exist.json")
    with pytest.raises(FileNotFoundError):
        job_monitor.main(["--config", missing_config])

    names = [e["event"] for e in _audit_events(isolated_logs)]
    assert names == ["RUN_START", "RUN_FAILED", "RUN_END"]
    assert _audit_events(isolated_logs)[-1]["status"] == "failed"
    print("PASS: a crash is audited as RUN_FAILED and still closes with RUN_END")


def test_no_credential_reaches_any_log_file(isolated_logs, tmp_path, fake_credentials):
    # Worst case on purpose: both an adapter crash and an SMTP failure carry
    # the credential values in their exception text.
    config = _write_config(tmp_path, [{"name": "Acme", "ats": "greenhouse", "slug": "acme"}])

    def leaky_fetch(company):
        raise RuntimeError(f"login failed for {FAKE_EMAIL} with {FAKE_PASSWORD}")

    def leaky_email(*args, **kwargs):
        raise smtplib.SMTPAuthenticationError(535, f"bad credentials {FAKE_EMAIL} {FAKE_PASSWORD} to {FAKE_RECIPIENT}".encode())

    with patch("job_monitor.CONNECTORS", {"greenhouse": leaky_fetch}), \
         patch("job_monitor.send_notification", leaky_email):
        job_monitor.main(["--config", config, "--db-path", str(tmp_path / "t.db")])

    audit_text = (isolated_logs / "audit.log").read_text(encoding="utf-8")
    operational_text = (isolated_logs / "operational.log").read_text(encoding="utf-8")
    for text in (audit_text, operational_text):
        assert FAKE_EMAIL not in text
        assert FAKE_PASSWORD not in text
        assert FAKE_RECIPIENT not in text
    assert REDACTED in audit_text
    assert REDACTED in operational_text
    print("PASS: credential values are redacted from both log files")


def test_redact_secrets_ignores_very_short_values(monkeypatch):
    monkeypatch.setenv("EMAIL_APP_PASSWORD", "ab")
    assert redact_secrets("about a bab") == "about a bab"
    print("PASS: tiny values are not redacted, so ordinary text is not blanked out")


def test_redact_secrets_without_env_is_noop(monkeypatch):
    for name in ("EMAIL_ADDRESS", "EMAIL_APP_PASSWORD", "RECIPIENT_EMAIL"):
        monkeypatch.delenv(name, raising=False)
    assert redact_secrets("nothing to hide") == "nothing to hide"
    print("PASS: redaction is a no-op when no credentials are configured")
