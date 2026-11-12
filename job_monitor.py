# Multi-company job monitor.
 
# Reads config.json for the list of companies to check. For each company,
# calls the connector matching its "ats" field, fetches current postings,
# compares against what was saved last run, and reports anything new.
 
# Usage:
#     python job_monitor.py
#     python job_monitor.py --config demo_config.json --db-path demo.db
#
# Scheduling (v4.0): .github/workflows/monitor.yml runs this script every
# 12 hours on GitHub Actions. The exit code is 0 for a clean run and 1 when
# the email failed, so a broken notification shows up as a failed workflow.
 
import argparse
import concurrent.futures
import json
import logging
import os
import sys
import time
import uuid
from datetime import datetime, timezone

import requests

from utils.audit_log import setup_logging, log_audit_event, set_run_id
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
from db import repository

operational_logger = logging.getLogger("job_monitor.operational")
 
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

skip_tracker = SkipTracker()
MAX_CONCURRENT_COMPANIES = 15
 
def load_config(config_path=None):
    with open(config_path or CONFIG_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data["companies"], data.get("filters", {"locations": [], "role_keywords": [], "domain_keywords": [], "exclude_keywords": []})


def parse_args(argv=None):
    # --config overrides which company/filter file is read (default: the
    # repo-anchored config.json). --db-path overrides where postings are
    # stored (default: DB_PATH from .env, else data/jobmonitor.db).
    parser = argparse.ArgumentParser(description="Multi-company job monitor")
    parser.add_argument(
        "--config",
        default=CONFIG_FILE,
        help="Path to the companies/filters config file (default: %(default)s)",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="Path to the SQLite database (default: DB_PATH env var or the repo default)",
    )
    return parser.parse_args(argv)
 
 
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
 
 
def check_company(company, filters, db_path):
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
    except requests.HTTPError as e:
        # A plain HTTP error (404, 500, etc.) from raise_for_status() in an
        # adapter. Bot-detection statuses (403, non-Workday 503) never reach
        # here: the rate limiter's check_hardstop() catches them first and
        # they arrive as a "bot-detection" SkipReason. So this is a normal
        # skip, not a Tier 3 hard-stop, and it counts toward the skip streak
        # so a company that keeps failing still gets flagged in the email.
        status_code = e.response.status_code if e.response is not None else "unknown"
        streak = skip_tracker.record_skip(name)
        operational_logger.warning("Skipping %s this cycle: HTTP %s error (streak: %d)", name, status_code, streak)
        raw_jobs = SkipReason("http-error", f"HTTP {status_code}")
    except Exception as e:
        log_audit_event("TIER3_HARDSTOP", ats=ats, company=name, reason=f"unexpected_error: {e}")
        return {"name": name, "ats": ats, "status": "error", "detail": str(e)}

    if isinstance(raw_jobs, SkipReason):
        log_audit_event("SKIP", ats=ats, company=name, reason=raw_jobs.reason, detail=raw_jobs.detail)
        return {"name": name, "ats": ats, "status": "skipped", "skip_reason": raw_jobs}

    # Split fetched jobs into: relevant matches, ambiguous, and the rest.
    # Only "match" and "ambiguous" jobs get persisted to the database =
    # anything clearly irrelevant (wrong location, wrong keyword) is dropped
    # here so it never counts toward "new" and never needs to be tracked.
    current_jobs = []
    ambiguous_jobs = []
    for job in raw_jobs:
        result = matches_filters(job, filters)
        if result == "match":
            current_jobs.append(job)
        elif result == "ambiguous":
            ambiguous_jobs.append(job)

    previous_ids = {r["job_id"]: r["tier"] for r in repository.list_jobs(company=name, db_path=db_path)}
    previous_matched_ids = {job_id for job_id, tier in previous_ids.items() if tier == "match"}
    previous_ambiguous_ids = {job_id for job_id, tier in previous_ids.items() if tier == "ambiguous"}

    new_jobs = [job for job in current_jobs if job["id"] not in previous_matched_ids]
    new_ambiguous_jobs = [job for job in ambiguous_jobs if job["id"] not in previous_ambiguous_ids]

    return {
        "name": name,
        "ats": ats,
        "status": "ok",
        "current_jobs": current_jobs,
        "ambiguous_jobs": ambiguous_jobs,
        "new_jobs": new_jobs,
        "new_ambiguous_jobs": new_ambiguous_jobs,
    }


def log_company_result(result, all_new_jobs, all_ambiguous_jobs, db_path):
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
        elif raw.reason == "http-error":
            operational_logger.warning("[%s] [SKIPPED] %s from the ATS, will retry next run.", name, raw.detail)
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

    now_iso = datetime.now(timezone.utc).isoformat()
    for job in current_jobs:
        repository.mark_job_seen(
            job_id=job["id"],
            company=name,
            title=job.get("title", "Untitled"),
            location=job.get("location", "Unknown"),
            url=job.get("link", ""),
            ats_platform=ats,
            tier="match",
            posted=job.get("posted", ""),
            posted_days_ago=job.get("posted_days_ago"),
            first_seen_at=now_iso,
            db_path=db_path,
        )
    for job in ambiguous_jobs:
        repository.mark_job_seen(
            job_id=job["id"],
            company=name,
            title=job.get("title", "Untitled"),
            location=job.get("location", "Unknown"),
            url=job.get("link", ""),
            ats_platform=ats,
            tier="ambiguous",
            posted=job.get("posted", ""),
            posted_days_ago=job.get("posted_days_ago"),
            first_seen_at=now_iso,
            db_path=db_path,
        )

def run_cycle(companies, filters, db_path):
    # One full monitoring cycle: fetch, classify, persist, notify. Returns a
    # summary dict for the RUN_END audit event. A failed email does not raise:
    # it is audited as NOTIFY_FAILED and reported through the summary, so the
    # scheduled workflow still saves this run's dedup state afterwards.
    all_new_jobs = []       # relevant new postings, across all companies
    all_ambiguous_jobs = [] # postings that passed keywords but location/date is unclear

    # Fetch and classify every company concurrently. Each worker only reads
    # dedup state for its own name from the shared database; aggregation,
    # logging, and persistence happen in this main thread after each future
    # completes, so no locking is needed on the shared lists.
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_CONCURRENT_COMPANIES) as executor:
        future_to_name = {
            executor.submit(check_company, company, filters, db_path): company["name"]
            for company in companies
        }
        for future in concurrent.futures.as_completed(future_to_name):
            name = future_to_name[future]
            try:
                result = future.result()
            except Exception as e:
                operational_logger.error("  [ERROR] Failed to check %s: %s", name, e)
                continue
            log_company_result(result, all_new_jobs, all_ambiguous_jobs, db_path)
 
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
 
    notify_ok = True
    try:
        send_notification(all_new_jobs, all_ambiguous_jobs, flagged_companies)
    except Exception as e:
        notify_ok = False
        log_audit_event("NOTIFY_FAILED", error_type=type(e).__name__, error=str(e)[:500])
        operational_logger.error("[NOTIFY] Email notification failed: %s", e)

    return {
        "notify_ok": notify_ok,
        "new_count": len(all_new_jobs),
        "ambiguous_count": len(all_ambiguous_jobs),
        "flagged_count": len(flagged_companies),
        "companies_checked": len(companies),
    }


