# Architecture

## Overview

The system follows an **adapter pattern**: a single orchestrator loop in `job_monitor.py` delegates to platform-specific adapter functions (Greenhouse, Lever, Ashby, etc.), each of which normalizes results into a shared 6-field job schema before they hit the classification layer. Every adapter routes HTTP calls through a shared rate limiter and checks robots.txt compliance before the first request. Since v3.1, every ATS response is also validated and sanitized by `utils/schema.py` before normalization, so untrusted external text and URLs are checked at the boundary.

The entrypoint is configurable since v3.3.2: `job_monitor.py` accepts `--config` and `--db-path` CLI flags (defaulting to the repo `config.json` and the `DB_PATH`-resolved database, so no-flag behavior is unchanged). The committed `demo_config.json` pairs with these flags so a fresh clone can run the monitor against real public Greenhouse and Lever endpoints (Airbnb, Coinbase, Reddit, Binance, Zoox) and write to a disposable `demo.db` without the private `config.json` or `.env`.

```
[Config/Scheduler] -> [Orchestrator (job_monitor.py)]
                           |
                +----------+-----------+
                |                      |
        [Adapter Layer]         [Custom Handlers]
        (9 standard ATS)       (company-specific)
                |                      |
        [Rate Limiter]  <---  [Robots Checker]
                |
        [Validation & Sanitization (utils/schema.py)]
                |
        [Classification / Filtering]
                |
        [Persistence (SQLite, db/repository.py)]
                |
        +-------+-------+
        |               |
[Email Notification]  [Dashboard (dashboard.py)]
```

## Key Components

### Adapter Layer

**What it does:** Each ATS platform gets its own module in `adapters/` (e.g., `adapters/greenhouse.py`, `adapters/lever.py`), each containing one fetch function that follows the same pattern: build the URL, check robots.txt, route through the rate limiter, parse the response, validate and sanitize it, normalize into the shared 6-field dict format, return a list of jobs or a `SkipReason`. Every raw response item and every normalized dict passes through the validation gates in `utils/schema.py` (see below) before it is stored.

`adapters/connectors.py` is a thin registry that imports each fetch function from its own module and assembles the `CONNECTORS` dict. `job_monitor.py` looks up this dict instead of having an `if/elif` chain per ATS.

**Why it is separated this way:** Adding a new ATS means writing one new module and adding one entry to the `CONNECTORS` dict, with no changes to the orchestrator, classification, or notification logic. Each adapter module is independently readable and testable.

The shared job schema has exactly six fields:

```python
{
    "id":              str,    # stable unique identifier
    "title":           str,    # job title ("Untitled" as fallback)
    "location":        str,    # location text ("Unknown" as fallback)
    "posted":          str,    # raw date text (or "" if unavailable)
    "posted_days_ago": int|None,  # computed age, or None if unconfirmable
    "link":            str,    # full URL to the job posting
}
```

### Input Validation and Sanitization

**What it does:** `utils/schema.py` is the single validation chokepoint every adapter routes its data through, protecting the dashboard (and the email) from untrusted external text. Since ATS content is rendered in a UI, this is a stored-XSS surface.

Two gates run, in order:

1. **Raw response models**: One pydantic model per ATS mirrors the response shape that ATS actually returns, with the fields its adapter reads. `validate_raw_jobs()` calls `model.model_validate()` on every item right after `response.json()`, so a malformed response is caught at the boundary. HTML-bearing description fields are tag-stripped with `bleach.clean()` (tags stripped, not escaped) before normalization ever reads them.

2. **Normalized gate**: `JobPosting` models the shared 6-field dict and `validate_job_posting()` re-cleans the stored text fields (`title`, `location`) and enforces the link policy: the scheme must be `https` and the host must match the expected ATS domain from `ALLOWED_LINK_DOMAINS` (subdomain suffixes for `*.recruitee.com` and `*.myworkdayjobs.com`; SAP and Workday links are config-derived so their expected domain is supplied at call time). An empty link still passes, matching pre-v3.1 behavior.

