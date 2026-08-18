# Changelog

## [Unreleased]
### What To Expect
- A working UI (dashboard MVP is out; a fuller UI is the next step)
- Scheduler
- Many more things to come :D

## [2026-08-18] v3.3.2 - Configurable Entrypoint & Demo Mode
### Added
- `job_monitor.py` - `argparse` CLI flags: `--config PATH` (default: the repo-anchored `config.json`) and `--db-path PATH` (default: `DB_PATH` env var, else `data/jobmonitor.db`). Running with no flags behaves exactly as before; the flags let anyone point the monitor at a different config file and database.
- `demo_config.json` - A committed demo config tracking 5 well-known public companies on real Greenhouse and Lever endpoints (Airbnb, Coinbase, Reddit, Binance, Zoox) with generic demo filters, so a fresh clone can produce real output without the private `config.json` or `.env`.
- `README.md` - New "Try it with real public demo companies" subsection under Getting Started: `python job_monitor.py --config demo_config.json --db-path demo.db`, serving the API against it (`DB_PATH=demo.db uvicorn api.main:app --reload`), a `curl` example against `/jobs`, and pointing the dashboard at the same database.

### Changed
- `utils/notifier.py` - `send_notification()` now skips gracefully with a console notice when `EMAIL_ADDRESS` / `EMAIL_APP_PASSWORD` / `RECIPIENT_EMAIL` are not configured, instead of raising `KeyError`. Email still sends whenever credentials are present, so the flagged-companies trigger logic is unchanged.
- `.gitignore` - Added `demo.db` (runtime output of demo mode). `demo_config.json` stays committed.
- `dashboard.py` - No code change needed: it already resolves the database via `DB_PATH` (loaded from `.env`) or the `--db-path` flag, so it can point at `demo.db` for the demo.

### Fixed
- (none)

## [2026-08-18] v3.3.1 - FastAPI Read Layer
### Added
- `api/` package - A thin, read-mostly FastAPI layer over `db/repository.py` with no new business logic:
  - `api/main.py` - App instance mounting the routers below plus the single write endpoint `POST /config/keywords`, which validates a non-empty list of strings (422 on bad input via Pydantic) and writes it into `role_keywords`, `domain_keywords`, and `exclude_keywords` in `config.json`.
  - `api/schemas.py` - Pydantic models: `JobOut`, `CompanyStatusOut`, and `KeywordsIn`.
  - `api/routes_jobs.py` - `GET /jobs` (optional `company` / `keyword` filters, empty list rather than an error) and `GET /jobs/{job_id}` (404 JSON body when not found).
  - `api/routes_companies.py` - `GET /companies` and `GET /companies/{name}` merging `config.json`'s company list with the `skip_streaks` table and a per-company `last_checked` derived from the most recent `first_seen_at`; 404 when a company is not tracked.
- `db/repository.py` - `get_job()` (most recent posting for a job id) and `list_company_last_checked()` (aggregate `MAX(first_seen_at)` per company).
- `tests/test_api.py` - 10 tests via FastAPI `TestClient` covering the 200 / 404 / 422 cases across all routes, using a temporary database (`DB_PATH`) and a temporary config so the gitignored real files are never touched.
- `requirements.txt` - Added `fastapi` and `uvicorn`.
- `requirements-dev.txt` - Added `httpx` (Starlette `TestClient` dependency).
- `README.md` - New "Running the API" section with the `uvicorn api.main:app --reload` command and one example curl per route; tech stack, test counts, and project structure updated.

