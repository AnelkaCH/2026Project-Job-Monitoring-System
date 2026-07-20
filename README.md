# Job Monitoring System

A self-hosted, zero-cost tool that watches specific companies' job boards and tells you when they post something new, instead of manually checking a dozen careers pages every day.

## Why I Built This

I was tracking internship and job openings across cybersecurity, cloud, and tech companies in Singapore as part of my own job search. Rather than checking each company's careers page by hand, I built this to automate the process while staying within clear technical and ethical boundaries. It is also a portfolio project demonstrating engineering judgment around API usage, rate limits, and respecting what platforms intend to expose publicly.

## Screenshots / Demo

### Logs

![Logs](/documentation/image.png)

### Email Notification

![Notification](/documentation/image1.png)

## Documentation

- [Architecture](./ARCHITECTURE.md) -- design decisions and system structure
- [Changelog](./CHANGELOG.md) -- version history

## Features

- **Adapter pattern** supporting 10+ ATS platforms (Greenhouse, Lever, Ashby, SmartRecruiters, Recruitee, Workable, Personio, Workday, SAP SuccessFactors, plus custom handlers)
- **Tiered classification** -- config-driven keyword, location, exclude-keyword, and age filters sort jobs into match / ambiguous / no_match tiers
- **Deduplication** -- previously seen job IDs are stored in `seen_jobs.json` so the same posting is never reported twice
- **Email notifications** -- HTML email alerts for new matches, ambiguous jobs, and repeatedly skipped companies
- **Robots.txt compliance** -- every request is preceded by a robots.txt check; paths are treated as disallowed if the file is unreachable
- **Rate limiting** -- per-company requests-per-minute cap with exponential backoff and jitter on throttle signals
- **Audit logging** -- dual-stream structured logging (JSON audit events + timestamped operational output) with file rotation
- **Skip tracking** -- persistent consecutive-skip counter per company; streaks of 3+ trigger a flag in the email
- **Detect-secrets pre-commit hook** -- scans staged changes for credentials and high-entropy strings before commits go through

## Tech Stack

`Python` `requests` `python-dotenv`

## How It Works

The system runs on a scheduled GitHub Actions job (not yet available). Each cycle, it iterates through configured companies, dispatches to the appropriate ATS adapter, checks robots.txt compliance, and fetches job listings through the rate limiter. Results are classified by keyword and location filters, deduplicated against previously seen postings, and new matches trigger an HTML email notification. Companies that fail repeatedly (rate-limited, bot-detected, or robots.txt-disallowed) are flagged for manual review after three consecutive skips.

## Getting Started

### Prerequisites

- Python 3.x
- A Gmail account with an [App Password](https://support.google.com/accounts/answer/185833) for email notifications

### Installation

```bash
git clone https://github.com/AnelkaCH/2026Project-Job-Monitoring-System.git
cd 2026Project-Job-Monitoring-System
pip install -r requirement.txt
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

33 tests across three modules covering the security-critical infrastructure: rate limiter (6 tests), robots.txt compliance checker (12 tests), and audit logging / hard-stop detection (15 tests). Tests use `unittest.mock` to avoid real network or filesystem I/O and are safe to run without configuration.

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

## Project Structure

```
JobMonitoring/
  job_monitor.py              Main orchestrator / entrypoint
  config.json                 Company list and filter configuration
  config.example.json         Template config for new users
  .env                        Email credentials (not committed)
  .env.example                Template for email credentials
  seen_jobs.json              Deduplication state (persisted)
  skip_history.json           Skip streak state (persisted)
  requirement.txt             Dependencies
  LICENSE                     MIT License
  adapters/                   ATS-specific fetch logic
    connectors.py             9 standard ATS adapters
    custom_handlers.py        Private custom handlers (e.g., Accenture)
    custom_handler_example.py Template for new custom handlers
  utils/                      Shared infrastructure
    rate_limiter.py           Per-company rate limiting + backoff
    skip_tracker.py           Cross-cycle skip streak tracker
    robots_check.py           Robots.txt compliance checker
    audit_log.py              Dual-stream audit + operational logging
    notifier.py               HTML email notifications via Gmail SMTP
    date_utils.py             Date format converters per ATS
  tests/                      Unit tests (33 total)
    test_rate_limiter.py      Rate limiter tests (6)
    test_robots_check.py      Robots.txt compliance tests (12)
    test_audit_log.py         Audit log and hard-stop tests (15)
  logs/                       Runtime log files (gitignored)
  documentation/              Holds documentation and screenshots
```

## Known Issues

- **Location filtering** -- There is a known problem with multiple locations in a single listing (e.g., "Singapore, Hong Kong") not reliably matching the configured location filter.
- **Stricter robots.txt on some ATS** -- SAP SuccessFactors, SmartRecruiters, Ashby, and Workable currently disallow the endpoints used by this system. The adapters remain in place in case the platforms update their policies.

## License

This project is licensed under the MIT License -- see [LICENSE](./LICENSE) for details.

## Contact

Anelka Cornelius Hariyanto -- [LinkedIn](https://www.linkedin.com/in/anelka-hariyanto/) -- [GitHub: AnelkaCH](https://github.com/AnelkaCH)