def new_run_id():
    # Inside GitHub Actions, reuse the workflow run id so an audit event can
    # be traced straight back to its Actions run. Locally, a random short id.
    github_run_id = os.environ.get("GITHUB_RUN_ID")
    if github_run_id:
        return f"gh-{github_run_id}-{os.environ.get('GITHUB_RUN_ATTEMPT', '1')}"
    return f"local-{uuid.uuid4().hex[:12]}"


def main(argv=None):
    # Returns a process exit code: 0 for a clean run, 1 when the email
    # failed. An unexpected crash is audited as RUN_FAILED and re-raised.
    # RUN_START and RUN_END bracket every run, so the dashboard can show when
    # the last run finished and warn when the schedule has gone quiet.
    setup_logging()
    args = parse_args(argv)
    run_id = new_run_id()
    set_run_id(run_id)
    started = time.monotonic()
    log_audit_event("RUN_START", trigger=os.environ.get("GITHUB_EVENT_NAME", "manual"))

    status = "failed"
    summary = {}
    try:
        companies, filters = load_config(args.config)
        if args.db_path:
            # Adapters and the skip tracker resolve the database through
            # repository.get_db_path(), which reads DB_PATH. Setting it here
            # makes --db-path apply to skip streaks too, not just job
            # postings, so a demo run never writes streaks into the real
            # database.
            os.environ["DB_PATH"] = args.db_path
        db_path = args.db_path or str(repository.get_db_path())

        summary = run_cycle(companies, filters, db_path)
        status = "ok" if summary["notify_ok"] else "notify_failed"
    except Exception as e:
        log_audit_event("RUN_FAILED", error_type=type(e).__name__, error=str(e)[:500])
        raise
    finally:
        log_audit_event(
            "RUN_END",
            status=status,
            duration_seconds=round(time.monotonic() - started, 1),
            **{key: value for key, value in summary.items() if key != "notify_ok"},
        )
        set_run_id(None)

    return 0 if status == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())