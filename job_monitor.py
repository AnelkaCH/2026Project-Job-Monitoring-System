# Multi-company job monitor.
 
# Reads config.json for the list of companies to check. For each company,
# calls the connector matching its "ats" field, fetches current postings,
# compares against what was saved last run, and reports anything new.
 
# Usage:
#     python monitor.py
 
import json
import os
 
from connectors import CONNECTORS
 
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
SEEN_JOBS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seen_jobs.json")
 
 
def load_config():
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data["companies"], data.get("filters", {"locations": [], "keywords": []})
 
 
def matches_filters(job, filters):
    # Returns "match", "ambiguous", or "no_match".
 
    # match      - location filter passes AND keyword filter passes
    # ambiguous  - location text doesn't clearly confirm or rule out a match
    #              (e.g. "2 Locations" instead of a named place) so it's
    #              surfaced separately rather than silently dropped
    # no_match   - clearly doesn't belong on either filter

    location_text = job.get("location", "").lower()
    title_text = job.get("title", "").lower()
 
    wanted_locations = [loc.lower() for loc in filters.get("locations", [])]
    wanted_keywords = [kw.lower() for kw in filters.get("keywords", [])]
    max_age_days = filters.get("max_age_days")
 
    # No location filter configured -> treat every location as a pass
    if not wanted_locations:
        location_match = True
        location_ambiguous = False
    else:
        location_match = any(loc in location_text for loc in wanted_locations)
        # Text like "2 Locations" or "Multiple Locations" doesn't name a place at all, so we can't confirm OR rule it out from title text.
        location_ambiguous = (not location_match) and ("location" in location_text)
 
    keyword_match = (not wanted_keywords) or any(kw in title_text for kw in wanted_keywords)
 
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
    # Structure: { "Company Name": ["job_id_1", "job_id_2", ...], ... }
    # Keyed by company so each company's history is tracked independently.

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
 
        if ats not in CONNECTORS:
            print(f"[SKIP] {name}: unknown ATS type '{ats}'")
            continue
 
        print(f"Checking {name} ({ats})...")
 
        try:
            raw_jobs = CONNECTORS[ats](company)
        except Exception as e:
            # One company failing (site down, endpoint changed) shouldn't crash the whole run and block checking every other company.
            print(f"  [ERROR] Failed to fetch {name}: {e}")
            continue
 
        # Split fetched jobs into: relevant matches, ambiguous, and the rest.
        # Only "match" and "ambiguous" jobs get saved to seen_jobs
        current_jobs = []
        ambiguous_jobs = []
        for job in raw_jobs:
            result = matches_filters(job, filters)
            if result == "match":
                current_jobs.append(job)
            elif result == "ambiguous":
                ambiguous_jobs.append(job)
 
        previous_ids = set(seen_jobs.get(name, []))
        current_ids = {job["id"] for job in current_jobs}
 
        new_jobs = [job for job in current_jobs if job["id"] not in previous_ids]
 
        if name not in seen_jobs:
            print(f"  First run for {name} - saved {len(current_jobs)} matching postings as baseline "
                  f"(out of {len(raw_jobs)} total postings fetched).")
        elif new_jobs:
            print(f"  {len(new_jobs)} new matching posting(s):")
            for job in new_jobs:
                print(f"   - {job['title']} | {job['location']}")
                all_new_jobs.append({**job, "company": name})
        else:
            print(f"  No new matching postings ({len(current_jobs)} match total, unchanged).")
 
        if ambiguous_jobs:
            print(f"  {len(ambiguous_jobs)} posting(s) with unclear location/date - check manually:")
            for job in ambiguous_jobs:
                print(f"   ? {job['title']} | {job['location']}")
                all_ambiguous_jobs.append({**job, "company": name})
 
        seen_jobs[name] = list(current_ids)
 
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
 
 
if __name__ == "__main__":
    main()