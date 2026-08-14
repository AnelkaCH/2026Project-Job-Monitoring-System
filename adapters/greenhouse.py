# Greenhouse ATS adapter.
#
# Single GET request, no pagination needed.

import logging
from urllib.parse import urlparse

from utils.audit_log import log_audit_event
from utils.date_utils import days_ago_from_iso
from utils.rate_limiter import RateLimiter, RateLimitExceeded
from utils.robots_check import robots_checker, SkipReason
from utils.schema import ALLOWED_LINK_DOMAINS, GreenhouseJob, validate_job_posting, validate_raw_jobs
from utils.skip_tracker import SkipTracker

logger = logging.getLogger(__name__)
limiter = RateLimiter()
skip_tracker = SkipTracker()


def fetch_greenhouse(company): # Working
    slug = company["slug"]
    name = company.get("name", slug)
    url = f"https://api.greenhouse.io/v1/boards/{slug}/jobs"

    parsed = urlparse(url)
    if not robots_checker.is_allowed(f"{parsed.scheme}://{parsed.netloc}", parsed.path):
        log_audit_event("robots_txt_skip", company=name, domain=parsed.netloc, path=parsed.path, reason="disallowed for user-agent *")
        streak = skip_tracker.record_skip(name)
        logger.warning("Skipping %s this cycle: robots.txt disallows %s (streak: %d)", name, parsed.path, streak)
        return SkipReason("robots.txt disallowed", f"robots.txt disallows {parsed.path}")

    try:
        response = limiter.get(url, platform="greenhouse", company=name, timeout=15)
        response.raise_for_status()
    except RateLimitExceeded as exc:
        streak = skip_tracker.record_skip(name)
        logger.warning("Skipping %s this cycle: %s (streak: %d)", name, exc, streak)
        return SkipReason(exc.reason, str(exc))

    skip_tracker.record_success(name)
    data = response.json()

    jobs = []
    for job in validate_raw_jobs(data.get("jobs", []), GreenhouseJob, "greenhouse", name):
        posting = validate_job_posting({
            "id": str(job.get("id")),
            "title": job.get("title", "Untitled"),
            "location": job.get("location", {}).get("name", "Unknown"),
            "posted": job.get("updated_at", ""),
            "posted_days_ago": days_ago_from_iso(job.get("updated_at")),
            "link": job.get("absolute_url", "")
        }, ats="greenhouse", company=name, allowed_domains=ALLOWED_LINK_DOMAINS["greenhouse"])
        if posting is not None:
            jobs.append(posting)
    return jobs
