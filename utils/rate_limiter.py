import threading
import time
import random
import logging
from dataclasses import dataclass
from typing import Optional, Dict
from collections import deque
 
import requests

from .audit_log import log_audit_event, check_hardstop
 
logger = logging.getLogger(__name__)
 
 
@dataclass
class PlatformConfig:
    max_requests_per_minute: int = 15   # conservative default, override per-adapter once real limits are known
    backoff_base_seconds: float = 2.0   # first retry waits ~2s, then ~4s, ~8s...
    max_retries: int = 4
    max_backoff_seconds: float = 60.0   # cap so a bad run doesn't sleep for minutes
    # Cap on requests in flight at the same moment across ALL companies on
    # this platform. None means no cap. Useful when many companies share the
    # same hosts (e.g. every Workday tenant sits on a few wdN hosts), since
    # the per-minute cap above is counted per company, not per host.
    max_concurrent_requests: Optional[int] = None


# All standard platforms start on the same conservative default. Tighten or
# loosen individual platforms here as you observe real behavior or find
# published limits, without touching any logic below. Custom handler
# platform names are intentionally absent here: they get DEFAULT_CONFIG via
# the _config_for() fallback, so adding a handler needs no rate-limiter
# changes.
PLATFORM_CONFIGS: Dict[str, PlatformConfig] = {
    "greenhouse": PlatformConfig(),
    # Workday: 30+ tenants share a handful of hosts behind one bot filter,
    # so only two requests may be in flight to Workday at once.
    "workday": PlatformConfig(max_concurrent_requests=2),
    "lever": PlatformConfig(),
    "ashby": PlatformConfig(),
    "smartrecruiters": PlatformConfig(),
    "recruitee": PlatformConfig(),
    "workable": PlatformConfig(),
    "personio": PlatformConfig(),
    "sap": PlatformConfig(),
}
 
DEFAULT_CONFIG = PlatformConfig()
 
 
class RateLimitExceeded(Exception):
    # Raised when a company's requests are exhausted after all retries,
    # or when a Tier 3 hard-stop (bot detection) is triggered.

    # Callers (adapters) should catch this, mark the company as skipped for
    # this cycle, and let it flow into the email notification rather than
    # crashing the whole run or escalating further.

    def __init__(self, company: str, platform: str, attempts: int, reason: str = "rate-limited",
                 status_code: Optional[int] = None):
        self.company = company
        self.platform = platform
        self.attempts = attempts
        self.reason = reason
        self.status_code = status_code
        status_text = f" (last HTTP status {status_code})" if status_code is not None else ""
        super().__init__(
            f"{company} ({platform}) skipped after {attempts} attempt(s): {reason}{status_text}"
        )


# Platform circuit breaker (per run).
#
# When one company on a platform exhausts its retries on throttling, or
# trips the Tier 3 bot-detection hard stop, the whole platform is paused for
# the rest of this run. Every other company on that platform is skipped
# without sending a request. Without this, a platform that is blocking us
# gets hit again by every one of its companies, each with its own retries,
# which only makes the block worse.
#
# This only ever makes the monitor send FEWER requests. It never loosens or
# bypasses the hard stop.
#
# State lives at module level because every adapter has its own RateLimiter
# instance. job_monitor.run_cycle() calls reset_platform_circuits() at the
# start of each run so one run's pause never carries into the next.

PAUSED_REASON = "platform-paused"

# Only "throttle_exhausted" and "bot-detection" trip the breaker. An
# exhausted network error is left out on purpose: that is usually our side
# or one company's endpoint, not the platform refusing us.

_circuit_lock = threading.Lock()
_paused_platforms: Dict[str, dict] = {}
_platform_semaphores: Dict[str, threading.BoundedSemaphore] = {}


def reset_platform_circuits():
    # Clears every platform pause and concurrency slot. Called once at the
    # start of each run, and by the test suite between tests.
    with _circuit_lock:
        _paused_platforms.clear()
        _platform_semaphores.clear()


def is_platform_paused(platform: str) -> bool:
    with _circuit_lock:
        return platform in _paused_platforms


