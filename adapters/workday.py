# Workday ATS adapter.
#
# POST request, paginated. Loops through offsets until
# all postings are collected. This is the pattern proven working
# on Ensign's endpoint.

import logging
from urllib.parse import urlparse

from utils.audit_log import log_audit_event
from utils.date_utils import days_ago_from_workday_text
from utils.rate_limiter import RateLimiter, RateLimitExceeded
from utils.robots_check import robots_checker, SkipReason
from utils.schema import ALLOWED_LINK_DOMAINS, WorkdayJob, validate_job_posting, validate_raw_jobs
from utils.skip_tracker import SkipTracker

logger = logging.getLogger(__name__)
limiter = RateLimiter()
skip_tracker = SkipTracker()


def fetch_workday(company): # Working
    url = company["workday_url"]
    job_base_url = company.get("job_base_url", "")
    name = company.get("name", url)
    page_size = 20
    offset = 0
    raw_jobs = []

    parsed = urlparse(url)
    if not robots_checker.is_allowed(f"{parsed.scheme}://{parsed.netloc}", parsed.path):
        log_audit_event("robots_txt_skip", company=name, domain=parsed.netloc, path=parsed.path, reason="disallowed for user-agent *")
        streak = skip_tracker.record_skip(name)
        logger.warning("Skipping %s this cycle: robots.txt disallows %s (streak: %d)", name, parsed.path, streak)
        return SkipReason("robots.txt disallowed", f"robots.txt disallows {parsed.path}")

    while True:
        body = {
            "appliedFacets": {},
            "limit": page_size,
            "offset": offset,
            "searchText": ""
        }

        try:
            response = limiter.post(
                url, platform="workday", company=name,
                headers={"Content-Type": "application/json"}, json=body, timeout=15
            )
            response.raise_for_status()
        except RateLimitExceeded as exc:
            streak = skip_tracker.record_skip(name)
            logger.warning("Skipping %s this cycle: %s (streak: %d)", name, exc, streak)
            return SkipReason(exc.reason, str(exc))
        data = response.json()

        postings = data.get("jobPostings", [])
        raw_jobs.extend(validate_raw_jobs(postings, WorkdayJob, "workday", name))

        total = data.get("total", 0)
        offset += page_size

        if offset >= total or not postings:
            break

    skip_tracker.record_success(name)

    jobs = []
    for job in raw_jobs:
        external_path = job.get("externalPath", "")
        posting = validate_job_posting({
            "id": external_path,
            "title": job.get("title", "Untitled"),
            "location": job.get("locationsText", "Unknown"),
            "posted": job.get("postedOn", ""),
            "posted_days_ago": days_ago_from_workday_text(job.get("postedOn")),
            "link": job_base_url + external_path
        }, ats="workday", company=name, allowed_domains=ALLOWED_LINK_DOMAINS["workday"])
        if posting is not None:
            jobs.append(posting)
    return jobs
