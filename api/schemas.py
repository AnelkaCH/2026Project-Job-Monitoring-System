from pydantic import BaseModel, Field, model_validator


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
    # Each filter list is updated independently, since they mean different
    # things to the classifier: a title needs a role AND a domain keyword,
    # and any exclude keyword rejects it outright. Lists left out of the
    # request keep their current value in config.json. Role and domain lists
    # must be non-empty when sent; exclude may be [] to clear it.
    role_keywords: list[str] | None = Field(default=None, min_length=1)
    domain_keywords: list[str] | None = Field(default=None, min_length=1)
    exclude_keywords: list[str] | None = None

    @model_validator(mode="after")
    def _at_least_one_list(self):
        if self.role_keywords is None and self.domain_keywords is None and self.exclude_keywords is None:
            raise ValueError("provide at least one of role_keywords, domain_keywords, exclude_keywords")
        return self
