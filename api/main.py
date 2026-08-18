from fastapi import FastAPI

from api import schemas
from api.routes_companies import load_config, router as companies_router, save_config
from api.routes_jobs import router as jobs_router

app = FastAPI(title="Job Monitor API", version="3.3.2")
app.include_router(jobs_router)
app.include_router(companies_router)


@app.post("/config/keywords")
def update_keywords(body: schemas.KeywordsIn):
    # Writes the submitted keywords into every filter list so the monitor's
    # classification picks them up on the next run.
    data = load_config()
    filters = data.setdefault("filters", {})
    filters["role_keywords"] = list(body.keywords)
    filters["domain_keywords"] = list(body.keywords)
    filters["exclude_keywords"] = list(body.keywords)
    save_config(data)
    return {
        "role_keywords": filters["role_keywords"],
        "domain_keywords": filters["domain_keywords"],
        "exclude_keywords": filters["exclude_keywords"],
    }