## [2026-08-18] v3.3 - SQLite Persistence
### Added
- `db/` package - stdlib `sqlite3` persistence layer replacing the two flat JSON state files:
  - `db/schema.py` - `init_db()` creates the `jobs` and `skip_streaks` tables if they do not exist (idempotent, safe to call every run). The `jobs` table keeps the existing dedup identity as its primary key: `(company, job_id, tier)`, with matched and ambiguous postings tracked separately exactly like the old `matched_ids` / `ambiguous_ids` lists.
  - `db/repository.py` - Plain functions for reads and writes (`mark_job_seen()`, `is_job_seen()`, `list_jobs()`, `record_skip()`, `get_skip_streak()`, `reset_skip_streak()`, `list_skip_streaks()`). Every call opens its own short-lived connection (with a timeout) so concurrent adapter workers never share a handle, and `init_db()` runs before each call so tables always exist. `DB_PATH` env var overrides the repo-relative default `data/jobmonitor.db`.
- `tests/test_db.py` - 9 tests covering table creation and idempotency, dedup (`is_job_seen`, tier separation, `first_seen_at` preserved across re-marks), `list_jobs` company/keyword filtering, and skip streak increment/reset.
- `DB_PATH` entry in `.env.example` - Location of the SQLite database, defaulting to `data/jobmonitor.db` when unset.

### Changed
- `job_monitor.py` - `load_seen_jobs()`, `save_seen_jobs()`, and `build_company_record()` removed; dedup state is now read and written through `db/repository.py`. `check_company()` and `log_company_result()` take a `db_path` instead of the shared `seen_jobs` dict, and `log_company_result()` persists matched and ambiguous postings via `mark_job_seen()` (keeping `first_seen_at` for postings already seen). Classification, rate-limiting, robots.txt, and adapter logic are unchanged.
- `utils/skip_tracker.py` - Consecutive skip streaks now persist in the `skip_streaks` table via `db/repository.py` instead of `data/skip_history.json`. The JSON file and its module-level lock are gone; the constructor takes `db_path` instead of `path` (all adapters call it with no arguments, so they are unchanged).
- `dashboard.py` - Reads postings from the SQLite database via `repository.list_jobs()` instead of `seen_jobs.json`; the path is configurable via `DB_PATH` / `--db-path` instead of `SEEN_JOBS_PATH` / `--seen-jobs`. `flatten_rows()` replaces `flatten_seen_jobs()` and maps database rows to the same row shape the dataframe and filters expect.
- `tests/test_dashboard.py` - Flattening and path-resolution tests updated for the database row shape and `DB_PATH` / `--db-path`.
- `tests/test_matching.py` - The concurrency aggregation test now writes through the repository to a temporary database instead of a shared dict.
- `tests/test_seen_jobs.py` removed - Its 6 tests covered the deleted `build_company_record()`; equivalent dedup and `first_seen` preservation coverage lives in `tests/test_db.py`.
- `ARCHITECTURE.md` - Persistence, skip tracking, dedup, dashboard, and data-flow sections updated for the SQLite layer.
- `README.md` - Dedup, dashboard, config, testing, and project-structure sections updated for the database.
- `.gitignore` - Added `jobmonitor.db`, `data/jobmonitor.db`, and `db/jobmonitor.db`.

### Fixed
- `dashboard.py` - The dashboard and the monitor could resolve different DB paths: the monitor loaded `.env` (via `notifier.py`) while the dashboard never did, so the dashboard read an empty store next to a populated one. `db/repository.py` now calls `load_dotenv()` itself, so every consumer (monitor, dashboard, adapters) resolves `DB_PATH` identically.

## [2026-08-16] v3.2.1 - Test Config Fixture
### Added
- `tests/fixtures/test_config.json` - Minimal, repo-safe config fixture (generic filters and a single company, no real or private handler data) so tests do not depend on the gitignored local `config.json`.

### Changed
- `job_monitor.py` - `load_config()` now accepts an optional `config_path` argument (defaults to `CONFIG_FILE`, no behavior change).
- `tests/test_matching.py` - Filters are loaded from the fixture via `load_config(FIXTURE_CONFIG)` instead of the gitignored `config.json`. Removed `test_ensign_intern_still_matches` and `test_gsk_internship_role_signal_present`, which asserted against the real company keyword lists and no longer apply to a generic fixture. Module now 8 tests.

