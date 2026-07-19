# Template for a custom, company-specific ATS handler.
 
# Some companies don't run on a standard ATS product (Greenhouse, Lever,
# Ashby, etc.) and instead expose their own bespoke job search API. This
# template shows the general shape a handler like that follows, without
# any real company's endpoint, request format, or response schema.
 
# Real handlers built from this template are kept private, since they're
# built against a specific company's internal API and publishing that
# publicly isn't something I'm comfortable doing.
 
# How to use this template:
# 1. Copy this file, rename it to the company's name.
# 2. Fill in the real endpoint, request format, and response parsing.
# 3. Register the function in CUSTOM_HANDLERS at the bottom.
# 4. Set "ats": "custom" and "handler": "<name>" in config.json for that company.
 
import logging
 
from utils.date_utils import days_ago_from_iso
from utils.rate_limiter import RateLimiter, RateLimitExceeded
from utils.robots_check import SkipReason
from utils.skip_tracker import SkipTracker
 
logger = logging.getLogger(__name__)

limiter = RateLimiter()
skip_tracker = SkipTracker()
 
 
def fetch_example_company(company):
    # Replace with the company's real job search endpoint.
    url = "https://example.com/api/careers/search"
 
    country_site = company.get("country_site", "sg-en")
    job_country = company.get("job_country", "Singapore")
    name = company.get("name", "company_name")
    page_size = 12
    start_index = 0
    all_jobs_raw = []
 
    # Many custom job search APIs paginate through a start index / page
    # size / total count pattern. Adjust to match the real API's shape,
    # some use page numbers instead, or cursor-based pagination.
    while True:
        request_params = {
            "startIndex": start_index,
            "maxResultSize": page_size,
            "jobCountry": job_country,
            "countrySite": country_site,
        }
 
        try:
            response = limiter.get(url, platform="platform_name", company=name, timeout=15)
            response.raise_for_status()
        except RateLimitExceeded as exc:
            streak = skip_tracker.record_skip(name)
            logger.warning("Skipping %s this cycle: %s (streak: %d)", name, exc, streak)
            return SkipReason(exc.reason, str(exc))
        data = response.json()
 
        postings = data.get("results", [])
        all_jobs_raw.extend(postings)
 
        total = data.get("totalCount", 0)
        start_index += len(postings)
 
        if start_index >= total or not postings:
            break
 
    skip_tracker.record_success(name)
    # Normalize into the shared job format used across every connector,
    # regardless of ATS.
    jobs = []
    for job in all_jobs_raw:
        jobs.append({
            "id": job.get("requisitionId", ""),
            "title": job.get("title", "Untitled"),
            "location": job.get("location", "Unknown"),
            "posted": job.get("postedDateText", ""),
            "posted_days_ago": days_ago_from_iso(job.get("updateDate")),
            "link": job.get("jobDetailUrl", ""),
        })
    return jobs
 
 
# Maps a "handler" name (set in config.json for any "ats": "custom" entry)
# to the right function above. Add one function per company that needs
# a bespoke handler, each registered here.
CUSTOM_HANDLERS = {
    "example_company": fetch_example_company,
}