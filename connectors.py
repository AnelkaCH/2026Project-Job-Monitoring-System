# One fetch function per ATS type. Every function returns a list of dicts
# in the SAME shape, regardless of which ATS it came from:
 
#     {
#         "id": <stable unique string>,
#         "title": <job title>,
#         "location": <location text>,
#         "posted": <posted date text, or "" if unavailable>,
#         "link": <full URL to the job posting>
#     }
 
# This is what lets monitor.py treat every company the same way, no matter
# which ATS it uses under the hood.

 
import requests
 
 
def fetch_greenhouse(company):
    #Greenhouse: single GET request, no pagination needed.
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
            "link": job.get("absolute_url", "")
        })
    return jobs
 
 
def fetch_lever(company):
    #Lever: single GET request, no pagination needed.
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
            "link": job.get("hostedUrl", "")
        })
    return jobs
 
 
def fetch_ashby(company):
    #Ashby: single GET request, no pagination needed.
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
            "link": job.get("jobUrl", "")
        })
    return jobs
 
def fetch_smartrecruiters(company):
    # SmartRecruiters: public Posting API, GET, paginated via offset/limit,
    # confirmed no-auth-required for this specific endpoint per their docs.
    company_id = company["company_id"]
    page_size = 100
    offset = 0
    all_postings = []
 
    while True:
        url = (f"https://api.smartrecruiters.com/v1/companies/{company_id}/postings"
               f"?offset={offset}&limit={page_size}")
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        data = response.json()
 
        postings = data.get("content", [])
        all_postings.extend(postings)
 
        total_found = data.get("totalFound", 0)
        offset += page_size
 
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
            "link": job.get("ref", "")  # 'ref' holds the link to full posting details
        })
    return jobs
 
 
def fetch_recruitee(company):
    #Recruitee: single GET request, no pagination needed.
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
            "link": job.get("careers_url", "")
        })
    return jobs

def fetch_workday(company):
    # Workday: POST request, paginated. Loops through offsets until all postings are collected. 
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
            "id": external_path,  # contains the JOBREQ number, stable across runs
            "title": job.get("title", "Untitled"),
            "location": job.get("locationsText", "Unknown"),
            "posted": job.get("postedOn", ""),
            "link": job_base_url + external_path
        })
    return jobs
 
 
# Maps the "ats" field in config.json to the right function above.
# This is the whole trick that makes the system generic: monitor.py just looks up this dict instead of having an if/elif per ATS.
CONNECTORS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "smartrecruiters": fetch_smartrecruiters,
    "recruitee": fetch_recruitee,
    "workday": fetch_workday,
}