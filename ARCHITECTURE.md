# Architecture

## Overview

The system follows an **adapter pattern** -- a single orchestrator loop in `job_monitor.py` delegates to platform-specific adapter functions (Greenhouse, Lever, Ashby, etc.), each of which normalizes results into a shared 6-field job schema before they hit the classification layer. Every adapter routes HTTP calls through a shared rate limiter and checks robots.txt compliance before the first request.

```
[Config/Scheduler] -> [Orchestrator (job_monitor.py)]
                           |
                +----------+-----------+
                |                      |
        [Adapter Layer]         [Custom Handlers]
        (9 standard ATS)       (company-specific)
                |                      |
        [Rate Limiter]  <---  [Robots Checker]
                |                      |
        [Classification / Filtering]
                |
        [Deduplication (seen_jobs.json)]
                |
        [Email Notification]
```

## Key Components

### Adapter Layer

**What it does:** Each ATS platform gets its own fetch function in `adapters/connectors.py`, all following the same pattern: build the URL, check robots.txt, route through the rate limiter, parse the response, normalize into the shared 6-field dict format, return a list of jobs or a `SkipReason`.

**Why it is separated this way:** Adding a new ATS means writing one new function and adding it to the `CONNECTORS` dict -- no changes to the orchestrator, classification, or notification logic. The dict-based dispatch replaces what would otherwise be a long if/elif chain.

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

### Classification / Tiering

**What it does:** Every job is classified into one of three results by `matches_filters()` in `job_monitor.py`:

| Result | Meaning | Action |
|---|---|---|
| `"match"` | Passes ALL filters (location, keyword, exclude-keyword, age) | Highlighted in email as green cards |
| `"ambiguous"` | Passes keywords but location or age is inconclusive | Listed in email as muted manual-check rows |
| `"no_match"` | Fails at least one filter definitively | Dropped entirely, not tracked in `seen_jobs` |

The age filter uses `posted_days_ago`: if the value is `None` (could not determine the date), the job is treated as ambiguous rather than rejected. If `max_age_days` is not configured, the age check is skipped entirely.

**Why thresholds exist:** Not all job postings include clean location data. Some are remote, some show "Singapore" as one of multiple locations, and some simply omit the field. The "ambiguous" tier prevents false negatives (missing a relevant job) while still flagging that a manual check is needed.

### Rate Limiting and Backoff

**What it does:** Every adapter HTTP call goes through `RateLimiter.get()` or `RateLimiter.post()` instead of calling `requests` directly. The module enforces two things:

1. **Per-company requests-per-minute cap** -- Tracked per company (not per platform), since two companies on the same ATS are independent endpoints. Conservative default of 10 requests/minute via `PlatformConfig`.

