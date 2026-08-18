from fastapi import APIRouter, HTTPException

from api.schemas import JobOut
from db import repository

router = APIRouter()


@router.get("/jobs", response_model=list[JobOut])
def list_jobs(company: str | None = None, keyword: str | None = None):
    # Thin passthrough to the repository filter; an empty result set is a
    # valid 200 with an empty list, never an error.
    rows = repository.list_jobs(company=company, keyword=keyword)
    return [JobOut(**row) for row in rows]


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: str):
    row = repository.get_job(job_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"job '{job_id}' not found")
    return JobOut(**row)
