# robots.txt compliance checker for the job monitoring system.
#
# Before any adapter makes an API call to a company's job board, this
# module checks that company's robots.txt to confirm the endpoint path
# is allowed for a generic user-agent ("*").  If disallowed, the adapter
# skips the company for that cycle rather than proceeding.
#
# This serves the same ethical/legal compliance purpose as the Tier 3
# hard-stop rule in audit_log.check_hardstop(): both are deliberate
# boundaries designed to keep the system operating within documented
# access constraints.  robots.txt is the standardised, decades-old
# protocol for signalling those constraints, and respecting it is a
# baseline expectation for any well-behaved automated agent.
#
# Fetching is done via requests with an identifying User-Agent; the
# response text is parsed with urllib.robotparser. The policy is
# fail-conservative: if robots.txt is unreachable, explicitly denied,
# or persistently returns a server-side error, the path is treated as
# disallowed. A missing robots.txt (404) means allow-all per RFC 9309.

import json
import logging
import os
import threading
import requests
import time
from dataclasses import dataclass
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

logger = logging.getLogger(__name__)

# Path relative to this file, same convention as job_monitor's config loading.
CONFIG_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")


class RobotsChecker:
    # Per-domain cache of RobotFileParser instances.

    # Mirrors the ``_trackers`` dict in ``rate_limiter._DomainTracker``:
    # in-memory storage keyed by domain URL, no TTL / per-process lifetime.
    # The cache is shared across concurrent adapter workers (job_monitor runs
    # adapters via ThreadPoolExecutor), so creation is guarded by a lock.

    def __init__(self):
        self._parsers = {}
        self._lock = threading.Lock()

    def _domain_url(self, base_url: str) -> str:
        # """Normalise a base URL to ``scheme://netloc`` form."""
        parsed = urlparse(base_url)
        return f"{parsed.scheme}://{parsed.netloc}"

    def _fetch_parser(self, domain_url: str, retries: int = 1, backoff_base: float = 1.5):
        robots_url = domain_url + "/robots.txt"
        headers = {"User-Agent": "JobMonitorBot/1.0 (+https://github.com/AnelkaCH/job-monitoring-system)"}

        attempt = 0
        while True:
            rp = RobotFileParser()
            rp.set_url(robots_url)
            try:
                resp = requests.get(robots_url, timeout=10, headers=headers)
            except requests.exceptions.RequestException as exc:
                if attempt < retries:
                    delay = backoff_base * (2 ** attempt)
                    logger.info(
                        "robots.txt fetch failed at %s (attempt %d/%d), retrying in %.1fs: %s",
                        domain_url, attempt + 1, retries + 1, delay, exc,
                    )
                    time.sleep(delay)
                    attempt += 1
                    continue
                logger.warning("Could not fetch/parse robots.txt at %s: %s", domain_url, exc)
                return None

            if resp.status_code == 200:
                rp.parse(resp.text.splitlines())
                return rp
            elif resp.status_code in (401, 403):
                # Explicit access denial, fail conservative, no point retrying.
                logger.warning("robots.txt fetch got %s at %s - treating as disallow", resp.status_code, domain_url)
                return None
            elif 500 <= resp.status_code < 600:
                # Server-side error, worth one retry, then fail conservative.
                if attempt < retries:
                    delay = backoff_base * (2 ** attempt)
                    logger.info(
                        "robots.txt fetch got %s at %s (attempt %d/%d), retrying in %.1fs",
                        resp.status_code, domain_url, attempt + 1, retries + 1, delay,
                    )
                    time.sleep(delay)
                    attempt += 1
                    continue
                logger.warning("robots.txt fetch got %s at %s - treating as disallow", resp.status_code, domain_url)
                return None
            else:
                # 404 etc. - RFC 9309: absence of robots.txt means allow-all.
                # Other 4xx also falls here rather than failing conservative,
                # since it's not an explicit access denial.
                rp.parse([])
                return rp

    def is_allowed(self, base_url: str, path: str, user_agent: str = "*") -> bool:
        # Check whether ``path`` is allowed by the domain's ``robots.txt``.

        # Parameters
        # ----------
        # base_url : str
        #     Scheme + hostname of the domain, e.g. ``"https://api.greenhouse.io"``.
        # path : str
        #     URL path (and optional query string), e.g. ``"/v1/boards/some-company/jobs"``.
        # user_agent : str
        #     User-agent token to check against.  Defaults to ``"*"``.

        # Returns
        # -------
        # bool
        #     ``True`` if allowed, ``False`` if disallowed or if the check
        #     could not be completed (fail conservative).
        domain = self._domain_url(base_url)

        with self._lock:
            if domain not in self._parsers:
                parser = self._fetch_parser(domain)
                if parser is None:
                    return False
                self._parsers[domain] = parser
            else:
                parser = self._parsers[domain]

        return parser.can_fetch(user_agent, path)


