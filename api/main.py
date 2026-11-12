from fastapi import FastAPI

from api import schemas
from api.routes_companies import load_config, router as companies_router, save_config
from api.routes_jobs import router as jobs_router

app = FastAPI(title="Job Monitor API", version="3.3.2")
app.include_router(jobs_router)
app.include_router(companies_router)


@app.post("/config/keywords")
def update_keywords(body: schemas.KeywordsIn):
    # Writes only the filter lists present in the request, so the monitor's
    # classification picks them up on the next run. The same list must never
    # be copied into exclude_keywords: exclude wins over role/domain in
    # matches_filters(), so doing that would reject every matching title.
    data = load_config()
    filters = data.setdefault("filters", {})
    for field in ("role_keywords", "domain_keywords", "exclude_keywords"):
        value = getattr(body, field)
        if value is not None:
            filters[field] = list(value)
    save_config(data)
    return {
        "role_keywords": filters.get("role_keywords", []),
        "domain_keywords": filters.get("domain_keywords", []),
        "exclude_keywords": filters.get("exclude_keywords", []),
    }
