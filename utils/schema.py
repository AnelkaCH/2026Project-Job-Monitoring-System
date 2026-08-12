# Input validation and sanitization for ATS API responses.
#
# v3.1: every adapter validates the raw response each ATS actually returns,
# right after the HTTP call and before normalization. Two gates run:
#
#   1. Per-ATS item models below validate the response shape and sanitize
#      any HTML-bearing description fields with bleach. Tags are STRIPPED
#      (not escaped), so raw markup never survives into the normalized dict
#      or seen_jobs.json.
#   2. validate_job_posting() runs on the normalized 6-field dict each
#      adapter builds. It validates the link is https and pointed at the
#      ATS's expected domain, and re-cleans the stored text fields.
#
# Anything that fails either gate is audited via log_audit_event() with
# event_type="VALIDATION_REJECTED" and dropped, never stored silently.

import bleach
from typing import Annotated, Any, Dict, List, Optional, Union
from urllib.parse import urlparse

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
)

from utils.audit_log import log_audit_event


def _none_to_string(value):
    # Raw optional strings become "" instead of None so the adapters'
    # existing .get("field", default) fallbacks keep working unchanged.
    return "" if value is None else value


def _clean_markup(value):
    # Strip tags, keep the text. No tags are allowed, so attributes are
    # dropped along with them.
    if not value:
        return value
    return bleach.clean(str(value), tags=[], strip=True).strip()


def _as_mapping(value):
    # Location-like fields are always dicts downstream; coerce garbage to {}
    # so the adapters' .get() chains keep working.
    return value if isinstance(value, dict) else {}


# Optional raw strings become "" and all text has markup stripped.
CleanStr = Annotated[str, BeforeValidator(_none_to_string), AfterValidator(_clean_markup)]

# Required string with markup stripped; None still fails validation.
Markup = Annotated[str, AfterValidator(_clean_markup)]

# Always-a-dict field, coerced from nothing/None/stray scalars.
MappingStr = Annotated[Dict[str, Any], BeforeValidator(_as_mapping)]


# Per-ATS raw response item models. Each mirrors the fields its adapter
# actually reads, plus any HTML-bearing description field so markup is
# sanitized before normalization. extra="ignore" means unknown response
# fields don't crash the whole pipeline when an ATS adds new ones.

