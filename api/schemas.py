from pydantic import BaseModel, Field


class JobOut(BaseModel):
    company: str
    job_id: str
    tier: str
    title: str
    location: str
    posted: str
    posted_days_ago: int | None = None
    url: str
    ats_platform: str
    first_seen_at: str


class CompanyStatusOut(BaseModel):
    name: str
    ats: str
    last_checked: str | None = None
    skip_streak: int = 0


class KeywordsIn(BaseModel):
    keywords: list[str] = Field(min_length=1)