def paused_platforms() -> Dict[str, dict]:
    # A copy of the current pauses, for the end-of-run summary.
    with _circuit_lock:
        return {name: dict(info) for name, info in _paused_platforms.items()}


def _pause_platform(platform: str, company: str, reason: str, status_code: Optional[int]):
    # Records the pause and writes one PLATFORM_PAUSED audit event. Only the
    # first trigger is recorded; later ones in the same run are ignored.
    with _circuit_lock:
        if platform in _paused_platforms:
            return
        _paused_platforms[platform] = {
            "triggered_by": company,
            "reason": reason,
            "status": status_code,
        }
    log_audit_event("PLATFORM_PAUSED", platform=platform, triggered_by=company, reason=reason, status=status_code)
    logger.warning(
        "%s paused for the rest of this run: %s on %s (HTTP %s)",
        platform, reason, company, status_code,
    )


def _semaphore_for(platform: str, config: PlatformConfig) -> Optional[threading.BoundedSemaphore]:
    if config.max_concurrent_requests is None:
        return None
    with _circuit_lock:
        if platform not in _platform_semaphores:
            _platform_semaphores[platform] = threading.BoundedSemaphore(config.max_concurrent_requests)
        return _platform_semaphores[platform]


def _redirect_target(response: requests.Response) -> Optional[str]:
    # Where the request ended up if it was redirected, otherwise None.
    # Recorded in the audit log so a redirect to a maintenance or outage
    # page is visible without re-running anything.
    history = getattr(response, "history", None)
    final_url = getattr(response, "url", None)
    if isinstance(history, list) and history and isinstance(final_url, str):
        return final_url
    return None

 
class _DomainTracker:
    # Tracks request timestamps for a single company within this run only.
 
    def __init__(self):
        self.timestamps: deque = deque()
 
    def _prune(self, window_seconds: float = 60.0):
        cutoff = time.monotonic() - window_seconds
        while self.timestamps and self.timestamps[0] < cutoff:
            self.timestamps.popleft()
 
    def count_in_window(self, window_seconds: float = 60.0) -> int:
        self._prune(window_seconds)
        return len(self.timestamps)
 
    def record(self):
        self.timestamps.append(time.monotonic())
 
 
