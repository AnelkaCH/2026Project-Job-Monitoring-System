# SAP SuccessFactors Recruiting adapter.
#
# Public JSON endpoint, page-based pagination.

import logging
from urllib.parse import urlparse

from utils.audit_log import log_audit_event
from utils.date_utils import calculate_sap_days_ago
from utils.rate_limiter import RateLimiter, RateLimitExceeded
from utils.robots_check import robots_checker, SkipReason
from utils.schema import SapPayload, validate_job_posting, validate_raw_jobs
from utils.skip_tracker import SkipTracker

logger = logging.getLogger(__name__)
limiter = RateLimiter()
skip_tracker = SkipTracker()


def fetch_sap(company): # Not Working (robots.txt disallowed)
    url = company["sap_url"]
    locale = company.get("locale", "en_GB")
    job_base_url = company.get("job_base_url", "")
    name = company.get("name", url)

    page = 0
    all_jobs = []

    parsed = urlparse(url)
    if not robots_checker.is_allowed(f"{parsed.scheme}://{parsed.netloc}", parsed.path):
        log_audit_event("robots_txt_skip", company=name, domain=parsed.netloc, path=parsed.path, reason="disallowed for user-agent *")
        streak = skip_tracker.record_skip(name)
        logger.warning("Skipping %s this cycle: robots.txt disallows %s (streak: %d)", name, parsed.path, streak)
        return SkipReason("robots.txt disallowed", f"robots.txt disallows {parsed.path}")

    while True:
        body = {
            "locale": locale,
            "pageNumber": page,
            "sortBy": "",
            "keywords": "",
            "location": "",
            "facetFilters": {},
            "brand": "",
            "skills": [],
            "categoryId": 0,
            "alertId": "",
            "rcmCandidateId": ""
        }

        try:
            response = limiter.post(
                url, platform="sap", company=name,
                headers={"Content-Type": "application/json"}, json=body, timeout=15
            )
            response.raise_for_status()
        except RateLimitExceeded as exc:
            streak = skip_tracker.record_skip(name)
            logger.warning("Skipping %s this cycle: %s (streak: %d)", name, exc, streak)
            return SkipReason(exc.reason, str(exc))
        data = response.json()

        postings = data.get("jobSearchResult", [])

        if not postings:
            break

        all_jobs.extend(validate_raw_jobs(postings, SapPayload, "sap", name))

        page += 1

    skip_tracker.record_success(name)

    jobs = []

    sap_domain = urlparse(job_base_url).netloc

    for item in all_jobs:
        job = item.get("response") or {}

        title = job.get("unifiedStandardTitle") or job.get("title") or "Untitled"

        location = ", ".join(job.get("jobLocationCountry", []))
        location = location.replace("<br/>", "").strip()

        job_id = str(job.get("id", ""))

        slug = job.get("urlTitle") or job.get("unifiedUrlTitle") or ""

        posted = job.get("unifiedStandardStart", "")
        posted_days_ago = calculate_sap_days_ago(posted)

        if posted_days_ago is None or posted_days_ago > 30:
            continue

        posting = validate_job_posting({
            "id": job_id,
            "title": title,
            "location": location,
            "posted": posted,
            "posted_days_ago": posted_days_ago,
            "link": f"{job_base_url}{slug}"
        }, ats="sap", company=name, allowed_domains=(sap_domain,))
        if posting is not None:
            jobs.append(posting)

    return jobs
