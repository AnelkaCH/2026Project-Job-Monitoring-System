# Changelog

## [Unreleased]
### What To Expect
- A working UI (dashboard MVP is out; a fuller UI is the next step)
- Scheduler
- Many more things to come :D

## [2026-08-11] v3.0.1 - Fixing CI Pipeline issue with custom handlers
### Added
- (none)

### Changed
- (none)

### Fixed
- Since the repository doesn't have `custom_handler.py`, the CI pipeline will fail. So, I made it optional in `job_monitor.py`.


## [2026-08-11] v3.0 - Dashboard MVP: Jobs Viewer
### Added
- `dashboard.py` - Streamlit dashboard that reads the enriched `seen_jobs.json` and renders a filterable table of every matched and ambiguous posting (company, title, date matched). Filters by company, tier, and date range. Data path is configurable via the `SEEN_JOBS_PATH` env var or the `--seen-jobs` CLI flag so forks can point it at their own file without code changes.
- `seen_jobs.json` enrichment - each company record now stores a `details` map (title, location, posted, posted_days_ago, link, ats, first_seen) alongside the dedup ID lists. `first_seen` records when a posting was first matched and is preserved across runs. Pre-v3.0 records are backfilled automatically on the next run.
- `tests/test_seen_jobs.py` - 6 tests covering the enrichment logic: first_seen stamping, preservation across runs, ambiguous tracking, ATS stamping, and pre-v3.0 backfill.
- `tests/test_dashboard.py` - 12 tests covering flattening (new and old formats), tier derivation, dataframe building (including tz normalization), date-range / company / ATS / tier filtering, and path resolution precedence.

### Changed
- `job_monitor.py` - persistence now writes the enriched record via `build_company_record()`; dedup identity (job ID per company) is unchanged.
- `requirement.txt` - added `streamlit` and `pandas` for the dashboard.
- `README.md` - Dashboard section, updated tech stack, test counts, and project structure.
- `ARCHITECTURE.md` - updated diagram, `seen_jobs.json` schema, dashboard component, data flow, and design decisions.

### Fixed
- `dashboard.py` - date-range filter raised `TypeError` on tz-aware match dates; `build_dataframe()` now normalizes `date_matched` to tz-naive so the widget dates compare correctly.

## [2026-07-28] v2.4 - CI Pipeline
### Added
- `.github/workflows/tests.yml` - GitHub Actions workflow that runs the full pytest suite (`pytest tests/ -v`) on every push and pull request to `main`. Installs runtime and dev dependencies in separate steps, matching the Getting Started docs. No secrets required as all 33 tests mock network and filesystem I/O.
- CI badge in `README.md` - Displays live passing/failing status of the last workflow run on `main`.

### Changed
- (none)

### Fixed
- (none)

## [2026-07-21] v2.3.2 - Example of Config.json Reformat
### Added
- (none)

### Changed
- Additional comments to `config.example.json` for more clarity.
- Adding template for jobs using SAP SuccessFactor as an ATS in the `config.example.json`.

### Fixed
- (none)

## [2026-07-20] v2.3.1 - Test Coverage Expansion
### Added
- `tests/test_robots_check.py` - 12 tests covering `RobotsChecker` (URL normalization, fetch success/URLError, allow/deny/caching/fail-conservative in `is_allowed`, custom user-agent) and `SkipReason` dataclass.
- `tests/test_audit_log.py` - 15 tests covering `check_hardstop()` (all 9 response-signal scenarios including the Workday exemption boundary, multiple-accumulation, and non-HTML keyword edge case), `log_audit_event()` (valid JSON shape), and `setup_logging()` (dir creation, handler attachment, idempotency via `tmp_path`).
- `requirements-dev.txt` - has the requirements specifically for testing

### Changed
- `tests/` - Both files use pytest-native style with fixtures and `unittest.mock`, consistent with the existing codebase convention.

### Fixed
- (none)

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
