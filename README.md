# Job Monitor System

A self-hosted, zero-cost tool that watches specific companies' job boards and tells me when they post something new, instead of me manually checking a dozen careers pages every day.

> **Status: early work in progress.** This is not a finished product. Connectors, filtering, and scheduling are all being built incrementally. Expect breaking changes, missing features, and rough edges. See [Roadmap](#roadmap) for what's actually done vs planned.

## Why I built this

I'm tracking internship and job openings at a specific list of Singapore-based companies across cybersecurity, cloud, and tech, as part of my own job search. Rather than checking each company's careers page by hand, this project automates that while staying within clear technical and ethical boundaries (see below).

It's also a portfolio project. I want to demonstrate reasonable engineering judgment around API usage, rate limits, and respecting what platforms actually intend to expose publicly.

## Why this exists

Most companies publish their openings through one of a handful of ATS platforms, many of which expose a structured JSON endpoint for their own careers page. Rather than scraping HTML per-company, this project builds a unified connector layer: one adapter per ATS, all normalized into the same job format, so adding a new company is usually just a config entry, not new code.

## ATS Coverage

**Tier 1 — Full JSON API support**
Greenhouse, Ashby, Lever, Workable, Personio, SmartRecruiters, Recruitee

These platforms expose clean, public JSON endpoints for their careers pages. Connectors here are stable and reusable across any company using that ATS.

**Tier 2 — Partial or non-standard support**
Workday, SAP SuccessFactors, Custom

These either require more complex request handling (pagination quirks, non-obvious endpoints, session tokens) or, in the case of "Custom," a bespoke handler for a company that doesn't run on a standard ATS. See [Custom Handlers](#custom-handlers) below for why this isn't public.

**Tier 3 — Not currently supported**
Companies with no accessible JSON endpoint. These would require HTML scraping or browser automation, which is out of scope for now. Skipped on purpose.

**On the roadmap**
Oracle Recruiting Cloud (Fusion), and other ATS platforms as they come up.

## Is this safe / legal?

Some notes on how this is built to stay on the right side of that line, though I'm not a lawyer and this isn't legal advice:

- It only reads job postings that are already public on each company's own careers page — nothing behind a login, nothing requiring authentication bypass.
- Tier 1 connectors use the same JSON endpoints the company's own careers page calls in the browser. This is the same data a human visitor would see, just fetched programmatically instead of clicked through.
- Requests are rate-limited and spaced out. This is a personal tool checking a handful of times a day, not a high-frequency scraper.
- No CAPTCHA solving, no bot-detection evasion. If a site actively blocks automated access, the tool treats that as a hard stop and skips the company rather than trying to work around it.
- Nothing is redistributed or published. Job data is used privately for personal job hunting, not resold, republished, or aggregated into a public feed.
- I don't plan to overwhelm the sites at all with a lot of requests. 

## Is it OK to use / fork this?

Yes, with a few caveats:
- This is provided as-is, for educational and personal use. No warranty, no guarantee any given connector still works by the time you read this (ATS platforms change endpoints without notice).
- Don't point this at platforms requiring login/auth, and don't try to work around rate limits or bot-detection if you hit them. That's the line where "reading public data" turns into something else.
- Company-specific config (Workday tenant URLs especially) will need to be rediscovered per company you add (see the Tiers section below).

## Features

- Unified job data format across all ATS connectors
- Tiered classification system for prioritizing relevant postings
- Keyword and location filtering (config-driven)
- Email notifications for new matches
- Deduplication so the same posting isn't reported twice

## Coming soon

- Scheduler for automated periodic runs
- Broader ATS coverage (Oracle Recruiting Cloud and others)
- Refined filtering logic

## Getting started

### Requirements

Install dependencies:

```bash
pip install -r requirement.txt
```

### Configuration

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

### Running it

```bash
python job_monitor.py
```

New matches get logged to `seen_jobs.json` and emailed if they pass the filters. Already-seen postings are skipped on future runs.

## Custom Handlers

Some companies don't run on a standard ATS and need a bespoke handler to pull their job data. These real handlers aren't included in this repo, since they're built against a specific company's internal endpoints and publishing that publicly isn't something I'm comfortable doing.

What's included instead is [`custom_handler_template.py`](./custom_handler_template.py), which shows the general shape every custom handler follows (pagination pattern, request/response normalization, registration into `CUSTOM_HANDLERS`) without any real company's endpoint or response schema.

Every connector, custom or not, normalizes into the same shared job format, which is what lets the main pipeline treat all 10+ sources identically regardless of what's happening under the hood.

## Status

Actively in development. This is a work in progress, built incrementally as I learn more about each ATS and refine the classification logic.