2. **Exponential backoff with jitter on throttle signals** -- On 429 or platform-specific signals (e.g., Workday's 503), the module waits before retrying, with the wait roughly doubling each attempt (2s, 4s, 8s...) capped at 60s. Jitter prevents multiple companies on the same ATS from retrying in lockstep.

If retries are exhausted, `RateLimitExceeded` is raised rather than looping forever. Adapters catch this and return a `SkipReason` -- the system **skips and reports** rather than escalating.

### Robots.txt Compliance

**What it does:** `RobotsChecker` in `utils/robots_check.py` uses Python's stdlib `urllib.robotparser` to check whether a target path is allowed for `user-agent: *` before every adapter's first API call. Results are cached per domain for the process lifetime.

**Fail-conservative:** If robots.txt is unreachable, unparseable, or errors, the path is treated as **disallowed**. The checker never assumes "no response means allowed."

### Audit Logging

**What it does:** `utils/audit_log.py` manages two log streams:

| Stream | File | Format | Max Size | Backups |
|---|---|---|---|---|
| Audit | `logs/audit.log` | JSON lines | 5 MB | 5 |
| Operational | `logs/operational.log` | Timestamped text | 5 MB | 3 |

Four audit event types are recorded: `QUERY` (before an adapter call), `SKIP` (when a company is skipped), `CLASSIFY` (filtering results per company), and `TIER3_HARDSTOP` (when bot-detection signals are detected or retries exhausted).

### Skip Tracking

**What it does:** `utils/skip_tracker.py` persists a consecutive skip count per company in `skip_history.json`. Each successful fetch resets the streak to zero. Companies at or above 3 consecutive skips are flagged in the email notification.

This is intentionally separate from the rate limiter because they track fundamentally different state:

| | Rate Limiter | Skip Tracker |
|---|---|---|
| Tracks | Request timing over the last ~60 seconds | Consecutive cycle skips per company |
| Lifespan | In-memory, thrown away every run | Persisted to `skip_history.json` across runs |
| Why | A 6-hour gap between cycles makes last run's timing meaningless | A pattern across cycles is the whole point |

### Deduplication

**What it does:** `seen_jobs.json` stores previously-seen job IDs per company under two keys:

```json
{
  "Company Name": {
    "matched_ids": ["id1", "id2"],
    "ambiguous_ids": ["id3", "id4"]
  }
}
```

On each run, current job IDs are compared against stored IDs. Only truly new IDs (present in current results but absent from stored data) are reported. After processing, stored IDs are overwritten with the current full set.

**Why two lists:** Without tracking `ambiguous_ids` separately, an ambiguous posting would appear as "new" in every single run's email forever, since it never graduates to "match" but also never gets suppressed.

### Email Notification

**What it does:** `utils/notifier.py` sends an HTML email via Gmail SMTP (`smtplib.SMTP_SSL` to `smtp.gmail.com:465`) when any of these conditions are true:

1. At least one new matched job
2. At least one new ambiguous job
3. At least one flagged company (3+ consecutive skips)

The HTML email contains green-bordered match cards, muted ambiguous rows, and amber flagged-company warnings. Credentials are read from environment variables via `python-dotenv`.

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
  **Reasoning:** Before v2.0, every fetch always returned a list. A rate-limited company returning `None` must not overwrite its `seen_jobs.json` baseline. An empty list means "we checked and there are genuinely no jobs" -- that should update the baseline. Treating these the same would cause a rate-limited company to show "18 new jobs" the moment it recovers.

- **Decision:** Skip-and-report instead of escalate on rate limits.
  **Reasoning:** Rate-limit skips are a much weaker signal than active bot detection. They are logged, counted, and mentioned in the email if they persist, but they never trigger the Tier 3 hard-stop logic. Only HTTP-level or content-level bot-detection signals trigger a hard stop.

- **Decision:** Separate `matched_ids` and `ambiguous_ids` in the dedup store.
  **Reasoning:** Without separate tracking, an ambiguous job (e.g., one with an inconclusive location) would be reported as "new" in every run's email forever. Separating the lists means ambiguous postings are only reported once.

- **Decision:** Fail-conservative for robots.txt checking.
  **Reasoning:** If robots.txt is unreachable (network error, timeout, malformed), the checker treats the path as disallowed. This is more restrictive than necessary when the platform is simply having a bad day, but it builds the right habit: treat access control signals as worth following even when nobody is enforcing them.

- **Decision:** Separate `rate_limiter.py` and `skip_tracker.py` instead of one module.
  **Reasoning:** They track fundamentally different state (in-memory timing vs persistent skip streaks). Combining them would conflate two concerns with different lifespans, persistence requirements, and failure semantics. The separation keeps each module's contract simple.

- **Decision:** Pre-commit `detect-secrets` hook to prevent credential leaks.
  **Reasoning:** `.gitignore` protects `.env` but does not catch credentials accidentally hardcoded during debugging. A pre-commit hook scanning staged changes is the standard defense-in-depth measure for this class of mistake.

- **Decision:** Tier 3 hard-stop is never loosened, even for testing.
  **Reasoning:** Continuing to probe a platform after receiving bot-detection signals crosses from "reading public data" into adversarial scraping. This is an explicit ethical boundary informed by cases like *hiQ v. LinkedIn*. The system is designed to fail safe, not evade detection.

## Data Flow

A complete run cycle in `job_monitor.py`:

1. **`setup_logging()`** -- Initializes the audit and operational log streams.
2. **`load_config()`** -- Reads `config.json` for the company list and global filters (locations, keywords, exclude_keywords, max_age_days).
3. **`load_seen_jobs()`** -- Loads `seen_jobs.json` into memory.
4. For each company in config:
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
   f. Deduplicate against `seen_jobs[company]` -- only new IDs are collected.
   g. Log `CLASSIFY` audit event with match/ambiguous/new counts.
   h. Update `seen_jobs[company]` with current IDs.
5. **`save_seen_jobs()`** -- Persist the updated dedup state to disk.
6. Log the summary of new matches, ambiguous jobs, and flagged companies.
7. **`send_notification()`** -- Build and send the HTML email if there is anything to report.

## Known Limitations / Future Work

- **Location filtering** -- There is a known issue with multiple locations. A job listing in "Singapore, Hong Kong, Tokyo" may not match a "singapore" filter correctly depending on the delimiter and formatting. This needs a more robust location-matching strategy.

- **Stricter robots.txt on some ATS** -- SAP SuccessFactors, SmartRecruiters, Ashby, and Workable have stricter `robots.txt` rules that currently disallow the endpoints this system uses. The adapters remain in `connectors.py` but return `SkipReason` for any company on those platforms. If the platforms update their policies, the adapters will work without code changes.

- **No scheduler wired yet** -- The system is designed to run on a schedule (GitHub Actions), but the scheduling configuration is not yet documented or finalized. Currently each run must be triggered manually or via an external scheduler.

- **Test coverage** -- Unit tests cover the three security-critical modules: rate limiter (6 tests in `tests/test_rate_limiter.py`), robots.txt compliance checker (12 tests in `tests/test_robots_check.py`), and audit logging / hard-stop detection (15 tests in `tests/test_audit_log.py`). Adapters, classification, and notification are not yet covered.

- **Minimal dependencies** -- Runtime: only `requests` and `python-dotenv`. Everything else (robotparser, JSON, logging, SMTP, datetime, collections, dataclasses) is Python stdlib. Testing requires `pytest` (listed in `requirements-dev.txt`). This is intentional for security and portability but means some features (e.g., HTML parsing) require manual implementation.
