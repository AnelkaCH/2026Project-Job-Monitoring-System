# Job Monitoring System

[![Tests](https://github.com/AnelkaCH/2026Project-Job-Monitoring-System/actions/workflows/tests.yml/badge.svg)](https://github.com/AnelkaCH/2026Project-Job-Monitoring-System/actions/workflows/tests.yml)

A self-hosted, zero-cost tool that watches specific companies' job boards and tells you when they post something new, instead of manually checking a dozen careers pages every day.

## Why I Built This

I was tracking internship and job openings across cybersecurity, cloud, and tech companies in Singapore as part of my own job search. Rather than checking each company's careers page by hand, I built this to automate the process while staying within clear technical and ethical boundaries. It is also a portfolio project demonstrating engineering judgment around API usage, rate limits, and respecting what platforms intend to expose publicly.

## Screenshots / Demo

### Logs

![Logs](/documentation/operational_logs_example.png)

### Email Notification

![Notification](/documentation/email_notification_example.png)

## Documentation

- [Architecture](./ARCHITECTURE.md): design decisions and system structure
- [Changelog](./CHANGELOG.md): version history

## Features

- **Interactive dashboard**: a Streamlit jobs viewer that turns the match history into a filterable table (company, title, date matched)
- **Adapter pattern** supporting 10+ ATS platforms (Greenhouse, Lever, Ashby, SmartRecruiters, Recruitee, Workable, Personio, Workday, SAP SuccessFactors, plus custom handlers)
- **Tiered classification**: config-driven role+domain keyword AND logic, location, exclude-keyword, and age filters sort jobs into match / ambiguous / no_match tiers
- **Deduplication**: previously seen job IDs are stored in `seen_jobs.json` so the same posting is never reported twice
- **Email notifications**: HTML email alerts for new matches, ambiguous jobs, and repeatedly skipped companies
- **Robots.txt compliance**: every request is preceded by a robots.txt check; paths are treated as disallowed if the file is unreachable
- **Rate limiting**: per-company requests-per-minute cap with exponential backoff and jitter on throttle signals
- **Audit logging**: dual-stream structured logging (JSON audit events + timestamped operational output) with file rotation
- **Skip tracking**: persistent consecutive-skip counter per company; streaks of 3+ trigger a flag in the email
- **Detect-secrets pre-commit hook**: scans staged changes for credentials and high-entropy strings before commits go through
- **CI pipeline**: GitHub Actions workflow runs the full test suite on every push and PR to `main`; live status badge in this README

## Tech Stack

`Python` `requests` `python-dotenv` `streamlit` `pandas` `pydantic` `bleach`

## How It Works

The system runs on a scheduled GitHub Actions job (not yet available). Each cycle, it checks configured companies concurrently, dispatches to the appropriate ATS adapter, checks robots.txt compliance, and fetches job listings through the rate limiter. Every ATS response is validated against a per-platform schema and sanitized (HTML tags stripped with `bleach`, links checked to be `https` on the expected domain) before normalization, with anything rejected logged as a `VALIDATION_REJECTED` audit event. Results are classified by role and domain keyword AND logic plus location and age filters, deduplicated against previously seen postings, and new matches trigger an HTML email notification. Companies that fail repeatedly (rate-limited, bot-detected, or robots.txt-disallowed) are flagged for manual review after three consecutive skips.

## Dashboard

A Streamlit app (`dashboard.py`) gives the match history a usable face: a filterable table of every matched and ambiguous posting, showing company, title, location, and the date each posting was first matched. Filter by company, tier (match / ambiguous), or date range.

```bash
streamlit run dashboard.py
```

The dashboard reads `seen_jobs.json`, and the path is configurable so anyone who forks the repo can point it at their own data without editing code:

```bash
SEEN_JOBS_PATH=/path/to/seen_jobs.json streamlit run dashboard.py
streamlit run dashboard.py -- --seen-jobs /path/to/seen_jobs.json
```

## Getting Started

### Prerequisites

- Python 3.x
- A Gmail account with an [App Password](https://support.google.com/accounts/answer/185833) for email notifications

### Installation

```bash
git clone https://github.com/AnelkaCH/2026Project-Job-Monitoring-System.git
cd 2026Project-Job-Monitoring-System
pip install -r requirements.txt
```

### Configuration

Copy the example env file and fill in your own values:

```bash
cp .env.example .env
```

See [`.env.example`](./.env.example) for the required variables (EMAIL_ADDRESS, EMAIL_APP_PASSWORD, RECIPIENT_EMAIL).

Then copy the example config and set up which companies to track:

```bash
cp config.example.json config.json
```

See [`config.example.json`](./config.example.json) for the format, including how a "custom" ATS entry references a handler.

### Testing

Install dev dependencies:
```bash
pip install -r requirements-dev.txt
```

Run all tests with:
```bash
pytest tests/
```

118 tests across seven modules covering the security-critical infrastructure, the dashboard data layer, and keyword matching: rate limiter (6 tests), robots.txt compliance checker (15 tests), audit logging / hard-stop detection (15 tests), seen_jobs enrichment (6 tests), dashboard flattening, filtering, and path resolution (18 tests), input validation / sanitization (48 tests, one skipped by design for the SAP optional-title case), and keyword matching / concurrent aggregation (10 tests). Tests use `unittest.mock` to avoid real network or filesystem I/O and are safe to run without configuration.

### Optional: Pre-Commit Hook

If you plan to contribute, install the detect-secrets pre-commit hook:

```bash
pip install pre-commit detect-secrets --break-system-packages
pre-commit install
```

### Usage

```bash
python job_monitor.py
```

New matches get logged to `seen_jobs.json` and emailed if they pass the filters. Already-seen postings are skipped on future runs.

To view the tracked postings in the dashboard:

```bash
streamlit run dashboard.py
```

## Project Structure

```
JobMonitoring/
  job_monitor.py              Main orchestrator / entrypoint
  dashboard.py                Streamlit dashboard (jobs viewer)
  config.json                 Company list and filter configuration
  config.example.json         Template config for new users
  .env                        Email credentials (not committed)
  .env.example                Template for email credentials
  requirements.txt            Runtime dependencies
  LICENSE                     MIT License
  adapters/                   ATS-specific fetch logic
    connectors.py             Registry: maps ATS names to fetch functions
    greenhouse.py             Greenhouse adapter
    lever.py                  Lever adapter
    ashby.py                  Ashby adapter
    smartrecruiters.py        SmartRecruiters adapter
    recruitee.py              Recruitee adapter
    workable.py               Workable adapter
    personio.py               Personio adapter
    workday.py                Workday adapter
    sap.py                    SAP SuccessFactors adapter
    custom_handlers.py        Private custom handlers (gitignored)
    custom_handler_example.py Template for new custom handlers
  utils/                      Shared infrastructure
    rate_limiter.py           Per-company rate limiting + backoff
    skip_tracker.py           Cross-cycle skip streak tracker
    robots_check.py           Robots.txt compliance checker
    audit_log.py              Dual-stream audit + operational logging
    notifier.py               HTML email notifications via Gmail SMTP
    date_utils.py             Date format converters per ATS
    schema.py                 Input validation and sanitization
    matching.py               Word-boundary keyword matching helpers
  tests/                      Unit tests (118 total, 1 skipped by design)
    test_rate_limiter.py      Rate limiter tests (6)
    test_robots_check.py      Robots.txt compliance tests (15)
    test_audit_log.py         Audit log and hard-stop tests (15)
    test_seen_jobs.py         seen_jobs enrichment tests (6)
    test_dashboard.py         Dashboard data-layer tests (18)
    test_schema.py            Input validation / sanitization tests (48)
    test_matching.py          Keyword matching / concurrency tests (10)
  static/                     UI assets for the dashboard
    win95_theme.css           Win95-chrome stylesheet
  data/                       Runtime state files (gitignored)
    seen_jobs.json            Deduplication state (persisted)
    skip_history.json         Skip streak state (persisted)
  logs/                       Runtime log files (gitignored)
  documentation/              Screenshots and supporting images
  .github/
    workflows/
      tests.yml               CI workflow: runs pytest on push and PR to main
```

## Known Issues

- **Location filtering**: There is a known problem with multiple locations in a single listing (e.g., "Singapore, Hong Kong") not reliably matching the configured location filter.
- **Stricter robots.txt on some ATS**: SAP SuccessFactors, SmartRecruiters, Ashby, and Workable currently disallow the endpoints used by this system. The adapters remain in place in case the platforms update their policies.

## License

This project is licensed under the MIT License. See [LICENSE](./LICENSE) for details.

## Contact

Anelka Cornelius Hariyanto, [LinkedIn](https://www.linkedin.com/in/anelka-hariyanto/), [GitHub: AnelkaCH](https://github.com/AnelkaCH)
