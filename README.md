# Job Monitoring System

A self-hosted, zero-cost tool that watches specific companies' job boards and tells me when they post something new, instead of me manually checking a dozen careers pages every day.

> **Status: early work in progress.** This is not a finished product. Connectors, filtering, and scheduling are all being built incrementally. Expect breaking changes, missing features, and rough edges. See [Roadmap](#roadmap) for what's actually done vs planned.

## Why I built this

I'm tracking internship and job openings at a specific list of Singapore-based companies across cybersecurity, cloud, and tech, as part of my own job search. Rather than checking each company's careers page by hand, this project automates that while staying within clear technical and ethical boundaries (see below).

It's also a portfolio project. I want to demonstrate reasonable engineering judgment around API usage, rate limits, and respecting what platforms actually intend to expose publicly.

## How it works, and why it's safe to use

This project **only reads data that is already public and requires no login.** Specifically:

- It calls documented or long-standing public JSON APIs that ATS platforms (Greenhouse, Lever, Ashby, SmartRecruiters, Recruitee, Workable) explicitly expose for this purpose, the same data their own careers pages display to any visitor.
- For platforms without a documented public API (like Workday), it uses the same JSON endpoint the company's own careers page calls in the visitor's browser, nothing hidden, nothing requiring a login or session.
- It does **not** scrape rendered HTML, log into any account, bypass CAPTCHAs, or get around any bot-detection or access control. If a target doesn't offer a clean, unauthenticated JSON response, it's simply not added, meaning no workarounds are attempted.
- It's read-only. It never submits applications, creates accounts, or writes anything back to any platform.

This means the risk profile here is closer to using an RSS reader than anything resembling scraping in the adversarial sense. That said: **this is a personal tool for my own job search, not a redistributable product.** If you use or adapt this, do your own due diligence on the specific companies and endpoints you point it at. Sme of what's "safe" here depends on the platform (documented API vs. reverse-engineered endpoint), and that can change over time.

Also, for an example of the formatting for the config.json file, you can check the config.example.json file.

## Is it OK to use / fork this?

Yes, with a few caveats:
- This is provided as-is, for educational and personal use. No warranty, no guarantee any given connector still works by the time you read this (ATS platforms change endpoints without notice).
- Don't point this at platforms requiring login/auth, and don't try to work around rate limits or bot-detection if you hit them. That's the line where "reading public data" turns into something else.
- Company-specific config (Workday tenant URLs especially) will need to be rediscovered per company you add (see the Tiers section below).

## Currently supported ATS platforms

| Platform | Status | Notes |
|---|---|---|
| Greenhouse | ✅ working | Single GET, no pagination |
| Lever | ✅ working | Single GET, no pagination |
| Ashby | ✅ working | Single GET, no pagination |
| SmartRecruiters | ✅ working | GET, paginated via offset/limit |
| Recruitee | ✅ working | Single GET, no pagination |
| Workday | ✅ working | POST, paginated, per-company endpoint discovery required |
| Workable | ✅ working | Public, endpoint confirmed, connector not yet written |
| Personio | ✅ working | Public (XML or JSON feed), connector not yet written |
| Oracle Recruiting Cloud | ❓ under investigation | No standard public API; feasibility being checked per company |
| SAP SuccessFactors | ❓ under investigation | No standard public API; feasibility being checked per company |
| Taleo | ❓ under investigation | Likely no usable feed for most tenants; low priority |
| Custom in-house portals | ❌ not supported generically | Would need one-off handler per company; not yet built |

## Tier system

Every company I add gets classified before any connector is written:

- **Tier 1** = documented public API, no auth, stable URL pattern (Greenhouse, Lever, Ashby, SmartRecruiters, Recruitee, Workable)
- **Tier 2** = no documented API, but the company's own careers page calls a JSON endpoint with no login required (Workday)
- **Tier 3** = no accessible JSON feed at all, or the endpoint sits behind bot-detection / login. Not automated and checked manually instead.

## Setup

```bash
pip install -r requirements.txt
```

Edit `config.json`:
- Add companies under `"companies"`, each with a `"name"` and `"ats"` type matching one of the supported connectors
- Adjust `"filters"` (`locations`, `keywords`) to match what you're looking for

Run it:
```bash
python job_monitor.py
```

First run establishes a baseline (saved to `seen_jobs.json`). Every run after that reports only new postings matching your filters.

## Roadmap

- [x] Tier 1 connectors: Greenhouse, Lever, Ashby, SmartRecruiters, Recruitee
- [x] Tier 2 connector: Workday
- [x] Location + keyword filtering
- [x] Per-company error isolation (one broken endpoint doesn't stop the whole run)
- [x] Workable and Personio connectors
- [ ] Investigate Oracle Recruiting Cloud / SAP SuccessFactors feasibility per company
- [ ] Scheduling via GitHub Actions (currently run manually)
- [x] Notifications (email / Discord / Telegram) instead of console output only
- [ ] Ghost-job / stale-posting detection
- [ ] Possibly: match scoring instead of binary include/exclude filtering

## Disclaimer

This is a personal side project built while job-hunting, not a maintained open-source library. Things will break, get restructured, or be abandoned as priorities shift. Use at your own judgment.