# Shared instance usable throughout the codebase - same pattern as
# ``limiter = RateLimiter()`` in connectors.py.
robots_checker = RobotsChecker()


@dataclass
class SkipReason:
    # Return type from adapters when a company is skipped.

    # ``job_monitor.py`` checks for this instead of ``None`` so it can
    # display the correct reason - rate-limited, robots.txt disallowed,
    # etc. - rather than a generic "rate-limited" message.
    # 
    reason: str
    detail: str = ""


# Standalone compliance CLI
# Running this module directly prints a pass/fail summary for every
# company in the current config without executing a full monitoring cycle.

# Maps an ATS type to (base_url, path) builders so we can construct the
# endpoint URL that would be checked during a real run.  The path omits
# query parameters (cursor tokens, offset values, etc.) since those are
# request-specific and robots.txt rules apply to the resource path itself.
_COMPLIANCE_URLS = {
    "greenhouse":       lambda c: ("https://api.greenhouse.io",              f"/v1/boards/{c['slug']}/jobs"),
    "lever":            lambda c: ("https://api.lever.co",                   f"/v0/postings/{c['slug']}"),
    "ashby":            lambda c: ("https://api.ashbyhq.com",                f"/posting-api/job-board/{c['slug']}"),
    "smartrecruiters":  lambda c: ("https://api.smartrecruiters.com",        f"/v1/companies/{c['slug']}/postings"),
    "recruitee":        lambda c: (f"https://{c['slug']}.recruitee.com",    "/api/offers/"),
    "workable":         lambda c: ("https://apply.workable.com",             f"/api/v3/accounts/{c['slug']}/jobs"),
    "personio":         lambda c: (f"https://{c['slug']}.jobs.personio.{c.get('domain', 'de')}", "/search.json"),
    "workday":          lambda c: _split_url(c.get("workday_url", "")),
    "sap":              lambda c: _split_url(c.get("sap_url", "")),
}

# Custom handler endpoint URLs are kept out of this file on purpose: real
# handlers live in the gitignored adapters/custom_handlers.py, so no
# company-specific details ever land in committed code. The registry is
# filled at CLI runtime by the loader below; when that module is absent
# (CI, fresh forks), it stays empty and the CLI reports an unknown URL
# pattern for any custom handler.
_CUSTOM_COMPLIANCE_URLS = {}


def _load_custom_compliance_urls():
    # Mirrors job_monitor.py's optional-import pattern so the CLI still
    # works when the private handler module is not present in the repo.
    try:
        from adapters.custom_handlers import COMPLIANCE_URLS as private_urls
        return dict(private_urls)
    except ModuleNotFoundError:
        return {}


def _split_url(url: str):
    """Split a full URL into ``(base_url, path)`` pair."""
    parsed = urlparse(url)
    return (f"{parsed.scheme}://{parsed.netloc}", parsed.path)


def _run_compliance_check():
    """Load config and print a pass/fail table for every company."""
    if not os.path.exists(CONFIG_FILE):
        print("No config.json found - nothing to check.")
        return

    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        config = json.load(f)

    companies = config.get("companies", [])
    total = len(companies)
    passed = 0
    failed = 0

    custom_urls = _load_custom_compliance_urls()

    print(f"Robots.txt compliance check for {total} companies:\n")

    for company in companies:
        name = company["name"]
        ats = company["ats"]

        if ats == "custom":
            handler = company.get("handler", "")
            url_fn = custom_urls.get(handler)
            if url_fn is None:
                print(f"  {name:30s} custom handler '{handler}' - unknown URL pattern")
                continue
        else:
            url_fn = _COMPLIANCE_URLS.get(ats)
        if url_fn is None:
            print(f"  {name:30s} unknown ATS '{ats}' - skipped")
            continue

        try:
            base_url, path = url_fn(company)
        except Exception:
            print(f"  {name:30s} could not determine endpoint URL")
            failed += 1
            continue

        allowed = robots_checker.is_allowed(base_url, path)
        status = "ALLOWED" if allowed else "DISALLOWED"
        if allowed:
            passed += 1
        else:
            failed += 1

        robots_url = base_url + "/robots.txt"
        print(f"  {name:30s} {robots_url:55s} {status}")

    print(f"\n{passed}/{total} companies pass robots.txt compliance.")
    if failed:
        print(f"{failed}/{total} companies are disallowed - adapters will skip them.")


if __name__ == "__main__":
    _run_compliance_check()
