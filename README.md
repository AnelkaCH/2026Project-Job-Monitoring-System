# Job Monitoring System

A self-hosted, zero-cost tool that watches specific companies' job boards and tells me when they post something new, instead of me manually checking a dozen careers pages every day.

> **Status: early work in progress.** This is not a finished product. Connectors, filtering, and scheduling are all being built incrementally. Expect breaking changes, missing features, and rough edges. See [Roadmap](#roadmap) for what's actually done vs planned.

## Current Issues

There are some issues observed that will be fixed in a later version.

- Broken links - Some of the buttons don't actually lead to the posting. Will fix this later.
- Location filtering - There is a problem with multiple locations.

## Version 2.2.2

### Version Notes

Secrets were already kept out of git via `.env` and `.gitignore`, but that only protects against committing the `.env` file itself. It doesn't catch a credential accidentally hardcoded somewhere else, like a value pasted in during debugging and never removed. This version adds a pre-commit hook that scans staged changes for anything that looks like a secret before a commit can go through.

### What was added

**`detect-secrets`** pre-commit hook, wired in via the `pre-commit` framework:

- **`.pre-commit-config.yaml`** - Configures the `detect-secrets` hook to run on every commit.
- **`.secrets.baseline`** - A snapshot of currently-flagged strings (mostly placeholder values in `.env.example`) so the hook only alerts on *new* secrets going forward, not ones already reviewed and accepted.

The hook runs locally, scanning staged files before a commit is finalized. If it detects something matching a secret pattern (API key formats, high-entropy strings assigned to variables like `password` or `token`, private key headers, etc.), the commit is blocked and the offending line is printed so it can be fixed before anything reaches git history.

### Setup required per machine

[#setup-required-per-machine](#setup-required-per-machine)

Unlike the rest of this repo, this hook only activates after running one extra command locally. It's not automatic on a fresh clone:

```bash
pip install pre-commit detect-secrets --break-system-packages
pre-commit install
```

This needs to be re-run on any new machine this repo is cloned onto (e.g. after the SUTD laptop switch).

### Files touched

| File | Change |
|---|---|
| `.pre-commit-config.yaml` | **New.** Configures the `detect-secrets` hook. |
| `.secrets.baseline` | **New.** Baseline of accepted/known flagged strings. |

## Version 2.2.1

### Version Notes

Every file lived in the root directory. As the codebase grew, it became harder to tell at a glance what was a core module, what was an adapter, and what was infrastructure. This version organizes the project into a package structure without changing any behavior.

### What was added

Restructured the flat file layout into three packages:

- **`adapters/`** - All ATS fetch logic: `connectors.py` (9 standard adapters), `custom_handlers.py` (private handler), and `custom_handler_example.py` (template).
- **`utils/`** - Shared infrastructure: `audit_log.py`, `date_utils.py`, `rate_limiter.py`, `skip_tracker.py`, `robots_check.py`, and `notifier.py`.
- **`tests/`** - Unit tests, with automatic `sys.path` resolution so tests run without manual `PYTHONPATH` setup.

Imports across all files updated to use package-qualified paths (`from utils.rate_limiter import ...`). Internal references within `utils/` use relative imports where appropriate. Three files (`audit_log.py`, `skip_tracker.py`, `robots_check.py`) had `__file__`-relative path logic updated to account for the extra directory level.

`job_monitor.py` stays in the project root as the main entrypoint.

### Files touched

| File | Change |
|---|---|
| `adapters/connectors.py` | **Moved** from root. Imports updated. |
| `adapters/custom_handlers.py` | **Moved** from root. Imports updated. |
| `adapters/custom_handler_example.py` | **Moved** from root. Imports updated. |
| `utils/audit_log.py` | **Moved** from root. Log path corrected for new depth. |
| `utils/date_utils.py` | **Moved** from root. No import changes (leaf module). |
| `utils/rate_limiter.py` | **Moved** from root. Import changed to relative (`from .audit_log`). |
| `utils/skip_tracker.py` | **Moved** from root. `skip_history.json` path corrected. |
| `utils/robots_check.py` | **Moved** from root. `config.json` path corrected for standalone CLI. |
| `utils/notifier.py` | **Moved** from root. No import changes (leaf module). |
| `tests/test_rate_limiter.py` | **Moved** from root. Patch strings updated. Added `sys.path` auto-resolution. |

## Version 2.2

### Version Notes

This is honestly the version I struggled with the most. No, it's not because it was hard. It was just because I had to make a big decision. If I want to work in cybersecurity, respecting robots.txt has to be something I must do. Sure, it is not illegal to disobey it, but this isn't about legal liability. It's about building the habit of treating access control signals as worth following, even when nobody's enforcing them.

### What was added

**`robots_check.py`** - A compliance checker that runs before every adapter's first API call. Uses `urllib.robotparser` (stdlib) to check whether the target path is allowed for `user-agent: *`. Fails conservative: if robots.txt is unreachable, treat as disallowed. Cached per domain for the process lifetime, same pattern as `rate_limiter._trackers`.

**`SkipReason`** = Adapters now return `SkipReason("robots.txt disallowed")` or `SkipReason("rate-limited")` instead of bare `None`. The orchestrator displays the correct reason rather than calling everything "rate-limited."

| File | Change |
|---|---|
| `robots_check.py` | **New.** `RobotsChecker`, `SkipReason`, standalone CLI (`python robots_check.py`). |
| `connectors.py` | robots.txt check + `SkipReason` return in all 9 adapters. |
| `custom_handlers.py` | Same for Accenture handler. |
| `job_monitor.py` | Distinguishes robots.txt disallows from rate-limit skips in output. |
| `notifier.py` | "Repeatedly rate-limited" -> "Repeatedly skipped". |

## Version 2.1

### Version Notes

Without an audit trail, the Tier 3 hard-stop from v2 is a described behavior with no evidence it runs. V2.1 adds structured logging that records every query, classification, and hard-stop event as JSON lines, kept separate from routine output and rotated so it stays bounded under continuous scheduling.

### What was added

#### `audit_log.py`

Three things, one new module:

- **Two log streams:** `logs/audit.log` (JSON lines, 5 MB × 5 backups) for security events; `logs/operational.log` (timestamped text, 5 MB × 3 backups) for routine output; plus a console handler so interactive runs still show feedback. `setup_logging()` is needed to safely call every run without stacking handlers.

- **`log_audit_event(event_type, **fields)`** writes a JSON line with an ISO 8601 timestamp and any key-value fields.

- **`check_hardstop(response, platform)`** inspects responses for bot-detection signals without DOM/HTML parsing: HTTP 403, HTTP 503 (non-Workday), and `text/html` responses containing `captcha`, `robot`, `access denied`, or `blocked` as substrings.

#### Changes to existing files

| File | Change |
|---|---|
| `rate_limiter.py` | Logs `TIER3_HARDSTOP` before raising `RateLimitExceeded` (retries exhausted) and when `check_hardstop()` finds indicators on a successful response. One centralized check covers every adapter. |
| `job_monitor.py` | All `print()` calls replaced with `operational_logger.*()`. Added three audit events: `QUERY` (before each adapter call), `TIER3_HARDSTOP` (on skip/error), `CLASSIFY` (match/ambiguous/new counts per company). Calls `setup_logging()` at startup. |
| `.gitignore` | Added `logs/`. |

### How audit events flow

```
QUERY (before adapter call)
  -> adapter runs through rate_limiter
    -> TIER3_HARDSTOP if exhausts retries (reason: throttle/request exhausted)
    -> TIER3_HARDSTOP if response has bot-detection indicators
  -> None returned? -> TIER3_HARDSTOP (reason: rate-limited)
  -> Exception? -> TIER3_HARDSTOP (reason: unexpected error)
  -> Jobs classified -> CLASSIFY (match_count, ambiguous_count, new_count)
```

Example `audit.log` lines:

```
{"event": "QUERY", "timestamp": "2026-07-16T10:30:00", "ats": "greenhouse", "company": "RandomCheese"}
{"event": "TIER3_HARDSTOP", "timestamp": "2026-07-16T10:30:01", "platform": "greenhouse", "company": "RandomCheese", "reasons": ["HTTP 403 - access denied / bot detection"], "status": 403}
{"event": "CLASSIFY", "timestamp": "2026-07-16T10:30:02", "ats": "greenhouse", "company": "RandomCheese", "match_count": 5, "ambiguous_count": 2, "new_count": 1}
```

## Version 2.0

### Version Notes

This version adds a rate limiter that proactively slows requests down so they're less likely to look bot-like in the first place. I want to make this system more ethical. This system currentl checks 18 companies across 9 different ATS platforms (for testing purposes), on a recurring schedule (not yet implemented), from the same IP (GitHub Actions' runner). That's exactly the kind of traffic pattern automated bot detection is built to notice. Slowing down and backing off intelligently is the standard, respectful way to run something like this without looking adversarial to the platforms being checked.

### What was added

Two new modules, plus integration changes across the four files that already existed.

#### `rate_limiter.py`

Every adapter's HTTP call now goes through `RateLimiter.get()` or `RateLimiter.post()` instead of calling `requests.get()`/`requests.post()` directly. One shared instance is used everywhere (created once in `connectors.py`, imported by `custom_handlers.py`), so there's a single audited piece of code enforcing the policy.

It enforces two things:

1. **A per-company requests-per-minute cap.** Tracked per company (not per platform), since two different companies on the same ATS (for example, two different Greenhouse boards) are different endpoints being checked on behalf of different job searches, and shouldn't throttle each other. Conservative by default (`PlatformConfig.max_requests_per_minute`), with room to loosen per-platform once real limits are observed.

2. **Exponential backoff with jitter on throttle signals.** If a request comes back with a 429 (or a platform-specific throttle signal like Workday's 503s), the module waits before retrying, and that wait roughly doubles each attempt. A small amount of randomness (jitter) is layered on top so retries don't land in a predictable, metronome-like pattern, which is itself something bot detection can key off. Since adapters run sequentially rather than in parallel, jitter matters less here than it would in a concurrent system.

   If retries run out, the module raises `RateLimitExceeded` rather than crashing or looping forever. This is a **skip-and-report** philosophy: a company that's rate-limited too many times in one cycle just gets skipped for that cycle, logged, and reported in the email. It doesn't escalate into Tier 3's hard-stop logic, since being rate-limited is a much weaker signal than active bot detection.

Config for all 9 platforms (`greenhouse`, `lever`, `ashby`, `smartrecruiters`, `recruitee`, `workable`, `personio`, `workday`, `sap`, plus `accenture` for the custom handler) starts on the same conservative default. Tightening or loosening any one of them is a one-line change to `PLATFORM_CONFIGS`, no logic changes needed.

#### `skip_tracker.py`

This tracks something the rate limiter deliberately doesn't: **consecutive cycle skips per company.** If a company gets rate-limited once, that's probably nothing. If it gets rate-limited 3+ cycles in a row, that's worth knowing about. Maybe the endpoint changed, maybe the platform's limits got stricter, maybe something needs a manual look.

This is intentionally a separate file from `rate_limiter.py`, because it tracks a fundamentally different kind of state:

| | `rate_limiter.py` | `skip_tracker.py` |
|---|---|---|
| Tracks | request timing over the last ~60 seconds | consecutive cycle skips per company |
| Lifespan | in-memory, thrown away every run | persisted to `skip_history.json`, survives across runs |
| Why | a 6-hour gap between cycles makes last cycle's request timing meaningless | a *pattern across cycles* is the whole point |

It resets a company's streak back to zero the moment that company succeeds again, so the flag only ever reflects an active, ongoing problem.

### How a request actually flows through v2

```
adapter (e.g. fetch_greenhouse)
    │
    v
limiter.get(url, platform="greenhouse", company="Cloudflare")
    │
    ├- under the per-minute cap? -- no -> wait, then recheck
    │        │
    │       yes
    │        v
    │   make the request
    │        │
    │        ├- 429 / throttled? - yes -> wait (exponential + jitter), retry
    │        │                                     │
    │        │                              retries exhausted?
    │        │                                     │
    │        │                                    yes -> raise RateLimitExceeded
    │        │
    │       no
    │        v
    │   return the response
    v
adapter catches RateLimitExceeded (if raised)
    │
    ├- skip_tracker.record_skip(company) -> increments streak, logs it
    └- return None instead of a job list
         │
         v
    job_monitor.py sees None, skips this company for the cycle WITHOUT touching its seen_jobs baseline, so next cycle's dedup still works correctly once the company succeeds again
         │
         v
    notifier.py includes an amber "repeatedly rate-limited" section in the email if any company's streak crossed the threshold (3)
```

### Design decisions worth calling out

**Per-company, not per-platform, rate limiting.** Two companies on the same ATS are still two independent endpoints on behalf of two independent job searches. There's no reason to throttle one because of the other.

**No cross-cycle persistence for the rate limiter itself.** Whatever gap exists between runs, anything about request timing from the last run is meaningless by the time the next one starts. Only `skip_tracker.py` needs to survive across runs, and it does as a small JSON file (`skip_history.json`) saved right alongside `seen_jobs.json`. Note: this currently only persists between runs if something in the deployment environment keeps that file around between executions (e.g. a long-running machine, or a scheduler that preserves the working directory). A scheduler hasn't been wired up yet, so this is a decision to revisit once one is.

**Return contract change: `None` vs `[]`.** Before v2, every fetch function always returned a list and empty if there were genuinely no jobs. Now it can return `None`, meaning "not checked this cycle at all." `job_monitor.py` was updated to treat these two cases very differently: an empty list updates the seen-jobs baseline (correctly, since a real check happened and found nothing); `None` skips the update entirely, so a temporarily rate-limited company doesn't have its real history erased and mistakenly reported as "18 new jobs" the moment it recovers.

**Skip-and-report, not escalate.** A rate-limit skip is a much weaker signal than the Tier 3 hard-stop's active bot detection, so it's handled separately: log it, count it, mention it in the email if it persists. Never treat it as proof the company has started blocking the monitor outright.

### Files touched

| File | Change |
|---|---|
| `rate_limiter.py` | **New.** Core module. |
| `skip_tracker.py` | **New.** Persistent skip-streak counter. |
| `connectors.py` | All 9 fetch functions route through the limiter; return `None` on skip. |
| `custom_handlers.py` | Accenture's handler gets the same treatment; shares state with `connectors.py`. |
| `job_monitor.py` | Handles `None` returns without crashing or wiping baselines. |
| `notifier.py` | Email now includes a flagged-companies section when a streak crosses threshold. |
| `date_utils.py`, `config.json` | Unchanged. |

## Version 1.0

### Version Notes

I'm tracking internship and job openings at a specific list of Singapore-based companies across cybersecurity, cloud, and tech, as part of my own job search. Rather than checking each company's careers page by hand, this project automates that while staying within clear technical and ethical boundaries (see below).

It's also a portfolio project. I want to demonstrate reasonable engineering judgment around API usage, rate limits, and respecting what platforms actually intend to expose publicly.

### Why this exists

Most companies publish their openings through one of a handful of ATS platforms, many of which expose a structured JSON endpoint for their own careers page. Rather than scraping HTML per-company, this project builds a unified connector layer: one adapter per ATS, all normalized into the same job format, so adding a new company is usually just a config entry, not new code.

### ATS Coverage

**Tier 1 = Full JSON API support**
Greenhouse, Ashby, Lever, Workable, Personio, SmartRecruiters, Recruitee

These platforms expose clean, public JSON endpoints for their careers pages. Connectors here are stable and reusable across any company using that ATS.

**Tier 2 = Partial or non-standard support**
Workday, SAP SuccessFactors, Custom

These either require more complex request handling (pagination quirks, non-obvious endpoints, session tokens) or, in the case of "Custom," a bespoke handler for a company that doesn't run on a standard ATS. See [Custom Handlers](#custom-handlers) below for why this isn't public.

**Tier 3 = Not currently supported**
Companies with no accessible JSON endpoint. These would require HTML scraping or browser automation, which is out of scope for now. Skipped on purpose.

**On the roadmap**
Oracle Recruiting Cloud (Fusion), and other ATS platforms as they come up.

### Is this safe / legal?

Some notes on how this is built to stay on the right side of that line, though I'm not a lawyer and this isn't legal advice:

- It only reads job postings that are already public on each company's own careers page, sp nothing behind a login, nothing requiring authentication bypass.
- Tier 1 connectors use the same JSON endpoints the company's own careers page calls in the browser. This is the same data a human visitor would see, just fetched programmatically instead of clicked through.
- Requests are rate-limited and spaced out. This is a personal tool checking a handful of times a day, not a high-frequency scraper.
- No CAPTCHA solving, no bot-detection evasion. If a site actively blocks automated access, the tool treats that as a hard stop and skips the company rather than trying to work around it.
- Nothing is redistributed or published. Job data is used privately for personal job hunting, not resold, republished, or aggregated into a public feed.
- I don't plan to overwhelm the sites at all with a lot of requests. 

### Is it OK to use / fork this?

Yes, with a few caveats:
- This is provided as-is, for educational and personal use. No warranty, no guarantee any given connector still works by the time you read this (ATS platforms change endpoints without notice).
- Don't point this at platforms requiring login/auth, and don't try to work around rate limits or bot-detection if you hit them. That's the line where "reading public data" turns into something else.
- Company-specific config (Workday tenant URLs especially) will need to be rediscovered per company you add (see the Tiers section below).

### Features

- Unified job data format across all ATS connectors
- Tiered classification system for prioritizing relevant postings
- Keyword and location filtering (config-driven)
- Email notifications for new matches
- Deduplication so the same posting isn't reported twice

### Coming soon

- Scheduler for automated periodic runs
- Broader ATS coverage (Oracle Recruiting Cloud and others)
- Refined filtering logic

### Getting started

#### Requirements

Install dependencies:

```bash
pip install -r requirement.txt
```

#### Configuration

Copy the example env file and fill in your own values:

```bash
cp .env.example .env
```

See [`.env.example`](./.env.example) for the required variables.

Then copy the example config and set up which companies to track:

```bash
cp config.example.json config.json
```

See [`config.example.json`](./config.example.json) for the format, including how a "custom" ATS entry references a handler from `custom_handler_template.py`.

#### Running it

```bash
python job_monitor.py
```

New matches get logged to `seen_jobs.json` and emailed if they pass the filters. Already-seen postings are skipped on future runs.

### Custom Handlers

Some companies don't run on a standard ATS and need a bespoke handler to pull their job data. These real handlers aren't included in this repo, since they're built against a specific company's internal endpoints and publishing that publicly isn't something I'm comfortable doing.

What's included instead is [`custom_handler_template.py`](./custom_handler_template.py), which shows the general shape every custom handler follows (pagination pattern, request/response normalization, registration into `CUSTOM_HANDLERS`) without any real company's endpoint or response schema.

Every connector, custom or not, normalizes into the same shared job format, which is what lets the main pipeline treat all 10+ sources identically regardless of what's happening under the hood.

### Status

Actively in development. This is a work in progress, built incrementally as I learn more about each ATS and refine the classification logic.