### Fixed
- `tests/test_matching.py` - `FileNotFoundError` when `config.json` is absent (it is gitignored and not present in the repo or CI).

## [2026-08-16] v3.2 - Parallel Execution & Role/Domain Keyword Split
### Added
- `utils/matching.py` - Word-boundary keyword matching via `keyword_matches()` (a term like "engineer" no longer trips on partial overlaps such as "Software Engineering") plus `has_date_range_signal()` for fixed-term internship titles that carry a month-year range in parentheses or brackets instead of a literal role keyword.
- `tests/test_matching.py` - 10 tests covering word-boundary matching, trailing-space config entries, case-insensitivity, date-range signals (paren and bracket styles, bare-year rejection), live-config false-positive regression cases, and a concurrency aggregation test exercising the ThreadPoolExecutor path.
- `tests/test_robots_check.py` - `TestFetchParser` tests rewritten for the `requests`-based fetch, covering the new branches: network-error fail-conservative, 401/403 explicit denial, 404 allow-all per RFC 9309, and 5xx retry-then-disallow (module now 15 tests).

### Changed
- `job_monitor.py` - Companies are fetched and classified concurrently via a `ThreadPoolExecutor` (capped at 15 workers via `MAX_CONCURRENT_COMPANIES`). Fetch and classify logic moved into `check_company()`, which returns a structured result; logging and aggregation happen in the main thread via `log_company_result()`, so shared lists and the `seen_jobs` dict need no locks.
- `job_monitor.py` - The single combined `keywords` filter is replaced with `role_keywords` plus `domain_keywords`. A posting now matches only when at least one role keyword and at least one domain keyword appear in the title (word-boundary regex) and no exclude keyword matches. A date-range signal in an internship title can satisfy the role half of the check.
- `config.example.json` - The `keywords` template entry is replaced with separate `role_keywords` and `domain_keywords` templates.
- `utils/rate_limiter.py` - The per-company tracker registry is guarded by a `threading.Lock` for the new concurrent workers.
- `utils/robots_check.py` - `robots.txt` is now fetched via `requests` with an identifying User-Agent instead of the stdlib `urllib.robotparser` reader. Network errors and server-side 5xx responses retry with exponential backoff (default 1 retry) and then fail conservative (treated as disallowed) if they persist. A 401 or 403 is treated as disallowed; a 404 or any other 4xx is treated as allow-all per RFC 9309. The parser cache is guarded by a lock.
- `utils/skip_tracker.py` - Shared `skip_history.json` writes are guarded by a module-level lock so concurrent adapter workers do not race on the file.
- All adapters - Loggers consolidated to the shared `job_monitor.operational` stream so concurrent workers write to one rotating operational log.
- `ARCHITECTURE.md` - Updated for the role/domain keyword split, concurrency model, and new robots.txt behavior.
- `README.md` - Updated filter semantics, test counts, and project structure.

### Fixed
- `utils/robots_check.py` - `robots.txt` is no longer fetched without an identifying User-Agent.

## [2026-08-14] v3.1.2 - Project Structure Refactor
### Added
- `static/` directory - `win95_theme.css` moved here from the project root to keep UI assets co-located with the dashboard instead of sitting beside source modules.
- `data/` directory - Runtime-generated state files (`seen_jobs.json`, `skip_history.json`) moved here from the project root. The directory is tracked via a `.gitkeep`; the files themselves remain gitignored per the existing policy.
- `adapters/greenhouse.py`, `adapters/lever.py`, `adapters/ashby.py`, `adapters/smartrecruiters.py`, `adapters/recruitee.py`, `adapters/workable.py`, `adapters/personio.py`, `adapters/workday.py`, `adapters/sap.py` - Each ATS adapter is now its own module, consistent with the adapter pattern described in `ARCHITECTURE.md`. Each file owns its own `logger`, `limiter`, and `skip_tracker`, matching the pattern established by `custom_handler_example.py`.

