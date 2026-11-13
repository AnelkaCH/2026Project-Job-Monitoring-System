# Tests for utils/state_fetch.py, the hosted dashboard's loader for the
# scheduled monitor's state branch. GitHub is mocked; no network calls.

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.state_fetch import (
    GITHUB_API,
    StateFetchError,
    StateSource,
    fetch_state,
    state_source_from_env,
)

FAKE_TOKEN = "github_pat_fake_token_for_tests_only"  # pragma: allowlist secret (fake value)


def _response(status_code, content=b""):
    response = MagicMock()
    response.status_code = status_code
    response.content = content
    return response


def _github(files):
    # Fake requests.get: serves the given {path: bytes} map, 404 otherwise.
    def fake_get(url, headers=None, params=None, timeout=None):
        path = url.split("/contents/", 1)[1]
        if path in files:
            return _response(200, files[path])
        return _response(404)
    return fake_get


SOURCE = StateSource(repo="owner/private-repo", token=FAKE_TOKEN)


def test_no_state_repo_means_local_mode(monkeypatch):
    monkeypatch.delenv("STATE_REPO", raising=False)
    assert state_source_from_env() is None
    print("PASS: without STATE_REPO the dashboard stays in local mode")


def test_state_repo_without_token_is_an_error(monkeypatch):
    monkeypatch.setenv("STATE_REPO", "owner/private-repo")
    monkeypatch.delenv("STATE_TOKEN", raising=False)
    with pytest.raises(StateFetchError, match="STATE_TOKEN"):
        state_source_from_env()
    print("PASS: a half-finished config fails loudly instead of showing empty data")


@pytest.mark.parametrize("bad_repo", ["owner", "owner/name/extra", "https://evil.example/x", "owner/na me"])
def test_malformed_repo_rejected(monkeypatch, bad_repo):
    monkeypatch.setenv("STATE_REPO", bad_repo)
    monkeypatch.setenv("STATE_TOKEN", FAKE_TOKEN)
    with pytest.raises(StateFetchError, match="owner/name"):
        state_source_from_env()
    print("PASS: STATE_REPO cannot redirect the request elsewhere")


def test_branch_with_traversal_rejected(monkeypatch):
    monkeypatch.setenv("STATE_REPO", "owner/private-repo")
    monkeypatch.setenv("STATE_TOKEN", FAKE_TOKEN)
    monkeypatch.setenv("STATE_BRANCH", "../main")
    with pytest.raises(StateFetchError):
        state_source_from_env()
    print("PASS: STATE_BRANCH cannot contain path traversal")


def test_fetch_writes_files_in_expected_layout(tmp_path):
    files = {
        "data/jobmonitor.db": b"sqlite-bytes",
        "logs/audit.log": b'{"event": "RUN_END"}\n',
        "logs/operational.log": b"op\n",
    }
    with patch("utils.state_fetch.requests.get", side_effect=_github(files)) as get:
        fetched = fetch_state(SOURCE, cache_dir=str(tmp_path))

    assert Path(fetched.db_path).read_bytes() == b"sqlite-bytes"
    assert (Path(fetched.log_dir) / "audit.log").read_bytes() == b'{"event": "RUN_END"}\n'
    assert fetched.missing == []
    for call in get.call_args_list:
        assert call.args[0].startswith(f"{GITHUB_API}/repos/owner/private-repo/contents/")
        assert call.kwargs["params"] == {"ref": "state"}
        assert call.kwargs["headers"]["Authorization"] == f"Bearer {FAKE_TOKEN}"
    print("PASS: state files are downloaded from api.github.com into data/ and logs/")


def test_missing_database_is_an_error(tmp_path):
    with patch("utils.state_fetch.requests.get", side_effect=_github({})):
        with pytest.raises(StateFetchError, match="completed a run"):
            fetch_state(SOURCE, cache_dir=str(tmp_path))
    print("PASS: a missing database explains that no run has completed yet")


def test_missing_log_is_reported_and_stale_copy_removed(tmp_path):
    stale = tmp_path / "logs" / "operational.log"
    stale.parent.mkdir(parents=True)
    stale.write_text("old run", encoding="utf-8")
    files = {"data/jobmonitor.db": b"db", "logs/audit.log": b"a\n"}

    with patch("utils.state_fetch.requests.get", side_effect=_github(files)):
        fetched = fetch_state(SOURCE, cache_dir=str(tmp_path))

    assert fetched.missing == ["logs/operational.log"]
    assert not stale.exists()
    print("PASS: a missing log is reported and an old cached copy is not shown")


@pytest.mark.parametrize("status", [401, 403])
def test_auth_failure_message_never_contains_token(tmp_path, status):
    with patch("utils.state_fetch.requests.get", return_value=_response(status)):
        with pytest.raises(StateFetchError) as excinfo:
            fetch_state(SOURCE, cache_dir=str(tmp_path))
    assert FAKE_TOKEN not in str(excinfo.value)
    assert "STATE_TOKEN" in str(excinfo.value)
    print("PASS: an auth failure explains the fix without exposing the token")


def test_network_error_message_never_contains_token(tmp_path):
    import requests

    error = requests.ConnectionError(f"failed with header Bearer {FAKE_TOKEN}")
    with patch("utils.state_fetch.requests.get", side_effect=error):
        with pytest.raises(StateFetchError) as excinfo:
            fetch_state(SOURCE, cache_dir=str(tmp_path))
    assert FAKE_TOKEN not in str(excinfo.value)
    assert excinfo.value.__cause__ is None
    print("PASS: network errors are reported by type only, never with the token")


def test_partial_file_never_left_behind(tmp_path):
    files = {"data/jobmonitor.db": b"db", "logs/audit.log": b"a", "logs/operational.log": b"o"}
    with patch("utils.state_fetch.requests.get", side_effect=_github(files)):
        fetch_state(SOURCE, cache_dir=str(tmp_path))
    leftovers = [p for p in tmp_path.rglob("*.part")]
    assert leftovers == []
    print("PASS: downloads are swapped in atomically with no .part files left")
