# One fetch function per ATS type. Every function returns a list of dicts
# in the SAME shape, regardless of which ATS it came from:

#     {
#         "id": <stable unique string>,
#         "title": <job title>,
#         "location": <location text>,
#         "posted": <posted date text, or "" if unavailable>,
#         "posted_days_ago": <int, or None if age can't be confirmed>,
#         "link": <full URL to the job posting>
#     }

# This is what lets monitor.py treat every company the same way, no matter
# which ATS it uses under the hood.

import requests
from date_utils import days_ago_from_iso, days_ago_from_unix_ms, days_ago_from_workday_text


def fetch_greenhouse(company):
    # Greenhouse: single GET request, no pagination needed.
    slug = company["slug"]
    url = f"https://api.greenhouse.io/v1/boards/{slug}/jobs"

    response = requests.get(url, timeout=15)
    response.raise_for_status()
    data = response.json()

    jobs = []
    for job in data.get("jobs", []):
        jobs.append({
            "id": str(job.get("id")),
            "title": job.get("title", "Untitled"),
            "location": job.get("location", {}).get("name", "Unknown"),
            "posted": job.get("updated_at", ""),
            "posted_days_ago": days_ago_from_iso(job.get("updated_at")),
            "link": job.get("absolute_url", "")
        })
    return jobs


def fetch_lever(company):
    # Lever: single GET request, no pagination needed.
    slug = company["slug"]
    url = f"https://api.lever.co/v0/postings/{slug}"

    response = requests.get(url, timeout=15)
    response.raise_for_status()
    data = response.json()

    jobs = []
    for job in data:
        jobs.append({
            "id": job.get("id", ""),
            "title": job.get("text", "Untitled"),
            "location": job.get("categories", {}).get("location", "Unknown"),
            "posted": str(job.get("createdAt", "")),
            "posted_days_ago": days_ago_from_unix_ms(job.get("createdAt")),
            "link": job.get("hostedUrl", "")
        })
    return jobs


def fetch_ashby(company):
    # Ashby: single GET request, no pagination needed.
    slug = company["slug"]
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"

    response = requests.get(url, timeout=15)
    response.raise_for_status()
    data = response.json()

    jobs = []
    for job in data.get("jobs", []):
        jobs.append({
            "id": job.get("id", ""),
            "title": job.get("title", "Untitled"),
            "location": job.get("location", "Unknown"),
            "posted": job.get("publishedAt", ""),
            "posted_days_ago": days_ago_from_iso(job.get("publishedAt")),
            "link": job.get("jobUrl", "")
        })
    return jobs


def fetch_smartrecruiters(company):
    # SmartRecruiters: public Posting API, GET, paginated via offset/limit,
    # confirmed no-auth-required for this specific endpoint per their docs.

    # Pagination advances by however many postings actually came back, not
    # by the requested page_size, in case the server ever returns fewer
    # than asked for.
    slug = company["slug"]
    requested_page_size = 100
    offset = 0
    all_postings = []

    while True:
        url = (f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
               f"?offset={offset}&limit={requested_page_size}")
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        data = response.json()

        postings = data.get("content", [])
        all_postings.extend(postings)

        total_found = data.get("totalFound", 0)
        offset += len(postings)

        if offset >= total_found or not postings:
            break

    jobs = []
    for job in all_postings:
        location = job.get("location", {})
        location_text = ", ".join(filter(None, [
            location.get("city"), location.get("region"), location.get("country")
        ])) or "Unknown"

        jobs.append({
            "id": job.get("id", ""),
            "title": job.get("name", "Untitled"),
            "location": location_text,
            "posted": job.get("releasedDate", ""),
            "posted_days_ago": days_ago_from_iso(job.get("releasedDate")),
            "link": job.get("ref", "")
        })
    return jobs


def fetch_recruitee(company):
    # Recruitee: single GET request, no pagination needed.
    slug = company["slug"]
    url = f"https://{slug}.recruitee.com/api/offers/"

    response = requests.get(url, timeout=15)
    response.raise_for_status()
    data = response.json()

    jobs = []
    for job in data.get("offers", []):
        jobs.append({
            "id": str(job.get("id", "")),
            "title": job.get("title", "Untitled"),
            "location": job.get("city", "Unknown") or "Unknown",
            "posted": job.get("created_at", ""),
            "posted_days_ago": days_ago_from_iso(job.get("created_at")),
            "link": job.get("careers_url", "")
        })
    return jobs