### Changed
- `adapters/connectors.py` - Reduced from a 546-line all-in-one file to a 26-line registry that imports each adapter from its own module and assembles the `CONNECTORS` dict. No behavior change; all imports in `job_monitor.py` remain unchanged.
- `dashboard.py` - `DASHBOARD_CSS_PATH` updated to `static/win95_theme.css`; `DEFAULT_SEEN_JOBS_FILE` updated to `data/seen_jobs.json`.
- `job_monitor.py` - `SEEN_JOBS_FILE` updated to `data/seen_jobs.json`.
- `utils/skip_tracker.py` - `DEFAULT_PATH` updated to `data/skip_history.json` so both state files remain co-located.
- `.gitignore` - Entries updated from `seen_jobs.json` / `skip_history.json` to `data/seen_jobs.json` / `data/skip_history.json`. Added `data/*` / `!data/.gitkeep` block.
- `requirement.txt` renamed to `requirements.txt` (standard convention). `.github/workflows/tests.yml` updated to match.
- `documentation/image.png` renamed to `documentation/operational_logs_example.png`.
- `documentation/image1.png` renamed to `documentation/email_notification_example.png`.

### Fixed
- (none)

## [2026-08-14] v3.1.1 - Dashboard Theming Pass
### Added
- `win95_theme.css` - Standalone Win95-chrome stylesheet (kept separate from the script for maintainability and wrapped in a `<style>` tag at injection time): light gray `#F0F0F0` main surface with classic gray `#C0C0C0` chrome, raised/inset beveled borders, Tahoma-led system font stack, chunky scrollbars, and ListView-style table chrome (white cells, gray header).
- `.streamlit/config.toml` - Light base with the dataframe theme keys set: `backgroundColor` `#FFFFFF` (white table cells), `dataframeHeaderBackgroundColor` `#C0C0C0` (classic gray header), `dataframeBorderColor` `#C0C0C0` (grid lines), `secondaryBackgroundColor` `#F0F0F0` (light gray surface), `#000000` text, navy `#000080` primary, distinct `[theme.sidebar]` gray.
- `dashboard.py` - `load_dashboard_css()` reads `win95_theme.css` and wraps it in a `<style>` tag; a missing stylesheet renders the dashboard unstyled rather than crashing it. A trust-boundary note documents why `unsafe_allow_html` is safe: the markup is our own static CSS, never ATS-sourced posting text.
- `dashboard.py` - Retro title bar (solid dark blue `#000080`, white bold text, fake window buttons) via `render_title_bar()`.
- `dashboard.py` - Metric tiles restyled as Win95 group boxes (raised bevel, sunken line, small-caps label on the top border, bold black value) via `render_metric_card()`.
- `dashboard.py` - Sticky functional taskbar (`st.container(key="taskbar")`) with two direct buttons: **Export CSV** (download button) and **About** (Win95-style popover with version, purpose, and source path), plus a live status line: `Ready | N companies | M ATS | showing X of Y postings | modified <time> | <path>`. Sticky (not fixed) so it never covers the table.
- `dashboard.py` - Sidebar filters gained widget keys (`company_filter`, `tier_filter`, `date_filter`) so Reset can clear them. UI-state only.

