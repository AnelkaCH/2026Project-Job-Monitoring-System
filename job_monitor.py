# Multi-company job monitor.
 
# Reads config.json for the list of companies to check. For each company,
# calls the connector matching its "ats" field, fetches current postings,
# compares against what was saved last run, and reports anything new.
 
# Usage:
#     python job_monitor.py
 
import concurrent.futures
import json
import logging
import os
from datetime import datetime, timezone
 
from utils.audit_log import setup_logging, log_audit_event
from adapters.connectors import CONNECTORS
from utils.robots_check import SkipReason
from utils.matching import keyword_matches, has_date_range_signal
# for the sake of the ci pipeline, we allow the custom_handlers module to be missing (it is not included in the repo)
try:
    from adapters.custom_handlers import CUSTOM_HANDLERS
except ModuleNotFoundError:
    CUSTOM_HANDLERS = {}
from utils.notifier import send_notification
from utils.skip_tracker import SkipTracker

operational_logger = logging.getLogger("job_monitor.operational")
 
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
SEEN_JOBS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "seen_jobs.json")

skip_tracker = SkipTracker()
MAX_CONCURRENT_COMPANIES = 15
 
def load_config(config_path=None):
    with open(config_path or CONFIG_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data["companies"], data.get("filters", {"locations": [], "role_keywords": [], "domain_keywords": [], "exclude_keywords": []})
 
 
def matches_filters(job, filters):
    # Returns "match", "ambiguous", or "no_match". Three things are checked:
    # location, role/domain keywords, and age. All three must pass for "match".
    # If keywords or location clearly fail, return "no_match" immediately.
    # If age can't be confirmed (e.g. Workday's "30+ Days Ago") but nothing
    # else fails, return "ambiguous", same treatment as an unclear location.

    location_text = job.get("location", "").lower()
    title_text = job.get("title", "")

    wanted_locations = [loc.lower() for loc in filters.get("locations", [])]
    role_keywords = filters.get("role_keywords", [])
    domain_keywords = filters.get("domain_keywords", [])
    exclude_keywords = filters.get("exclude_keywords", [])
    max_age_days = filters.get("max_age_days")

    # Keyword matching requires ALL of the following:
    #   - at least one role_keywords entry in the title (a date-range signal
    #     can substitute for this, see has_date_range_signal)
    #   - at least one domain_keywords entry in the title
    #   - zero exclude_keywords entries in the title
    # The exclude list wins over everything: if a title matches an excluded
    # term (e.g. "HR Analyst" contains "analyst"), reject immediately, even if
    # it would otherwise pass on a role/domain keyword match.
    if keyword_matches(title_text, exclude_keywords) is not None:
        return "no_match"

    role_hit = keyword_matches(title_text, role_keywords) is not None or has_date_range_signal(title_text)
    domain_hit = keyword_matches(title_text, domain_keywords) is not None
    keyword_match = role_hit and domain_hit

    if not keyword_match:
        return "no_match"

    if not wanted_locations:
        location_match = True
        location_ambiguous = False
    else:
        location_match = any(loc in location_text for loc in wanted_locations)
        location_ambiguous = (not location_match) and ("location" in location_text)

    # Age check: None means "can't confirm exact age" (e.g. Workday's 30+ case)
    age_days = job.get("posted_days_ago")
    if max_age_days is None:
        age_ok = True
        age_ambiguous = False
    elif age_days is None:
        age_ok = False       # not clearly OK...
        age_ambiguous = True  # ...but not clearly stale either, so flag it
    else:
        age_ok = age_days <= max_age_days
        age_ambiguous = False

    if location_match is False and not location_ambiguous:
        return "no_match"
    if age_ambiguous is False and age_ok is False:
        return "no_match"

    if location_ambiguous or age_ambiguous:
        return "ambiguous"
    return "match"
 
 
def load_seen_jobs():
    # Structure: { "Company Name": {"matched_ids": [...], "ambiguous_ids": [...]}, ... }
    # Both lists are tracked so ambiguous postings only notify once too,
    # instead of re-appearing in every single run's email forever.

    if not os.path.exists(SEEN_JOBS_FILE):
        return {}
    with open(SEEN_JOBS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)
 
 
def save_seen_jobs(seen_jobs):
    with open(SEEN_JOBS_FILE, "w", encoding="utf-8") as f:
        json.dump(seen_jobs, f, indent=2)


def build_company_record(company, current_jobs, ambiguous_jobs, previous_record, now_iso=None):
    # Builds the stored entry for one company in seen_jobs.json.
    #
    # Dedup identity stays the job id per company (matched_ids / ambiguous_ids
    # are unchanged, same mechanism as before). Since v3.0 an extra "details"
    # map is written too, keyed by job id, so the dashboard can show titles,
    # links, ATS platform, and the date each posting was first matched.
    #
    # first_seen is preserved for ids already in the previous record and set
    # to now for brand-new ones. Records written before v3.0 have no details
    # at all, so their ids get backfilled with now on the first v3.0 run.

    if now_iso is None:
        now_iso = datetime.now(timezone.utc).isoformat()

    previous_record = previous_record or {}
    previous_details = previous_record.get("details", {})

    details = {}
    for job in current_jobs + ambiguous_jobs:
        job_id = job["id"]
        previous = previous_details.get(job_id, {})
        details[job_id] = {
            "title": job.get("title", "Untitled"),
            "location": job.get("location", "Unknown"),
            "posted": job.get("posted", ""),
            "posted_days_ago": job.get("posted_days_ago"),
            "link": job.get("link", ""),
            "ats": company.get("ats", "unknown"),
            "first_seen": previous.get("first_seen", now_iso),
        }

    return {
        "matched_ids": [job["id"] for job in current_jobs],
        "ambiguous_ids": [job["id"] for job in ambiguous_jobs],
        "details": details,
    }


def check_company(company, filters, seen_jobs):
    # Fetches and classifies one company's postings. Returns a structured
    # dict so the caller (main thread) can log and aggregate results without
    # multiple workers racing on shared state.
    name = company["name"]
    ats = company["ats"]

    if ats == "custom":
        handler_name = company.get("handler")
        fetch_function = CUSTOM_HANDLERS.get(handler_name)
        if fetch_function is None:
            return {"name": name, "ats": ats, "status": "no_handler", "detail": f"no custom handler named '{handler_name}'"}
    elif ats in CONNECTORS:
        fetch_function = CONNECTORS[ats]
    else:
        return {"name": name, "ats": ats, "status": "unknown_ats", "detail": f"unknown ATS type '{ats}'"}

    log_audit_event("QUERY", ats=ats, company=name)
    operational_logger.info("Checking %s (%s)...", name, ats)

    try:
        raw_jobs = fetch_function(company)
    except Exception as e:
        log_audit_event("TIER3_HARDSTOP", ats=ats, company=name, reason=f"unexpected_error: {e}")
        return {"name": name, "ats": ats, "status": "error", "detail": str(e)}

    if isinstance(raw_jobs, SkipReason):
        log_audit_event("SKIP", ats=ats, company=name, reason=raw_jobs.reason, detail=raw_jobs.detail)
        return {"name": name, "ats": ats, "status": "skipped", "skip_reason": raw_jobs}

    # Split fetched jobs into: relevant matches, ambiguous, and the rest.
    # Only "match" and "ambiguous" jobs get saved to seen_jobs = anything
    # clearly irrelevant (wrong location, wrong keyword) is dropped here
    # so it never counts toward "new" and never needs to be tracked.
    current_jobs = []
    ambiguous_jobs = []
    for job in raw_jobs:
        result = matches_filters(job, filters)
        if result == "match":
            current_jobs.append(job)
        elif result == "ambiguous":
            ambiguous_jobs.append(job)

    previous_record = seen_jobs.get(name, {})
    previous_matched_ids = set(previous_record.get("matched_ids", []))
    previous_ambiguous_ids = set(previous_record.get("ambiguous_ids", []))

    new_jobs = [job for job in current_jobs if job["id"] not in previous_matched_ids]
    new_ambiguous_jobs = [job for job in ambiguous_jobs if job["id"] not in previous_ambiguous_ids]

    return {
        "name": name,
        "ats": ats,
        "status": "ok",
        "company": company,
        "current_jobs": current_jobs,
        "ambiguous_jobs": ambiguous_jobs,
        "new_jobs": new_jobs,
        "new_ambiguous_jobs": new_ambiguous_jobs,
        "previous_record": previous_record,
    }


def log_company_result(result, all_new_jobs, all_ambiguous_jobs, seen_jobs):
    name = result["name"]
    ats = result["ats"]
    status = result["status"]

    if status == "no_handler":
        operational_logger.warning("[%s] [SKIP] %s", name, result["detail"])
        return
    if status == "unknown_ats":
        operational_logger.warning("[%s] [SKIP] %s", name, result["detail"])
        return
    if status == "error":
        operational_logger.error("[%s] [ERROR] Failed to fetch: %s", name, result["detail"])
        return
    if status == "skipped":
        raw = result["skip_reason"]
        if raw.reason == "robots.txt disallowed":
            operational_logger.warning("[%s] [SKIPPED] not allowed by robots.txt.", name)
        elif raw.reason == "bot-detection":
            operational_logger.warning("[%s] [SKIPPED] bot-detection triggered (Tier 3 hard-stop).", name)
        else:
            operational_logger.warning("[%s] [SKIPPED] rate-limited this cycle, will retry next run.", name)
        return

    current_jobs = result["current_jobs"]
    ambiguous_jobs = result["ambiguous_jobs"]
    new_jobs = result["new_jobs"]
    new_ambiguous_jobs = result["new_ambiguous_jobs"]

    log_audit_event("CLASSIFY", ats=ats, company=name, match_count=len(current_jobs), ambiguous_count=len(ambiguous_jobs), new_count=len(new_jobs))

    if new_jobs:
        operational_logger.info("[%s] %d new matching posting(s):", name, len(new_jobs))
        for job in new_jobs:
            operational_logger.info("[%s]  - %s | %s", name, job['title'], job['location'])
            all_new_jobs.append({**job, "company": name})
    else:
        operational_logger.info("[%s] No new matching postings (%d match total, unchanged).", name, len(current_jobs))

    if new_ambiguous_jobs:
        operational_logger.info("[%s] %d posting(s) with unclear location/date. Please check manually:", name, len(new_ambiguous_jobs))
        for job in new_ambiguous_jobs:
            operational_logger.info("[%s]  ? %s | %s", name, job['title'], job['location'])
            all_ambiguous_jobs.append({**job, "company": name})

    previous_record = result["previous_record"]
    if "details" not in previous_record and (
        previous_record.get("matched_ids") or previous_record.get("ambiguous_ids")
    ):
        operational_logger.info("[%s] [MIGRATE] pre-v3.0 record found, backfilling match dates", name)
    seen_jobs[name] = build_company_record(result["company"], current_jobs, ambiguous_jobs, previous_record)

def main():
    setup_logging()
    companies, filters = load_config()
    seen_jobs = load_seen_jobs()

    all_new_jobs = []       # relevant new postings, across all companies
    all_ambiguous_jobs = [] # postings that passed keywords but location/date is unclear

    # Fetch and classify every company concurrently. Each worker only reads
    # seen_jobs for its own name; aggregation and logging happen in this main
    # thread after each future completes, so no locking is needed on the
    # shared lists or the seen_jobs dict.
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_CONCURRENT_COMPANIES) as executor:
        future_to_name = {
            executor.submit(check_company, company, filters, seen_jobs): company["name"]
            for company in companies
        }
        for future in concurrent.futures.as_completed(future_to_name):
            name = future_to_name[future]
            try:
                result = future.result()
            except Exception as e:
                operational_logger.error("  [ERROR] Failed to check %s: %s", name, e)
                continue
            log_company_result(result, all_new_jobs, all_ambiguous_jobs, seen_jobs)

    save_seen_jobs(seen_jobs)
 
    operational_logger.info("")
    operational_logger.info("%s", "=" * 50)
    if all_new_jobs:
        operational_logger.info("SUMMARY: %d new matching posting(s) across all companies:", len(all_new_jobs))
        operational_logger.info("")
        for job in all_new_jobs:
            operational_logger.info("[%s] %s | %s", job['company'], job['title'], job['location'])
            operational_logger.info("  %s", job['link'])
            operational_logger.info("")
    else:
        operational_logger.info("SUMMARY: No new matching postings this run.")

    if all_ambiguous_jobs:
        operational_logger.info("")
        operational_logger.info("%d posting(s) need a manual look (unclear location/date):", len(all_ambiguous_jobs))
        operational_logger.info("")
        for job in all_ambiguous_jobs:
            operational_logger.info("[%s] %s | %s", job['company'], job['title'], job['location'])
            operational_logger.info("  %s", job['link'])
            operational_logger.info("")

    flagged_companies = skip_tracker.get_flagged()
    if flagged_companies:
        operational_logger.info("")
        operational_logger.info("%d company(ies) repeatedly skipped:", len(flagged_companies))
        for company_name, streak in sorted(flagged_companies.items(), key=lambda x: -x[1]):
            operational_logger.info("  - %s: skipped %d cycles in a row", company_name, streak)
 
    send_notification(all_new_jobs, all_ambiguous_jobs, flagged_companies)
 
 
if __name__ == "__main__":
    main()