Anything that fails either gate is dropped and recorded via `log_audit_event(event_type="VALIDATION_REJECTED", ...)` with a `reason` of `response_shape`, `schema_violation`, or `url_rejected`. Rejected data is never silently stored. Because sanitization happens at storage time, the SQLite database never holds raw markup. Streamlit auto-escapes cell text by default and `dashboard.py` never uses `unsafe_allow_html`, so the dashboard has defense-in-depth on top of storage-time cleaning.

### Classification / Tiering

**What it does:** Every job is classified into one of three results by `matches_filters()` in `job_monitor.py`:

| Result | Meaning | Action |
|---|---|---|
| `"match"` | Passes ALL filters (location, role+domain keyword AND logic, exclude-keyword, age) | Highlighted in email as green cards |
| `"ambiguous"` | Passes keyword and location checks but age is inconclusive | Listed in email as muted manual-check rows |
| `"no_match"` | Fails at least one filter definitively | Dropped entirely, not persisted in the database |

Keyword matching uses word-boundary regex against the job title, not raw substring. A posting matches only when ALL of these hold: at least one `role_keywords` entry matches, at least one `domain_keywords` entry matches, and zero `exclude_keywords` entries match. This kills false positives where a generic term like "engineer" tripped the old single combined `keywords` list. A secondary heuristic covers internship postings whose titles carry an explicit date range (a month paired with a specific year in parentheses or brackets, e.g. "(January to June 2027)") instead of a literal role keyword: such a title satisfies the role half of the check even when no `role_keywords` term appears. Matching lives in `utils/matching.py` (`keyword_matches()` and `has_date_range_signal()`).

The age filter uses `posted_days_ago`: if the value is `None` (could not determine the date), the job is treated as ambiguous rather than rejected. If `max_age_days` is not configured, the age check is skipped entirely.

**Why thresholds exist:** Not all job postings include clean location data. Some are remote, some show "Singapore" as one of multiple locations, and some simply omit the field. The "ambiguous" tier prevents false negatives (missing a relevant job) while still flagging that a manual check is needed.

### Rate Limiting and Backoff

**What it does:** Every adapter HTTP call goes through `RateLimiter.get()` or `RateLimiter.post()` instead of calling `requests` directly. The module enforces two things:

1. **Per-company requests-per-minute cap**: Tracked per company (not per platform), since two companies on the same ATS are independent endpoints. Conservative default of 10 requests/minute via `PlatformConfig`.

