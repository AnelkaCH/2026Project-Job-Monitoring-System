# SmartRecruiters ATS adapter.
#
# Public Posting API, GET, paginated via offset/limit,
# confirmed no-auth-required for this specific endpoint per their docs.
#
# Pagination advances by however many postings actually came back, not
# by the requested page_size, in case the server ever returns fewer
# than asked for.

import logging
from urllib.parse import urlparse

from utils.audit_log import log_audit_event
from utils.date_utils import days_ago_from_iso
from utils.rate_limiter import RateLimiter, RateLimitExceeded
from utils.robots_check import robots_checker, SkipReason
from utils.schema import ALLOWED_LINK_DOMAINS, SmartRecruitersJob, validate_job_posting, validate_raw_jobs
from utils.skip_tracker import SkipTracker

logger = logging.getLogger(__name__)
limiter = RateLimiter()
skip_tracker = SkipTracker()


def fetch_smartrecruiters(company): # Not Working (robots.txt disallowed)
    slug = company["slug"]
    name = company.get("name", slug)
    requested_page_size = 100
    offset = 0
    all_postings = []

    first_url = (f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
                 f"?offset={offset}&limit={requested_page_size}")
    parsed = urlparse(first_url)
    if not robots_checker.is_allowed(f"{parsed.scheme}://{parsed.netloc}", parsed.path):
        log_audit_event("robots_txt_skip", company=name, domain=parsed.netloc, path=parsed.path, reason="disallowed for user-agent *")
        streak = skip_tracker.record_skip(name)
        logger.warning("Skipping %s this cycle: robots.txt disallows %s (streak: %d)", name, parsed.path, streak)
        return SkipReason("robots.txt disallowed", f"robots.txt disallows {parsed.path}")

    while True:
        url = (f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
               f"?offset={offset}&limit={requested_page_size}")
        try:
            response = limiter.get(url, platform="smartrecruiters", company=name, timeout=15)
            response.raise_for_status()
        except RateLimitExceeded as exc:
            streak = skip_tracker.record_skip(name)
            logger.warning("Skipping %s this cycle: %s (streak: %d)", name, exc, streak)
            return SkipReason(exc.reason, str(exc))
        data = response.json()

        postings = data.get("content", [])
        all_postings.extend(validate_raw_jobs(postings, SmartRecruitersJob, "smartrecruiters", name))

        total_found = data.get("totalFound", 0)
        offset += len(postings)

        if offset >= total_found or not postings:
            break

    skip_tracker.record_success(name)

    jobs = []
    for job in all_postings:
        location = job.get("location", {})
        location_text = ", ".join(filter(None, [
            location.get("city"), location.get("region"), location.get("country")
        ])) or "Unknown"

        posting = validate_job_posting({
            "id": job.get("id", ""),
            "title": job.get("name", "Untitled"),
            "location": location_text,
            "posted": job.get("releasedDate", ""),
            "posted_days_ago": days_ago_from_iso(job.get("releasedDate")),
            "link": job.get("ref", "")
        }, ats="smartrecruiters", company=name, allowed_domains=ALLOWED_LINK_DOMAINS["smartrecruiters"])
        if posting is not None:
            jobs.append(posting)
    return jobs