### Changed
- `dashboard.py` - The "Could not read seen_jobs.json" failure now renders as a tinted inset badge card instead of the default full-width `st.error` box. Cosmetic only; no data flow or behavior change.
- `dashboard.py` - Previous cyan-blue accent redesign replaced by the Win95 chrome; company-name monospace Styler removed so the retro font applies app-wide.
- `dashboard.py` - The standalone "Monitoring N companies" status badge folded into the taskbar status line; `render_badge()` and the `.badge-card` CSS remain for the error path and future skip-streak / Tier 3 hard-stop indicators.
- `.streamlit/config.toml` - The postings table reads as a classic Win95 ListView: white cells, gray header, gray grid lines, no rounded corners. Streamlit's dataframe canvas colors come from the theme (not CSS), so the fix lives in the theme keys (`backgroundColor`, `dataframeHeaderBackgroundColor`, `dataframeBorderColor`).
- `win95_theme.css` - Dataframe styling updated for Streamlit 1.61's Glide Data Grid: the old AG Grid selectors (`.ag-header`, `thead th`, `td`) no longer match the DOM, so the grid's `--gdg-*` theme variable overrides (`bg-header`, `bg-cell`, `border-color`) replace them. The table container and every inner element are forced square (`border-radius: 0`) so no rounded corners remain.

### Fixed
- `dashboard.py` - The injected CSS was missing a `<style>` HTML wrapper, so `st.markdown` rendered the raw stylesheet as literal text at the top of the dashboard instead of applying it. The stylesheet is now wrapped in `<style>` tags, and a regression test guards against it.
- `dashboard.py` - `st.popover` raised `StreamlitAPIException` because `:material/windows:` is not a valid Material icon; the About popover now uses `:material/info:` (Refresh uses `:material/refresh:`, Export uses `:material/download:`).
- `.streamlit/config.toml` - The table no longer renders as the same gray as the page: the previous `backgroundColor` (`#C0C0C0`) was inherited by both the header and the cells; the cells are now white while the header stays classic gray.

## [2026-08-12] v3.1 - Input Validation & Sanitization (on API responses)
### Added
- `utils/schema.py` - One pydantic model per ATS that validates the raw response shape each ATS actually returns, plus `JobPosting` (the shared normalized 6-field dict) and the `ALLOWED_LINK_DOMAINS` allowlist. `validate_raw_jobs()` and `validate_job_posting()` are the two gates every adapter routes data through.
- **HTML sanitization at storage time** - Description-bearing fields (when present) plus the stored `title`/`location` fields are tag-stripped with `bleach.clean()` (tags stripped, not escaped), so `seen_jobs.json` never holds raw markup.
- **URL validation** - Job links must be `https` and point at the expected ATS domain (exact match, or subdomain suffix for `*.recruitee.com`, `*.myworkdayjobs.com`, `*.jobs.personio.{de,com}`). SAP and Workday links are config-derived, so their expected domain is supplied at call time.
- **`VALIDATION_REJECTED` audit event** - Any response item or normalized dict that fails validation is logged via `log_audit_event()` with a reason (`response_shape`, `schema_violation`, or `url_rejected`) and dropped, never silently stored.
- `tests/test_schema.py` - 48 tests covering every raw model (valid pass, missing required field dropped + audited), raw HTML stripping, normalized-gate sanitization, URL scheme/domain rules (including subdomain suffix and SAP config-derived domains), empty-link passthrough, and the local-model pattern custom handlers use.

### Changed
- `adapters/connectors.py` - All 9 connectors validate each API response with its ATS model right after `response.json()` (inside pagination loops where applicable) and pass every normalized dict through `validate_job_posting()` before appending.
- `adapters/custom_handler_example.py` - Template now models the bespoke response schema with a local pydantic model, validates the feed through `validate_raw_jobs()` before parsing, passes every normalized dict through `validate_job_posting()`, and includes the robots.txt compliance check, so new custom handlers built from it follow the same validation and compliance posture as the standard ATS connectors.
- `requirement.txt` - Added `pydantic` and `bleach`.
- `dashboard.py` - Comment documenting that Streamlit auto-escapes cell text by default and `unsafe_allow_html` is intentionally never used (defense-in-depth on top of storage-time sanitization). No behavior change.

### Fixed
- `adapters/connectors.py` - SAP connector previously wrote the `posted` field twice in its normalized dict (duplicate key); the redundant entry is removed.

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
