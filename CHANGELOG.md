# Changelog

## [Unreleased]
### What To Expect
- A working UI
- Scheduler

## [2026-07-19] v2.3 - Tier 3 Hard-Stop Propagation
### Added
-(none)

### Changed
- `RateLimitExceeded` exception now accepts an optional `reason` field to distinguish rate-limits from bot-detection.
- `RateLimiter._request()` now raises `RateLimitExceeded(reason="bot-detection")` when `check_hardstop()` detects a CAPTCHA or bot-detection signal, instead of silently logging and returning the response.
- All 9 adapters in `connectors.py` and the custom handler in `custom_handlers.py` pass `exc.reason` through to `SkipReason`, so the orchestrator and email can distinguish bot-detection skips from rate-limit skips.
- `job_monitor.py` displays a dedicated `[SKIPPED] bot-detection triggered (Tier 3 hard-stop)` message instead of lumping hard-stop events under "rate-limited."

### Fixed
- `custom_handler_example.py` template now returns `SkipReason(exc.reason, str(exc))` instead of bare `None` when rate-limited, matching the real handlers' contract.

## [2026-07-19] v2.2.3 - Proper Documentation
### Added
- `CHANGELOG.md` - Holds every version notes.
- `ARCHITECTURE.md` - Explains the system's architecture.
- `LICENSE` - Holds the license for this project.
- `documentation/` - Holds screenshots of the project.

### Changed
- `README.md` - Explaining new versions -> giving the overview of the project.

### Fixed
- (none)

## [2026-07-17] v2.2.2 - Detect-Secrets Pre-Commit Hook
### Added
- `.pre-commit-config.yaml` - Configures the `detect-secrets` hook to run on every commit.
- `.secrets.baseline` - Snapshot of currently-flagged strings so the hook only alerts on new secrets.
- Pre-commit hook that scans staged changes for credentials or high-entropy strings before commits go through.

### Changed
- (none)

### Fixed
- (none)

## [2026-07-17] v2.2.1 - Package Restructuring
### Added
- `adapters/` package - All ATS fetch logic moved here: `connectors.py`, `custom_handlers.py`, `custom_handler_example.py`.
- `utils/` package - Shared infrastructure moved here: `audit_log.py`, `date_utils.py`, `rate_limiter.py`, `skip_tracker.py`, `robots_check.py`, `notifier.py`.
- `tests/` package - Unit tests with automatic `sys.path` resolution.

### Changed
- All imports across the codebase updated to use package-qualified paths (`from utils.rate_limiter import ...`).
- Internal references within `utils/` use relative imports where appropriate.
- Three files (`audit_log.py`, `skip_tracker.py`, `robots_check.py`) had `__file__`-relative path logic updated for the extra directory level.
- `job_monitor.py` stays in the project root as the main entrypoint.

### Fixed
- Path resolution in `audit_log.py` for log file location under the new directory depth.
- Path resolution in `skip_tracker.py` for `skip_history.json`.
- Path resolution in `robots_check.py` for `config.json` when run as a standalone CLI.

## [2026-07-16] v2.2 - Robots.txt Compliance
### Added
- `robots_check.py` - Compliance checker using `urllib.robotparser` (stdlib). Checks whether the target path is allowed for `user-agent: *` before every adapter's first API call. Fails conservative: if robots.txt is unreachable, treat as disallowed. Cached per domain for the process lifetime.
- `SkipReason` dataclass - Adapters return `SkipReason("robots.txt disallowed")` or `SkipReason("rate-limited")` instead of bare `None`.

### Changed
- All 9 adapters in `connectors.py` now check robots.txt and return `SkipReason` on disallow.
- `custom_handlers.py` - Accenture handler gets the same robots.txt check.
- `job_monitor.py` - Distinguishes robots.txt disallows from rate-limit skips in output.
- `notifier.py` - "Repeatedly rate-limited" wording updated to "Repeatedly skipped."

### Fixed
- (none)

## [2026-07-16] v2.1 - Audit Logging
### Added
- `audit_log.py` with three capabilities:
  - **Two log streams:** `logs/audit.log` (JSON lines, 5 MB x 5 backups) for security events; `logs/operational.log` (timestamped text, 5 MB x 3 backups) for routine output; plus a console handler for interactive runs.
  - **`log_audit_event(event_type, **fields)`** writes a JSON line with ISO 8601 timestamp and key-value fields.
  - **`check_hardstop(response, platform)`** inspects responses for bot-detection signals: HTTP 403, HTTP 503 (non-Workday), and `text/html` responses containing `captcha`, `robot`, `access denied`, or `blocked`.
- Three audit event types: `QUERY` (before adapter call), `TIER3_HARDSTOP` (on skip/error), `CLASSIFY` (match/ambiguous/new counts per company).

### Changed
- `rate_limiter.py` - Logs `TIER3_HARDSTOP` before raising `RateLimitExceeded` and when `check_hardstop()` finds indicators on a successful response.
- `job_monitor.py` - All `print()` calls replaced with `operational_logger.*()`. Calls `setup_logging()` at startup.
- `.gitignore` - Added `logs/`.

### Fixed
- (none)

## [2026-07-15] v2.0 - Rate Limiting and Skip Tracking
### Added
- `rate_limiter.py` - Core module with:
  - Per-company requests-per-minute cap tracked in a rolling 60-second window.
  - Exponential backoff with jitter on 429 or platform-specific throttle signals.
  - Retry-until-exhausted behavior that raises `RateLimitExceeded` instead of looping forever.
  - Platform-specific throttle detection (e.g., Workday's 503).
- `skip_tracker.py` - Persistent consecutive-cycle skip counter per company. Streaks reset to zero on success.
- `RateLimitExceeded` exception carrying `company`, `platform`, and `attempts` fields.

### Changed
- All 9 adapters in `connectors.py` route HTTP calls through `limiter.get()`/`limiter.post()` instead of raw `requests`. Return `None` on skip.
- `custom_handlers.py` - Accenture handler gets the same rate-limiter treatment.
- `job_monitor.py` - Handles `None` returns (skip) without crashing or wiping dedup baselines.
- `notifier.py` - Email now includes a flagged-companies amber-warning section when a skip streak crosses the threshold of 3.
- Return contract change: fetch functions can now return `None` (not checked) vs `[]` (checked, no jobs found). `None` skips the `seen_jobs.json` update so rate-limited companies don't have their history erased.

### Fixed
- (none)

## [2026-07-05] v1.0 - Initial Release
### Added
- Nine ATS adapters: Greenhouse, Ashby, Lever, Workable, Personio, SmartRecruiters, Recruitee (Tier 1), Workday, SAP SuccessFactors (Tier 2).
- Custom handler support - company-specific handlers for non-standard ATS platforms, registered via `CUSTOM_HANDLERS` dict.
- Unified 6-field job schema (`id`, `title`, `location`, `posted`, `posted_days_ago`, `link`) normalized across all adapters.
- Three-tier classification system with config-driven keyword, location, exclude-keyword, and age filters.
- Deduplication via `seen_jobs.json` with separate `matched_ids` and `ambiguous_ids` tracking.
- HTML email notifications via Gmail SMTP (`smtplib.SMTP_SSL`).
- Config-driven setup via `config.json` (companies, filters) and `.env` (email credentials).
- Date parsing utilities supporting ISO 8601, Unix ms, DD/MM/YYYY, and relative text formats.

### Changed
- (none)

### Fixed
- (none)