def fetch_workable(company):
    # Workable: POST request, cursor-based pagination (not offset-based).
    # Each response includes a "nextPage" token; feeding it back as "token"
    # in the next request's body is how you get the next page (confirmed
    # via DevTools against a real multi-page company).
    slug = company["slug"]
    url = f"https://apply.workable.com/api/v3/accounts/{slug}/jobs"

    all_results = []
    body = {"query": "", "department": [], "location": [], "workplace": [], "worktype": []}

    while True:
        response = requests.post(url, headers={"Content-Type": "application/json"},
                                  json=body, timeout=15)
        response.raise_for_status()
        data = response.json()

        results = data.get("results", [])
        all_results.extend(results)

        next_page = data.get("nextPage")
        if not next_page or not results:
            break
        body["token"] = next_page

    jobs = []
    for job in all_results:
        location = job.get("location", {})
        location_text = ", ".join(filter(None, [
            location.get("city"), location.get("region"), location.get("country")
        ])) or "Unknown"

        shortcode = job.get("shortcode", "")
        jobs.append({
            "id": str(job.get("id", "")),
            "title": job.get("title", "Untitled"),
            "location": location_text,
            "posted": job.get("published", ""),
            "posted_days_ago": days_ago_from_iso(job.get("published")),
            "link": f"https://apply.workable.com/{slug}/j/{shortcode}/"
        })
    return jobs


def fetch_personio(company):
    # Personio: single GET request via the /search.json endpoint, no auth.

    # Two known gaps in this feed:
    # 1. No date field exists at all - postings can never get a confirmed
    #    age, so they'll always land in the "ambiguous" bucket once
    #    max_age_days filtering runs. Not a bug, just what this feed offers.
    # 2. No link field - the URL is built from job id using Personio's known
    #    pattern, but this hasn't been click-tested to confirm it resolves.
    # Also assumes no pagination exists (no total/next-page field seen in
    # a real response) - worth revisiting if a large company returns
    # suspiciously few results here.
    slug = company["slug"]
    domain = company.get("domain", "de")  # some tenants use .com instead of .de
    url = f"https://{slug}.jobs.personio.{domain}/search.json"

    response = requests.get(url, timeout=15)
    response.raise_for_status()
    data = response.json()

    jobs = []
    for job in data:
        jobs.append({
            "id": str(job.get("id", "")),
            "title": job.get("name", "Untitled"),
            "location": job.get("office", "Unknown"),
            "posted": "",
            "posted_days_ago": None,
            "link": f"https://{slug}.jobs.personio.{domain}/job/{job.get('id', '')}"
        })
    return jobs


def fetch_workday(company):
    # Workday: POST request, paginated. Loops through offsets until
    # all postings are collected. This is the pattern proven working
    # on Ensign's endpoint.
    url = company["workday_url"]
    job_base_url = company.get("job_base_url", "")
    page_size = 20
    offset = 0
    raw_jobs = []

    while True:
        body = {
            "appliedFacets": {},
            "limit": page_size,
            "offset": offset,
            "searchText": ""
        }

        response = requests.post(
            url,
            headers={"Content-Type": "application/json"},
            json=body,
            timeout=15
        )
        response.raise_for_status()
        data = response.json()

        postings = data.get("jobPostings", [])
        raw_jobs.extend(postings)

        total = data.get("total", 0)
        offset += page_size

        if offset >= total or not postings:
            break

    jobs = []
    for job in raw_jobs:
        external_path = job.get("externalPath", "")
        jobs.append({
            "id": external_path,
            "title": job.get("title", "Untitled"),
            "location": job.get("locationsText", "Unknown"),
            "posted": job.get("postedOn", ""),
            "posted_days_ago": days_ago_from_workday_text(job.get("postedOn")),
            "link": job_base_url + external_path
        })
    return jobs


# Maps the "ats" field in config.json to the right function above.
# This is the whole trick that makes the system generic: monitor.py
# just looks up this dict instead of having an if/elif per ATS.
CONNECTORS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "smartrecruiters": fetch_smartrecruiters,
    "recruitee": fetch_recruitee,
    "workable": fetch_workable,
    "personio": fetch_personio,
    "workday": fetch_workday,
}