class RateLimiter:
# Usage:
    #     limiter = RateLimiter()
    #     response = limiter.get(url, platform="greenhouse", company="acme-corp")

    # Adapters run concurrently (ThreadPoolExecutor in job_monitor.py), so
    # the per-company tracker registry is guarded by a lock. Per-company
    # trackers are only ever touched by the one worker handling that company,
    # so counting and recording do not need their own locks.

    def __init__(self, configs: Optional[Dict[str, PlatformConfig]] = None):
        self.configs = configs or PLATFORM_CONFIGS
        self._trackers: Dict[str, _DomainTracker] = {}
        self._lock = threading.Lock()

    def _tracker_for(self, company: str) -> _DomainTracker:
        with self._lock:
            if company not in self._trackers:
                self._trackers[company] = _DomainTracker()
            return self._trackers[company]
 
    def _config_for(self, platform: str) -> PlatformConfig:
        return self.configs.get(platform, DEFAULT_CONFIG)
 
    def _wait_for_capacity(self, company: str, config: PlatformConfig):
        tracker = self._tracker_for(company)
        while tracker.count_in_window() >= config.max_requests_per_minute:
            sleep_time = 1.0 + random.uniform(0, 0.5)
            logger.debug(
                "%s: at per-minute cap (%d), waiting %.1fs",
                company, config.max_requests_per_minute, sleep_time,
            )
            time.sleep(sleep_time)
 
    def _compute_backoff(self, attempt: int, config: PlatformConfig) -> float:
        # Exponential backoff with jitter. Small amount of randomness so
        # retries across companies on the same ATS don't land in lockstep.
        base = config.backoff_base_seconds * (2 ** (attempt - 1))
        capped = min(base, config.max_backoff_seconds)
        jitter = random.uniform(0, capped * 0.3)
        return capped + jitter
 
    def _is_platform_throttle(self, platform: str, response: requests.Response) -> bool:
        # Hook for ATS-specific throttle signals beyond a plain 429.
        # Add cases here as you observe real platform behavior, e.g. some
        # platforms throttle via a 503 or a custom header instead of 429.
        if platform == "workday" and response.status_code == 503:
            return True
        # Workday answers with a 303 redirect to a maintenance or outage page
        # while a tenant is down. requests follows it and hands back that
        # page, so treat it as "service unavailable" instead of trying to
        # read it as JSON.
        if platform == "workday":
            target = (_redirect_target(response) or "").lower()
            if "maintenance" in target or "outage" in target:
                return True
        return False
 
    def get(self, url: str, platform: str, company: str, **kwargs) -> requests.Response:
        # Rate-limited, backoff-aware GET. See _request() for details.
        return self._request(requests.get, url, platform, company, **kwargs)
 
    def post(self, url: str, platform: str, company: str, **kwargs) -> requests.Response:
        # Rate-limited, backoff-aware POST. See _request() for details.
        return self._request(requests.post, url, platform, company, **kwargs)
 
    def _request(self, method, url: str, platform: str, company: str, **kwargs) -> requests.Response:
        # Shared logic for get()/post(). Raises RateLimitExceeded if retries
        # are exhausted; the caller decides what "skip this company" means for
        # its cycle (log it, flag it for the seen-jobs/skip tracker, etc).
        config = self._config_for(platform)
        tracker = self._tracker_for(company)
        semaphore = _semaphore_for(platform, config)
        attempt = 0

        while True:
            # Checked before every attempt, including retries, so a company
            # already mid-backoff also stops once another company on the
            # same platform has tripped the breaker.
            if is_platform_paused(platform):
                raise RateLimitExceeded(company, platform, attempt, reason=PAUSED_REASON)

            self._wait_for_capacity(company, config)
            tracker.record()

            try:
                if semaphore is not None:
                    with semaphore:
                        response = method(url, **kwargs)
                else:
                    response = method(url, **kwargs)
            except requests.RequestException as exc:
                attempt += 1
                logger.warning(
                    "%s: request error on attempt %d: %s", company, attempt, exc
                )
                if attempt > config.max_retries:
                    log_audit_event("TIER3_HARDSTOP", platform=platform, company=company, reason="request_error_exhausted", attempts=attempt)
                    raise RateLimitExceeded(company, platform, attempt) from exc
                time.sleep(self._compute_backoff(attempt, config))
                continue
 
            throttled = response.status_code == 429 or self._is_platform_throttle(platform, response)
            if throttled:
                attempt += 1
                retry_after = response.headers.get("Retry-After")
                if attempt > config.max_retries:
                    log_audit_event(
                        "TIER3_HARDSTOP", platform=platform, company=company,
                        reason="throttle_exhausted", attempts=attempt,
                        status=response.status_code, retry_after=retry_after,
                        redirected_to=_redirect_target(response),
                    )
                    _pause_platform(platform, company, "throttle_exhausted", response.status_code)
                    raise RateLimitExceeded(company, platform, attempt, status_code=response.status_code)

                wait = self._compute_backoff(attempt, config)
                if retry_after:
                    try:
                        wait = max(wait, float(retry_after))
                    except ValueError:
                        pass
 
                logger.info(
                    "%s: throttled with HTTP %s (attempt %d/%d), backing off %.1fs",
                    company, response.status_code, attempt, config.max_retries, wait,
                )
                time.sleep(wait)
                continue

            reasons = check_hardstop(response, platform)
            if reasons:
                log_audit_event(
                    "TIER3_HARDSTOP", platform=platform, company=company,
                    reasons=reasons, status=response.status_code,
                    redirected_to=_redirect_target(response),
                )
                _pause_platform(platform, company, "bot-detection", response.status_code)
                raise RateLimitExceeded(company, platform, attempt, reason="bot-detection", status_code=response.status_code)

            return response