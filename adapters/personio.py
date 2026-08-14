# Personio ATS adapter.
#
# Single GET request via the /search.json endpoint, no auth.
#
# Two known gaps in this feed:
# 1. No date field exists at all - postings can never get a confirmed
#    age, so they'll always land in the "ambiguous" bucket once
#    max_age_days filtering runs. Not a bug, just what this feed offers.
# 2. No link field - the URL is built from job id using Personio's known
#    pattern, but this hasn't been click-tested to confirm it resolves.
# Also assumes no pagination exists (no total/next-page field seen in
# a real response) - worth revisiting if a large company returns
# suspiciously few results here.

import logging
from urllib.parse import urlparse

from utils.audit_log import log_audit_event
from utils.rate_limiter import RateLimiter, RateLimitExceeded
from utils.robots_check import robots_checker, SkipReason
from utils.schema import ALLOWED_LINK_DOMAINS, PersonioJob, validate_job_posting, validate_raw_jobs
from utils.skip_tracker import SkipTracker

logger = logging.getLogger(__name__)
limiter = RateLimiter()
skip_tracker = SkipTracker()


def fetch_personio(company): # Working
    slug = company["slug"]
    name = company.get("name", slug)
    domain = company.get("domain", "de")  # some tenants use .com instead of .de
    url = f"https://{slug}.jobs.personio.{domain}/search.json"

    parsed = urlparse(url)
    if not robots_checker.is_allowed(f"{parsed.scheme}://{parsed.netloc}", parsed.path):
        log_audit_event("robots_txt_skip", company=name, domain=parsed.netloc, path=parsed.path, reason="disallowed for user-agent *")
        streak = skip_tracker.record_skip(name)
        logger.warning("Skipping %s this cycle: robots.txt disallows %s (streak: %d)", name, parsed.path, streak)
        return SkipReason("robots.txt disallowed", f"robots.txt disallows {parsed.path}")

    try:
        response = limiter.get(url, platform="personio", company=name, timeout=15)
        response.raise_for_status()
    except RateLimitExceeded as exc:
        streak = skip_tracker.record_skip(name)
        logger.warning("Skipping %s this cycle: %s (streak: %d)", name, exc, streak)
        return SkipReason(exc.reason, str(exc))

    skip_tracker.record_success(name)
    data = response.json()

    jobs = []
    for job in validate_raw_jobs(data, PersonioJob, "personio", name):
        posting = validate_job_posting({
            "id": str(job.get("id", "")),
            "title": job.get("name", "Untitled"),
            "location": job.get("office", "Unknown"),
            "posted": "",
            "posted_days_ago": None,
            "link": f"https://{slug}.jobs.personio.{domain}/job/{job.get('id', '')}"
        }, ats="personio", company=name, allowed_domains=ALLOWED_LINK_DOMAINS["personio"])
        if posting is not None:
            jobs.append(posting)
    return jobs
