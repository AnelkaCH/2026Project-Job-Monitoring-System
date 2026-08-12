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
# 3. Shape ExampleCompanyJob below to the company's actual response schema
#    (same pattern as the per-ATS models in utils/schema.py).
# 4. Register the function in CUSTOM_HANDLERS at the bottom.
# 5. Set "ats": "custom" and "handler": "<name>" in config.json for that company.
 
import logging
from urllib.parse import urlparse
from typing import Union
 
from pydantic import BaseModel, ConfigDict
 
from utils.audit_log import log_audit_event
from utils.date_utils import days_ago_from_iso
from utils.rate_limiter import RateLimiter, RateLimitExceeded
from utils.robots_check import robots_checker, SkipReason
from utils.schema import (
    CleanStr,
    Markup,
    validate_job_posting,
    validate_raw_jobs,
)
from utils.skip_tracker import SkipTracker
 
logger = logging.getLogger(__name__)

limiter = RateLimiter()
skip_tracker = SkipTracker()


class ExampleCompanyJob(BaseModel):
    # Validates the shape this company's API actually returns BEFORE
    # normalization, mirroring the per-ATS models in utils/schema.py.
    # Reshape these fields to match the real response. Markup strips any
    # HTML tags from the title; CleanStr turns optional strings into ""
    # so the .get() fallbacks below keep working.
    model_config = ConfigDict(extra="ignore")
    requisitionId: Union[str, int]
    title: Markup
    location: CleanStr = "Unknown"
    postedDateText: CleanStr = ""
    updateDate: CleanStr = ""
    jobDetailUrl: CleanStr = ""
 
 
def fetch_example_company(company):
    # Replace with the company's real job search endpoint.
    url = "https://example.com/api/careers/search"

    country_site = company.get("country_site", "sg-en")
    job_country = company.get("job_country", "Singapore")
    name = company.get("name", "company_name")
    page_size = 12
    start_index = 0
    all_jobs_raw = []

    parsed = urlparse(url)
    if not robots_checker.is_allowed(f"{parsed.scheme}://{parsed.netloc}", parsed.path):
        log_audit_event("robots_txt_skip", company=name, domain=parsed.netloc, path=parsed.path, reason="disallowed for user-agent *")
        streak = skip_tracker.record_skip(name)
        logger.warning("Skipping %s this cycle: robots.txt disallows %s (streak: %d)", name, parsed.path, streak)
        return SkipReason("robots.txt disallowed", f"robots.txt disallows {parsed.path}")

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
        all_jobs_raw.extend(validate_raw_jobs(postings, ExampleCompanyJob, "example_company", name))

        total = data.get("totalCount", 0)
        start_index += len(postings)

        if start_index >= total or not postings:
            break

    skip_tracker.record_success(name)

    # The expected link domain is the API host by default; if a company's
    # posting links live on a different domain, supply that instead.
    expected_domain = urlparse(url).netloc

    # Normalize into the shared job format used across every connector,
    # regardless of ATS. validate_job_posting re-cleans the stored text and
    # enforces the https + expected-domain URL policy; rejected postings are
    # audited as VALIDATION_REJECTED and dropped, never stored silently.
    jobs = []
    for job in all_jobs_raw:
        posting = validate_job_posting({
            "id": job.get("requisitionId", ""),
            "title": job.get("title", "Untitled"),
            "location": job.get("location", "Unknown"),
            "posted": job.get("postedDateText", ""),
            "posted_days_ago": days_ago_from_iso(job.get("updateDate")),
            "link": job.get("jobDetailUrl", ""),
        }, ats="example_company", company=name, allowed_domains=(expected_domain,))
        if posting is not None:
            jobs.append(posting)
    return jobs
 
 
# Maps a "handler" name (set in config.json for any "ats": "custom" entry)
# to the right function above. Add one function per company that needs
# a bespoke handler, each registered here.
CUSTOM_HANDLERS = {
    "example_company": fetch_example_company,
}