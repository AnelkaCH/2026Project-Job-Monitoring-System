# Fetches the scheduled monitor's state (database and logs) from the private
# repo's "state" branch, for the hosted dashboard (v4.1).
#
# Since v4.0 the monitor runs on GitHub Actions and commits data/jobmonitor.db
# and logs/ to a "state" branch, so there is no local copy for dashboard.py to
# read. When STATE_REPO is configured, the dashboard calls fetch_state() to
# download those files into a local cache folder and then reads them exactly
# like local files. When it is not configured, nothing here runs and the
# dashboard keeps reading local paths as before.
#
# Configuration (environment variables, or Streamlit secrets, which Streamlit
# exposes as environment variables):
#     STATE_REPO     "owner/name" of the private repo holding the state branch
#     STATE_TOKEN    fine-grained GitHub token with read-only Contents access
#                    to that one repo
#     STATE_BRANCH   branch name (default "state")
#
# Security notes:
#   - Requests only ever go to api.github.com over https, for a fixed list of
#     file paths. The repo name is validated, so no config value can redirect
#     the request or request another path.
#   - The token is sent only in the Authorization header and never appears in
#     any message this module returns or logs.
#   - The token only needs read access; the dashboard never writes to GitHub.

import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone

import requests

GITHUB_API = "https://api.github.com"
DEFAULT_BRANCH = "state"
REQUEST_TIMEOUT_SECONDS = 30

# Files the dashboard reads, as (path on the state branch, required). The
# database is required; a missing log just leaves that tab empty.
STATE_FILES = (
    ("data/jobmonitor.db", True),
    ("logs/audit.log", False),
    ("logs/operational.log", False),
)

_REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_BRANCH_PATTERN = re.compile(r"^[A-Za-z0-9_./-]+$")


class StateFetchError(Exception):
    # Raised with a message that is safe to show in the dashboard: it never
    # contains the token.
    pass


@dataclass
class StateSource:
    repo: str
    token: str
    branch: str = DEFAULT_BRANCH


@dataclass
class FetchedState:
    db_path: str
    log_dir: str
    fetched_at: datetime
    missing: list = field(default_factory=list)


def state_source_from_env():
    # Returns a StateSource when STATE_REPO is set, otherwise None (local
    # mode). A half-finished configuration raises instead of silently falling
    # back to an empty local database.
    repo = os.environ.get("STATE_REPO", "").strip()
    if not repo:
        return None
    if not _REPO_PATTERN.match(repo):
        raise StateFetchError("STATE_REPO must look like 'owner/name'.")
    token = os.environ.get("STATE_TOKEN", "").strip()
    if not token:
        raise StateFetchError("STATE_REPO is set but STATE_TOKEN is missing.")
    branch = os.environ.get("STATE_BRANCH", DEFAULT_BRANCH).strip() or DEFAULT_BRANCH
    if not _BRANCH_PATTERN.match(branch) or ".." in branch:
        raise StateFetchError("STATE_BRANCH contains characters that are not allowed.")
    return StateSource(repo=repo, token=token, branch=branch)


def default_cache_dir():
    return os.path.join(tempfile.gettempdir(), "jobmonitor-state")


def _download(source, path):
    # Returns the raw file bytes, or None when the file does not exist on the
    # branch. Any other failure raises StateFetchError with a token-free
    # message.
    url = f"{GITHUB_API}/repos/{source.repo}/contents/{path}"
    headers = {
        "Accept": "application/vnd.github.raw+json",
        "Authorization": f"Bearer {source.token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "JobMonitorDashboard/1.0",
    }
    try:
        response = requests.get(url, headers=headers, params={"ref": source.branch}, timeout=REQUEST_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        raise StateFetchError(f"Could not reach GitHub to fetch {path}: {type(exc).__name__}.") from None

    if response.status_code == 200:
        return response.content
    if response.status_code == 404:
        return None
    if response.status_code in (401, 403):
        raise StateFetchError(
            f"GitHub refused access to {source.repo} (HTTP {response.status_code}). "
            "Check that STATE_TOKEN is valid, not expired, and has read access to Contents."
        )
    raise StateFetchError(f"GitHub returned HTTP {response.status_code} while fetching {path}.")


def _write_atomic(target, content):
    # Write to a temp file in the same folder, then swap it in, so the
    # dashboard never opens a half-written SQLite file mid-refresh.
    os.makedirs(os.path.dirname(target), exist_ok=True)
    tmp = f"{target}.part"
    with open(tmp, "wb") as f:
        f.write(content)
    os.replace(tmp, target)


def fetch_state(source, cache_dir=None):
    # Downloads every file in STATE_FILES into cache_dir, keeping the same
    # data/ and logs/ layout, and returns where the dashboard should read.
    cache_dir = cache_dir or default_cache_dir()
    missing = []
    for path, required in STATE_FILES:
        content = _download(source, path)
        if content is None:
            if required:
                raise StateFetchError(
                    f"{path} was not found on the '{source.branch}' branch of {source.repo}. "
                    "Has the scheduled workflow completed a run yet?"
                )
            missing.append(path)
            # Drop any copy left from an earlier fetch, so the dashboard
            # never shows a file that is no longer on the branch.
            stale = os.path.join(cache_dir, *path.split("/"))
            if os.path.exists(stale):
                os.remove(stale)
            continue
        _write_atomic(os.path.join(cache_dir, *path.split("/")), content)

    return FetchedState(
        db_path=os.path.join(cache_dir, "data", "jobmonitor.db"),
        log_dir=os.path.join(cache_dir, "logs"),
        fetched_at=datetime.now(timezone.utc),
        missing=missing,
    )