2. **Exponential backoff with jitter on throttle signals**: On 429 or platform-specific signals (e.g., Workday's 503), the module waits before retrying, with the wait roughly doubling each attempt (2s, 4s, 8s...) capped at 60s. Jitter prevents multiple companies on the same ATS from retrying in lockstep.

If retries are exhausted, `RateLimitExceeded` is raised rather than looping forever. Adapters catch this and return a `SkipReason`; the system **skips and reports** rather than escalating.

Adapters run concurrently in `job_monitor.py` via a `ThreadPoolExecutor` (capped at 15 workers), so the rate limiter's per-company tracker registry and the robots.txt parser cache are guarded by locks to stay safe under parallel workers. The skip tracker and dedup layer need no such lock: every `db/repository.py` call opens its own short-lived connection to SQLite. Per-company trackers are only touched by the single worker handling that company, so the requests-per-minute cap is still enforced per company exactly as before.

### Robots.txt Compliance

**What it does:** `RobotsChecker` in `utils/robots_check.py` checks whether a target path is allowed for `user-agent: *` before every adapter's first API call. `robots.txt` is fetched via `requests` with an identifying User-Agent (`JobMonitorBot/1.0`) and parsed with the stdlib `urllib.robotparser`. Results are cached per domain for the process lifetime, and the cache is guarded by a lock because adapters now run concurrently.

**Fetch behavior:** Network errors retry with exponential backoff (default 1 retry) and fail conservative if they persist. Server-side 5xx responses retry once, then fail conservative (treated as disallowed) if they persist. A 401 or 403 is an explicit access denial and is treated as disallowed. A 404 or any other 4xx means the platform publishes no usable `robots.txt`, so the path is allowed per RFC 9309 (absence means allow-all).

**Fail-conservative:** If `robots.txt` is unreachable or explicitly denied (persistent network errors, 401, or 403), the path is treated as **disallowed**. The checker never assumes "no response means allowed" when the file could not actually be fetched.

### Audit Logging

**What it does:** `utils/audit_log.py` manages two log streams:

| Stream | File | Format | Max Size | Backups |
|---|---|---|---|---|
| Audit | `logs/audit.log` | JSON lines | 5 MB | 5 |
| Operational | `logs/operational.log` | Timestamped text | 5 MB | 3 |

Five audit event types are recorded: `QUERY` (before an adapter call), `SKIP` (when a company is skipped), `CLASSIFY` (filtering results per company), `TIER3_HARDSTOP` (when bot-detection signals are detected or retries exhausted), and `VALIDATION_REJECTED` (when an ATS response or normalized job fails the `utils/schema.py` gates).

### Skip Tracking

**What it does:** `utils/skip_tracker.py` persists a consecutive skip count per company in the `skip_streaks` table of the SQLite database (via `db/repository.py`). Each successful fetch resets the streak to zero. Companies at or above 3 consecutive skips are flagged in the email notification.

This is intentionally separate from the rate limiter because they track fundamentally different state:

| | Rate Limiter | Skip Tracker |
|---|---|---|
| Tracks | Request timing over the last ~60 seconds | Consecutive cycle skips per company |
| Lifespan | In-memory, thrown away every run | Persisted in the `skip_streaks` table across runs |
| Why | A 6-hour gap between cycles makes last run's timing meaningless | A pattern across cycles is the whole point |

Each `SkipTracker` method delegates to a `db/repository.py` function (`record_skip`, `reset_skip_streak`, `list_skip_streaks`), and every call opens its own short-lived connection, so the concurrent adapter workers that each hold their own `SkipTracker` instance never race on a shared handle.

### Deduplication

**What it does:** Since v3.3, dedup state lives in the `jobs` table of the SQLite database (`data/jobmonitor.db` by default), managed through `db/repository.py`. The primary key is `(company, job_id, tier)`, which preserves the same dedup identity as the old `seen_jobs.json`: one row per job ID per company, with `tier` (`match` or `ambiguous`) tracked separately.

| Column | Meaning |
|---|---|
| `company` | Company name from config |
| `job_id` | Stable unique identifier from the ATS |
| `tier` | `match` or `ambiguous` |
| `title`, `location`, `posted`, `posted_days_ago` | Human-readable detail fields |
| `url` | Full URL to the posting |
| `ats_platform` | ATS that produced the posting (e.g. `greenhouse`) |
| `first_seen_at` | When the posting was first matched, preserved across runs so the dashboard shows a stable "date matched" |

On each run, current job IDs are compared against stored IDs via `repository.is_job_seen()` / `repository.list_jobs()`. Only truly new IDs (present in current results but absent from stored data) are reported. After processing, postings are written via `repository.mark_job_seen()`, which uses `INSERT OR IGNORE` so a posting already in the database keeps its original `first_seen_at` while its mutable detail fields are refreshed.

**Why two tiers:** Without tracking `ambiguous_ids` separately, an ambiguous posting would appear as "new" in every single run's email forever, since it never graduates to "match" but also never gets suppressed. The `tier` column keeps the same guarantee: an ID is only "new" when it is absent from *its* tier.

### Email Notification

**What it does:** `utils/notifier.py` sends an HTML email via Gmail SMTP (`smtplib.SMTP_SSL` to `smtp.gmail.com:465`) when any of these conditions are true:

1. At least one new matched job
2. At least one new ambiguous job
3. At least one flagged company (3+ consecutive skips)

The HTML email contains green-bordered match cards, muted ambiguous rows, and amber flagged-company warnings. Credentials are read from environment variables via `python-dotenv`.

### Dashboard

**What it does:** `dashboard.py` is a Streamlit app that reads the tracked postings from the SQLite database via `repository.list_jobs()` and renders a filterable table of every matched and ambiguous posting. Each row shows company, title, location, link, and the date the posting was first matched. Users can filter by company, tier (match / ambiguous), and date range.

The dashboard is a pure read-side view: it never writes to the database and never fetches live data. The data path is configurable via the `DB_PATH` env var or the `--db-path` CLI flag (flag wins, then env var, then the repo-relative default), so forks can point it at their own database without code changes. `db/repository.py` loads `.env` itself, so the dashboard always resolves the same `DB_PATH` as the monitor.

### Tier 3 Hard-Stop

**What it does:** `check_hardstop()` in `utils/audit_log.py` inspects every successful HTTP response for bot-detection signals:

- HTTP 403
- HTTP 503 (non-Workday platforms)
- HTML body containing `captcha`, `robot`, `access denied`, or `blocked`

This check runs inside the rate limiter on every successful response. If triggered, a `TIER3_HARDSTOP` audit event is logged and the request is treated as failed.

**This logic should never be loosened or bypassed.** It is a deliberate ethical and legal safeguard. Continuing to probe after detection crosses from "automated job search" into adversarial scraping territory.

## Design Decisions

- **Decision:** Per-company rate limiting instead of per-platform.
  **Reasoning:** Two companies on the same ATS (e.g., two Greenhouse boards) are independent endpoints on behalf of independent job searches. Throttling one because of the other would be incorrect. Per-company tracking is more conservative and more correct.

- **Decision:** Fetch functions can return `None` (not checked) vs `[]` (checked, no jobs found).
  **Reasoning:** Before v2.0, every fetch always returned a list. A rate-limited company returning `None` must not overwrite its dedup baseline in the database. An empty list means "we checked and there are genuinely no jobs", so that should update the baseline. Treating these the same would cause a rate-limited company to show "18 new jobs" the moment it recovers.

- **Decision:** Skip-and-report instead of escalate on rate limits.
  **Reasoning:** Rate-limit skips are a much weaker signal than active bot detection. They are logged, counted, and mentioned in the email if they persist, but they never trigger the Tier 3 hard-stop logic. Only HTTP-level or content-level bot-detection signals trigger a hard stop.

- **Decision:** Keep a `tier` column (`match` / `ambiguous`) as part of the dedup key instead of a single per-company ID list.
  **Reasoning:** Without separate tracking, an ambiguous job (e.g., one with an inconclusive location) would be reported as "new" in every run's email forever. Keeping the tier in the primary key means ambiguous postings are only reported once, matching the behavior of the pre-v3.3 `matched_ids` / `ambiguous_ids` lists.

- **Decision:** Fail-conservative for robots.txt checking.
  **Reasoning:** If robots.txt is unreachable (network error, timeout, malformed), the checker treats the path as disallowed. This is more restrictive than necessary when the platform is simply having a bad day, but it builds the right habit: treat access control signals as worth following even when nobody is enforcing them.

- **Decision:** Separate `rate_limiter.py` and `skip_tracker.py` instead of one module.
  **Reasoning:** They track fundamentally different state (in-memory timing vs persistent skip streaks). Combining them would conflate two concerns with different lifespans, persistence requirements, and failure semantics. The separation keeps each module's contract simple.

- **Decision:** Validate and sanitize ATS data at the boundary, before normalization.
  **Reasoning:** Job titles, companies, and descriptions are untrusted external text that eventually renders in a UI (dashboard, email). Validating the raw response shape catches an ATS "starting to return garbage" at the source with an audit trail, and stripping HTML tags at storage time means the database itself never holds raw markup, with Streamlit's auto-escaping then a second layer rather than the only one.

- **Decision:** Pre-commit `detect-secrets` hook to prevent credential leaks.
  **Reasoning:** `.gitignore` protects `.env` but does not catch credentials accidentally hardcoded during debugging. A pre-commit hook scanning staged changes is the standard defense-in-depth measure for this class of mistake.

- **Decision:** Tier 3 hard-stop is never loosened, even for testing.
  **Reasoning:** Continuing to probe a platform after receiving bot-detection signals crosses from "reading public data" into adversarial scraping. This is an explicit ethical boundary informed by cases like *hiQ v. LinkedIn*. The system is designed to fail safe, not evade detection.

## Data Flow

A complete run cycle in `job_monitor.py`:

1. **`setup_logging()`**: Initializes the audit and operational log streams.
2. **`load_config()`**: Reads the companies/filters config file (locations, role_keywords, domain_keywords, exclude_keywords, max_age_days). The path comes from the `--config` CLI flag, defaulting to the repo-anchored `config.json`.
3. **DB path resolution**: The SQLite database path comes from the `--db-path` CLI flag, else `repository.get_db_path()` (`DB_PATH` env var, else `data/jobmonitor.db`); tables are created idempotently on first contact.
4. For each company in config (dispatched concurrently via a `ThreadPoolExecutor` capped at 15 workers):
   a. Look up the fetch function: `CONNECTORS[ats]` for standard ATS, `CUSTOM_HANDLERS[handler]` for custom.
   b. Log `QUERY` audit event.
   c. Call the fetch function:
      - `RobotsChecker.is_allowed()` runs first. If disallowed, return `SkipReason`.
      - `RateLimiter.get()`/`post()` handles throttling, backoff, and retries.
      - On success, `check_hardstop()` inspects the response for bot-detection signals.
      - On `RateLimitExceeded`, catch and return `SkipReason`.
      - `SkipTracker.record_success()` or `record_skip()` is called accordingly.
   d. If the result is a `SkipReason`, log and skip to the next company.
   e. Run `matches_filters()` on each job to classify as match/ambiguous/no_match.
   f. Deduplicate against the stored `jobs` rows for the company (via `repository.list_jobs()`); only new IDs are collected.
   g. Log `CLASSIFY` audit event with match/ambiguous/new counts.
   h. Persist current matches and ambiguous postings via `repository.mark_job_seen()` in the main thread.
5. Log the summary of new matches, ambiguous jobs, and flagged companies.
6. **`send_notification()`**: Build and send the HTML email if there is anything to report.

Outside the run cycle, `dashboard.py` reads the same `jobs` table via `repository.list_jobs()` and renders the records as a filterable table, so the monitor's output has both an email view and a web view of the same underlying data.

## Known Limitations / Future Work

- **Location filtering**: There is a known issue with multiple locations. A job listing in "Singapore, Hong Kong, Tokyo" may not match a "singapore" filter correctly depending on the delimiter and formatting. This needs a more robust location-matching strategy.

- **Stricter robots.txt on some ATS**: SAP SuccessFactors, SmartRecruiters, Ashby, and Workable have stricter `robots.txt` rules that currently disallow the endpoints this system uses. The adapters remain in their own modules but return `SkipReason` for any company on those platforms. If the platforms update their policies, the adapters will work without code changes.

- **No scheduler wired yet**: The system is designed to run on a schedule (GitHub Actions), but the scheduling configuration is not yet documented or finalized. Currently each run must be triggered manually or via an external scheduler.

- **Posting dates on upgrade**: The SQLite database starts empty on first run after the v3.3 migration (no JSON-to-SQLite import), so `first_seen_at` values begin at that first run; current matches are re-reported once as "new". The old `seen_jobs.json` / `skip_history.json` files are no longer written but are left in place.

- **Test coverage**: Unit tests cover the security-critical modules plus the data layer: rate limiter (6 tests in `tests/test_rate_limiter.py`), robots.txt compliance checker (15 tests in `tests/test_robots_check.py`), audit logging / hard-stop detection (15 tests in `tests/test_audit_log.py`), database persistence / dedup / skip streaks (9 tests in `tests/test_db.py`), dashboard flattening, filtering, and path resolution (18 tests in `tests/test_dashboard.py`), input validation / sanitization (48 tests in `tests/test_schema.py`, one skipped by design for the SAP optional-title case), and keyword matching / concurrent aggregation (8 tests in `tests/test_matching.py`). Adapters, classification, and notification are not yet covered.

- **Minimal dependencies**: Runtime: `requests` and `python-dotenv` for the monitor core, plus `streamlit` and `pandas` for the dashboard. Since v3.1, `pydantic` (response validation) and `bleach` (HTML sanitization) are runtime dependencies too. Persistence uses the stdlib `sqlite3` module, with no ORM. Everything else (robotparser, JSON, logging, SMTP, datetime, collections, dataclasses) is Python stdlib. Testing requires `pytest` (listed in `requirements-dev.txt`). This is intentional for security and portability but means some features (e.g., HTML parsing) require manual implementation.
