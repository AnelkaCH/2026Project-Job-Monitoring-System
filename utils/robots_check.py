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
# Using urllib.robotparser (stdlib only) and
# failing conservatively: if robots.txt is unreachable or unparseable
# for any reason, the path is treated as disallowed.

import json
import logging
import os
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

    def __init__(self):
        self._parsers = {}

    def _domain_url(self, base_url: str) -> str:
        # """Normalise a base URL to ``scheme://netloc`` form."""
        parsed = urlparse(base_url)
        return f"{parsed.scheme}://{parsed.netloc}"

    def _fetch_parser(self, domain_url: str):
        # Fetch and parse ``robots.txt`` for the domain.

        # Returns a ``RobotFileParser`` on success, or ``None`` if the
        # resource is unreachable, unparseable, or errors for any reason
        # (fail conservative).

        rp = RobotFileParser()
        rp.set_url(domain_url + "/robots.txt")
        try:
            rp.read()
        except Exception as exc:
            logger.warning("Could not fetch/parse robots.txt at %s: %s", domain_url, exc)
            return None
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

# Custom handlers whose endpoint URLs are known and public.
_CUSTOM_COMPLIANCE_URLS = {
    "accenture": lambda c: ("https://www.accenture.com", "/api/accenture/elastic/findjobs"),
}


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

    print(f"Robots.txt compliance check for {total} companies:\n")

    for company in companies:
        name = company["name"]
        ats = company["ats"]

        if ats == "custom":
            handler = company.get("handler", "")
            url_fn = _CUSTOM_COMPLIANCE_URLS.get(handler)
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
