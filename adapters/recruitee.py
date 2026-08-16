# Recruitee ATS adapter.
#
# Single GET request, no pagination needed.

import logging
from urllib.parse import urlparse

from utils.audit_log import log_audit_event
from utils.date_utils import days_ago_from_iso
from utils.rate_limiter import RateLimiter, RateLimitExceeded
from utils.robots_check import robots_checker, SkipReason
from utils.schema import ALLOWED_LINK_DOMAINS, RecruiteeJob, validate_job_posting, validate_raw_jobs
from utils.skip_tracker import SkipTracker

logger = logging.getLogger("job_monitor.operational")
limiter = RateLimiter()
skip_tracker = SkipTracker()


def fetch_recruitee(company): # Working
    slug = company["slug"]
    name = company.get("name", slug)
    url = f"https://{slug}.recruitee.com/api/offers/"

    parsed = urlparse(url)
    if not robots_checker.is_allowed(f"{parsed.scheme}://{parsed.netloc}", parsed.path):
        log_audit_event("robots_txt_skip", company=name, domain=parsed.netloc, path=parsed.path, reason="disallowed for user-agent *")
        streak = skip_tracker.record_skip(name)
        logger.warning("Skipping %s this cycle: robots.txt disallows %s (streak: %d)", name, parsed.path, streak)
        return SkipReason("robots.txt disallowed", f"robots.txt disallows {parsed.path}")

    try:
        response = limiter.get(url, platform="recruitee", company=name, timeout=15)
        response.raise_for_status()
    except RateLimitExceeded as exc:
        streak = skip_tracker.record_skip(name)
        logger.warning("Skipping %s this cycle: %s (streak: %d)", name, exc, streak)
        return SkipReason(exc.reason, str(exc))

    skip_tracker.record_success(name)
    data = response.json()

    jobs = []
    for job in validate_raw_jobs(data.get("offers", []), RecruiteeJob, "recruitee", name):
        posting = validate_job_posting({
            "id": str(job.get("id", "")),
            "title": job.get("title", "Untitled"),
            "location": job.get("city", "Unknown") or "Unknown",
            "posted": job.get("created_at", ""),
            "posted_days_ago": days_ago_from_iso(job.get("created_at")),
            "link": job.get("careers_url", "")
        }, ats="recruitee", company=name, allowed_domains=ALLOWED_LINK_DOMAINS["recruitee"])
        if posting is not None:
            jobs.append(posting)
    return jobs