class GreenhouseJob(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: Union[str, int]
    title: Markup
    location: MappingStr = Field(default_factory=dict)
    updated_at: CleanStr = ""
    absolute_url: CleanStr = ""
    content: CleanStr = ""


class LeverJob(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: Union[str, int]
    text: Markup
    categories: MappingStr = Field(default_factory=dict)
    createdAt: Union[str, int] = ""
    hostedUrl: CleanStr = ""
    description: CleanStr = ""
    descriptionPlain: CleanStr = ""


class AshbyJob(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: Union[str, int]
    title: Markup
    location: CleanStr = "Unknown"
    publishedAt: CleanStr = ""
    jobUrl: CleanStr = ""
    descriptionHtml: CleanStr = ""


class SmartRecruitersJob(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: Union[str, int]
    name: Markup
    location: MappingStr = Field(default_factory=dict)
    releasedDate: CleanStr = ""
    ref: CleanStr = ""


class RecruiteeJob(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: Union[str, int]
    title: Markup
    city: CleanStr = "Unknown"
    created_at: CleanStr = ""
    careers_url: CleanStr = ""
    content: CleanStr = ""


class WorkableJob(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: Union[str, int]
    title: Markup
    location: MappingStr = Field(default_factory=dict)
    published: CleanStr = ""
    shortcode: CleanStr = ""
    description: CleanStr = ""


class PersonioJob(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: Union[str, int]
    name: Markup
    office: CleanStr = "Unknown"
    jobDescriptions: Optional[List[Dict[str, Any]]] = None

    @field_validator("jobDescriptions", mode="before")
    @classmethod
    def _clean_descriptions(cls, value):
        # Personio ships job descriptions as a list of {plain, richText}
        # dicts; sanitize the text/HTML in each one so no markup survives.
        if not isinstance(value, list):
            return value
        cleaned = []
        for entry in value:
            entry = dict(entry) if isinstance(entry, dict) else entry
            for key in ("plain", "richText"):
                if isinstance(entry.get(key), str):
                    entry[key] = _clean_markup(entry[key])
            cleaned.append(entry)
        return cleaned


class WorkdayJob(BaseModel):
    model_config = ConfigDict(extra="ignore")
    externalPath: Markup
    title: Markup
    locationsText: CleanStr = "Unknown"
    postedOn: CleanStr = ""


class SapJob(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: Union[str, int]
    unifiedStandardTitle: CleanStr = ""
    title: CleanStr = ""
    jobLocationCountry: Optional[List[str]] = None
    urlTitle: CleanStr = ""
    unifiedUrlTitle: CleanStr = ""
    unifiedStandardStart: CleanStr = ""


class SapPayload(BaseModel):
    # SAP wraps each job under a "response" key inside jobSearchResult.
    model_config = ConfigDict(extra="ignore")
    response: Optional[SapJob] = None


# Expected link domains per ATS. Entries starting with "." are subdomain
# suffixes (e.g. ".myworkdayjobs.com" covers visa.wd5.myworkdayjobs.com).
# Workday/Personio links are constructed from config, and SAP uses the
# company's own careers domain, so those are supplied at call time when the
# fixed entries don't apply.
ALLOWED_LINK_DOMAINS = {
    "greenhouse": ("boards.greenhouse.io", "job-boards.greenhouse.io"),
    "lever": ("jobs.lever.co",),
    "ashby": ("jobs.ashbyhq.com",),
    "smartrecruiters": ("jobs.smartrecruiters.com",),
    "recruitee": (".recruitee.com",),
    "workable": ("apply.workable.com",),
    "personio": (".jobs.personio.de", ".jobs.personio.com"),
    "workday": (".myworkdayjobs.com",),
}


def is_valid_job_url(url, allowed_domains):
    # Scheme must be https and the host must match one of the expected
    # ATS domains (exact, or subdomain suffix for "."-prefixed entries).
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    return any(
        host.endswith(entry) if entry.startswith(".") else host == entry
        for entry in allowed_domains
    )


def validate_raw_jobs(items, model_cls, ats, company):
    # Validate each raw response item against the ATS's model before
    # normalization. Returns only the items that parse; rejected ones are
    # audited and dropped, never silently passed through.
    valid = []
    for item in items or []:
        try:
            valid.append(model_cls.model_validate(item).model_dump())
        except ValidationError as exc:
            log_audit_event(
                "VALIDATION_REJECTED",
                ats=ats,
                company=company,
                reason="response_shape",
                detail=str(exc)[:500],
            )
    return valid


class JobPosting(BaseModel):
    # The shared normalized schema written to seen_jobs.json, kept in the
    # codebase's existing 6-field shape (id, title, location, posted,
    # posted_days_ago, link). Stored text is re-cleaned here as a second
    # pass, so storage never holds raw markup even if a future adapter
    # skips the raw-response layer upstream.
    model_config = ConfigDict(extra="ignore")
    id: str
    title: Markup
    location: Markup
    posted: CleanStr = ""
    posted_days_ago: Optional[int] = None
    link: CleanStr = ""


def validate_job_posting(raw, ats, company, allowed_domains=None):
    # Final gate on the normalized 6-field dict: re-cleanse stored text,
    # enforce the https + expected-domain URL policy, and audit + drop
    # anything that fails. An empty link passes, matching the pre-v3.1
    # behavior of storing "" when an ATS exposes no URL.
    try:
        posting = JobPosting(**raw)
    except ValidationError as exc:
        log_audit_event(
            "VALIDATION_REJECTED",
            ats=ats,
            company=company,
            reason="schema_violation",
            detail=str(exc)[:500],
        )
        return None

    link = posting.link
    if link:
        domains = (
            allowed_domains
            if allowed_domains is not None
            else ALLOWED_LINK_DOMAINS.get(ats, ())
        )
        if not is_valid_job_url(link, domains):
            log_audit_event(
                "VALIDATION_REJECTED",
                ats=ats,
                company=company,
                reason="url_rejected",
                url=link,
            )
            return None

    return posting.model_dump()