# Job Monitor — v2: Rate-Limiting & Backoff

## Why this exists

v1 of the job monitor had one layer of defense against bot detection: a hard
stop. If an ATS flagged the monitor as a bot, the Tier 3 classification logic
would notice and halt entirely for that company. That's a *reactive* rule —
it only kicks in after something's already gone wrong.

v2 adds the layer underneath it: a rate limiter that proactively slows
requests down so they're less likely to look bot-like in the first place.
The goal isn't to replace the Tier 3 hard stop, it's to make triggering it
less likely to begin with. Think of it as the difference between a fire
alarm (Tier 3) and not leaving the stove unattended (v2).

The concrete motivation: this system checks 18 companies across 9 different
ATS platforms, on a recurring schedule, from the same IP (GitHub Actions'
runner). That's exactly the kind of traffic pattern automated bot detection
is built to notice — repeated, regular, unauthenticated requests to the same
endpoints. Slowing down and backing off intelligently is the standard,
respectful way to run something like this without looking adversarial to
the platforms being checked.

## What was added

Two new modules, plus integration changes across the four files that already
existed.

### `rate_limiter.py` — the core module

Every adapter's HTTP call now goes through `RateLimiter.get()` or
`RateLimiter.post()` instead of calling `requests.get()`/`requests.post()`
directly. One shared instance is used everywhere (created once in
`connectors.py`, imported by `custom_handlers.py`), so there's a single
audited piece of code enforcing the policy — not 9 separate places it could
drift or get forgotten.

It enforces two things:

1. **A per-company requests-per-minute cap.** Tracked per company (not per
   platform), since two different companies on the same ATS — say, two
   different Greenhouse boards — are different endpoints being checked on
   behalf of different job searches, and shouldn't throttle each other.
   Conservative by default (`PlatformConfig.max_requests_per_minute`), with
   room to loosen per-platform once real limits are observed.

2. **Exponential backoff with jitter on throttle signals.** If a request
   comes back with a 429 (or a platform-specific throttle signal — Workday's
   503s, for example), the module waits before retrying, and that wait
   roughly doubles each attempt. A small amount of randomness (jitter) is
   layered on top so retries don't land in a predictable, metronome-like
   pattern, which is itself something bot detection can key off. Since
   adapters run sequentially rather than in parallel, jitter matters less
   here than it would in a concurrent system — it's a minor ingredient, not
   a core design decision.

   If retries run out, the module raises `RateLimitExceeded` rather than
   crashing or looping forever. This is a **skip-and-report** philosophy: a
   company that's rate-limited too many times in one cycle just gets skipped
   for that cycle, logged, and reported in the email — it doesn't escalate
   into Tier 3's hard-stop logic, since being rate-limited is a much weaker
   signal than active bot detection.

Config for all 9 platforms (`greenhouse`, `lever`, `ashby`, `smartrecruiters`,
`recruitee`, `workable`, `personio`, `workday`, `sap`, plus `accenture` for
the custom handler) starts on the same conservative default. Tightening or
loosening any one of them is a one-line change to `PLATFORM_CONFIGS`, no
logic changes needed.

### `skip_tracker.py` — a second, separate kind of state

This tracks something the rate limiter deliberately doesn't: **consecutive
cycle skips per company.** If a company gets rate-limited once, that's
probably nothing. If it gets rate-limited 3+ cycles in a row, that's worth
knowing about — maybe the endpoint changed, maybe the platform's limits got
stricter, maybe something needs a manual look.

This is intentionally a separate file from `rate_limiter.py`, because it
tracks a fundamentally different kind of state:

| | `rate_limiter.py` | `skip_tracker.py` |
|---|---|---|
| Tracks | request timing over the last ~60 seconds | consecutive cycle skips per company |
| Lifespan | in-memory, thrown away every run | persisted to `skip_history.json`, survives across runs |
| Why | a 6-hour gap between cycles makes last cycle's request timing meaningless | a *pattern across cycles* is the whole point — one skip means nothing, a streak means something |

It resets a company's streak back to zero the moment that company succeeds
again, so the flag only ever reflects an active, ongoing problem.

## How a request actually flows through v2

```
adapter (e.g. fetch_greenhouse)
    │
    ▼
limiter.get(url, platform="greenhouse", company="Cloudflare")
    │
    ├─ under the per-minute cap? ─── no ──▶ wait, then recheck
    │        │
    │       yes
    │        ▼
    │   make the request
    │        │
    │        ├─ 429 / throttled? ─── yes ──▶ wait (exponential + jitter), retry
    │        │                                     │
    │        │                              retries exhausted?
    │        │                                     │
    │        │                                    yes ──▶ raise RateLimitExceeded
    │        │
    │       no
    │        ▼
    │   return the response
    ▼
adapter catches RateLimitExceeded (if raised)
    │
    ├─ skip_tracker.record_skip(company)   → increments streak, logs it
    └─ return None instead of a job list
         │
         ▼
    job_monitor.py sees None, skips this company for the cycle
    WITHOUT touching its seen_jobs baseline — so next cycle's dedup
    still works correctly once the company succeeds again
         │
         ▼
    notifier.py includes an amber "repeatedly rate-limited" section
    in the email if any company's streak crossed the threshold (3)
```

## Design decisions worth calling out

**Per-company, not per-platform, rate limiting.** Two companies on the same
ATS are still two independent endpoints on behalf of two independent job
searches — no reason to throttle one because of the other.

**No cross-cycle persistence for the rate limiter itself.** The scheduler
runs every 6 hours; anything about request timing from the last cycle is
meaningless by the time the next one starts. Only `skip_tracker.py` needs to
survive across runs, and it does — as a small JSON file committed back to
the repo by the GitHub Actions workflow after every run, right alongside
`seen_jobs.json`.

**Return contract change: `None` vs `[]`.** Before v2, every fetch function
always returned a list — empty if there were genuinely no jobs. Now it can
return `None`, meaning "not checked this cycle at all." `job_monitor.py` was
updated to treat these two cases very differently: an empty list updates the
seen-jobs baseline (correctly, since a real check happened and found
nothing); `None` skips the update entirely, so a temporarily rate-limited
company doesn't have its real history erased and mistakenly reported as "18
new jobs" the moment it recovers.

**Skip-and-report, not escalate.** A rate-limit skip is a much weaker signal
than the Tier 3 hard-stop's active bot detection, so it's handled
separately: log it, count it, mention it in the email if it persists — never
treat it as proof the company has started blocking the monitor outright.

## Files touched

| File | Change |
|---|---|
| `rate_limiter.py` | **New.** Core module. |
| `skip_tracker.py` | **New.** Persistent skip-streak counter. |
| `connectors.py` | All 9 fetch functions route through the limiter; return `None` on skip. |
| `custom_handlers.py` | Accenture's handler gets the same treatment; shares state with `connectors.py`. |
| `job_monitor.py` | Handles `None` returns without crashing or wiping baselines. |
| `notifier.py` | Email now includes a flagged-companies section when a streak crosses threshold. |
| `.github/workflows/job-monitor.yml` | **New.** Commits `seen_jobs.json` and `skip_history.json` back to the repo after each run. |
| `date_utils.py`, `config.json` | Unchanged. |