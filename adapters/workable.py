# Workable ATS adapter.
#
# POST request, cursor-based pagination (not offset-based).
# Each response includes a "nextPage" token; feeding it back as "token"
# in the next request's body is how you get the next page (confirmed
# via DevTools against a real multi-page company).

import logging
from urllib.parse import urlparse

from utils.audit_log import log_audit_event
from utils.date_utils import days_ago_from_iso
from utils.rate_limiter import RateLimiter, RateLimitExceeded
from utils.robots_check import robots_checker, SkipReason
from utils.schema import ALLOWED_LINK_DOMAINS, WorkableJob, validate_job_posting, validate_raw_jobs
from utils.skip_tracker import SkipTracker

logger = logging.getLogger("job_monitor.operational")
limiter = RateLimiter()
skip_tracker = SkipTracker()


def fetch_workable(company): # Not Working (robots.txt disallowed)
    slug = company["slug"]
    name = company.get("name", slug)
    url = f"https://apply.workable.com/api/v3/accounts/{slug}/jobs"

    parsed = urlparse(url)
    if not robots_checker.is_allowed(f"{parsed.scheme}://{parsed.netloc}", parsed.path):
        log_audit_event("robots_txt_skip", company=name, domain=parsed.netloc, path=parsed.path, reason="disallowed for user-agent *")
        streak = skip_tracker.record_skip(name)
        logger.warning("Skipping %s this cycle: robots.txt disallows %s (streak: %d)", name, parsed.path, streak)
        return SkipReason("robots.txt disallowed", f"robots.txt disallows {parsed.path}")

    all_results = []
    body = {"query": "", "department": [], "location": [], "workplace": [], "worktype": []}

    while True:
        try:
            response = limiter.post(
                url, platform="workable", company=name,
                headers={"Content-Type": "application/json"}, json=body, timeout=15
            )
            response.raise_for_status()
        except RateLimitExceeded as exc:
            streak = skip_tracker.record_skip(name)
            logger.warning("Skipping %s this cycle: %s (streak: %d)", name, exc, streak)
            return SkipReason(exc.reason, str(exc))
        data = response.json()

        results = data.get("results", [])
        all_results.extend(validate_raw_jobs(results, WorkableJob, "workable", name))

        next_page = data.get("nextPage")
        if not next_page or not results:
            break
        body["token"] = next_page

    skip_tracker.record_success(name)

    jobs = []
    for job in all_results:
        location = job.get("location", {})
        location_text = ", ".join(filter(None, [
            location.get("city"), location.get("region"), location.get("country")
        ])) or "Unknown"

        shortcode = job.get("shortcode", "")
        posting = validate_job_posting({
            "id": str(job.get("id", "")),
            "title": job.get("title", "Untitled"),
            "location": location_text,
            "posted": job.get("published", ""),
            "posted_days_ago": days_ago_from_iso(job.get("published")),
            "link": f"https://apply.workable.com/{slug}/j/{shortcode}/"
        }, ats="workable", company=name, allowed_domains=ALLOWED_LINK_DOMAINS["workable"])
        if posting is not None:
            jobs.append(posting)
    return jobs
