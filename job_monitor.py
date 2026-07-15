# Multi-company job monitor.
 
# Reads config.json for the list of companies to check. For each company,
# calls the connector matching its "ats" field, fetches current postings,
# compares against what was saved last run, and reports anything new.
 
# Usage:
#     python job_monitor.py
 
import json
import os
 
from connectors import CONNECTORS
from custom_handlers import CUSTOM_HANDLERS
from notifier import send_notification
from skip_tracker import SkipTracker
 
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
SEEN_JOBS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seen_jobs.json")

skip_tracker = SkipTracker()
 
def load_config():
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data["companies"], data.get("filters", {"locations": [], "keywords": []})
 
 
def matches_filters(job, filters):
    # Returns "match", "ambiguous", or "no_match" = now checking THREE things:
    # location, keywords, and age. All three must pass for "match".
    # If keywords or location clearly fail -> no_match, full stop.
    # If age can't be confirmed (e.g. Workday's "30+ Days Ago") but nothing
    # else fails -> ambiguous, same treatment as an unclear location.
 
    location_text = job.get("location", "").lower()
    title_text = job.get("title", "").lower()
 
    wanted_locations = [loc.lower() for loc in filters.get("locations", [])]
    wanted_keywords = [kw.lower() for kw in filters.get("keywords", [])]
    exclude_keywords = [kw.lower() for kw in filters.get("exclude_keywords", [])]
    max_age_days = filters.get("max_age_days")
 
    # Exclude list wins over everything - if a title matches an excluded
    # term (e.g. "HR Analyst" contains "analyst"), reject immediately,
    # even if it would otherwise pass on a generic keyword match.
    if any(kw in title_text for kw in exclude_keywords):
        return "no_match"
 
    if not wanted_locations:
        location_match = True
        location_ambiguous = False
    else:
        location_match = any(loc in location_text for loc in wanted_locations)
        location_ambiguous = (not location_match) and ("location" in location_text)
 
    keyword_match = (not wanted_keywords) or any(kw in title_text for kw in wanted_keywords)
 
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
 
    if not keyword_match:
        return "no_match"
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
 
 
def main():
    companies, filters = load_config()
    seen_jobs = load_seen_jobs()
 
    all_new_jobs = []       # relevant new postings, across all companies
    all_ambiguous_jobs = [] # postings that passed keywords but location is unclear
 
    for company in companies:
        name = company["name"]
        ats = company["ats"]
 
        if ats == "custom":
            handler_name = company.get("handler")
            fetch_function = CUSTOM_HANDLERS.get(handler_name)
            if fetch_function is None:
                print(f"[SKIP] {name}: no custom handler named '{handler_name}'")
                continue
        elif ats in CONNECTORS:
            fetch_function = CONNECTORS[ats]
        else:
            print(f"[SKIP] {name}: unknown ATS type '{ats}'")
            continue
 
        print(f"Checking {name} ({ats})...")
 
        try:
            raw_jobs = fetch_function(company)
        except Exception as e:
            # One company failing (site down, endpoint changed) shouldn't
            # crash the whole run and block checking every other company.
            print(f"  [ERROR] Failed to fetch {name}: {e}")
            continue

        if raw_jobs is None:
            # Connector was rate-limited into exhaustion and skipped this
            # company for the cycle (see connectors.py / rate_limiter.py).
            # Continuing here is deliberate: it skips the seen_jobs[name]
            # assignment below, so the company's last real baseline stays
            # intact instead of being overwritten with empty lists. If we
            # let this fall through, next cycle's dedup would think every
            # existing posting for this company is "new" again.
            print(f"  [SKIPPED] {name}: rate-limited this cycle, will retry next run.")
            continue
 
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
 
        previous_matched_ids = set(seen_jobs.get(name, {}).get("matched_ids", []))
        previous_ambiguous_ids = set(seen_jobs.get(name, {}).get("ambiguous_ids", []))
 
        current_matched_ids = {job["id"] for job in current_jobs}
        current_ambiguous_ids = {job["id"] for job in ambiguous_jobs}
 
        new_jobs = [job for job in current_jobs if job["id"] not in previous_matched_ids]
        new_ambiguous_jobs = [job for job in ambiguous_jobs if job["id"] not in previous_ambiguous_ids]
 
        if new_jobs:
            print(f"  {len(new_jobs)} new matching posting(s):")
            for job in new_jobs:
                print(f"   - {job['title']} | {job['location']}")
                all_new_jobs.append({**job, "company": name})
        else:
            print(f"  No new matching postings ({len(current_jobs)} match total, unchanged).")
 
        if new_ambiguous_jobs:
            # Only report ambiguous postings not seen in a previous run - same
            # dedup treatment as matches, so these don't re-appear every run.
            print(f"  {len(new_ambiguous_jobs)} posting(s) with unclear location/date. Please check manually:")
            for job in new_ambiguous_jobs:
                print(f"   ? {job['title']} | {job['location']}")
                all_ambiguous_jobs.append({**job, "company": name})
 
        seen_jobs[name] = {
            "matched_ids": list(current_matched_ids),
            "ambiguous_ids": list(current_ambiguous_ids)
        }
 
    save_seen_jobs(seen_jobs)
 
    print("\n" + "=" * 50)
    if all_new_jobs:
        print(f"SUMMARY: {len(all_new_jobs)} new matching posting(s) across all companies:\n")
        for job in all_new_jobs:
            print(f"[{job['company']}] {job['title']} | {job['location']}")
            print(f"  {job['link']}\n")
    else:
        print("SUMMARY: No new matching postings this run.")
 
    if all_ambiguous_jobs:
        print(f"\n{len(all_ambiguous_jobs)} posting(s) need a manual look (unclear location/date):\n")
        for job in all_ambiguous_jobs:
            print(f"[{job['company']}] {job['title']} | {job['location']}")
            print(f"  {job['link']}\n")

    flagged_companies = skip_tracker.get_flagged()
    if flagged_companies:
        print(f"\n{len(flagged_companies)} company(ies) repeatedly rate-limited:\n")
        for company_name, streak in sorted(flagged_companies.items(), key=lambda x: -x[1]):
            print(f"  - {company_name}: skipped {streak} cycles in a row")
 
    send_notification(all_new_jobs, all_ambiguous_jobs, flagged_companies)
 
 
if __name__ == "__main__":
    main()