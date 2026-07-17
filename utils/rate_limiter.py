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
    max_requests_per_minute: int = 10   # conservative default, override per-adapter once real limits are known
    backoff_base_seconds: float = 2.0   # first retry waits ~2s, then ~4s, ~8s...
    max_retries: int = 4
    max_backoff_seconds: float = 60.0   # cap so a bad run doesn't sleep for minutes
 
 
# All 8 platforms start on the same conservative default. Tighten or loosen
# individual platforms here as you observe real behavior or find published
# limits, without touching any logic below.
PLATFORM_CONFIGS: Dict[str, PlatformConfig] = {
    "greenhouse": PlatformConfig(),
    "workday": PlatformConfig(),
    "lever": PlatformConfig(),
    "ashby": PlatformConfig(),
    "smartrecruiters": PlatformConfig(),
    "recruitee": PlatformConfig(),
    "workable": PlatformConfig(),
    "personio": PlatformConfig(),
    "sap": PlatformConfig(),
    "accenture": PlatformConfig(),  # custom handler, not a standard ATS, but still needs its own bucket
}
 
DEFAULT_CONFIG = PlatformConfig()
 
 
class RateLimitExceeded(Exception):
    # Raised when a company's requests are exhausted after all retries.
 
    # Callers (adapters) should catch this, mark the company as skipped for
    # this cycle, and let it flow into the email notification rather than
    # crashing the whole run or escalating to the Tier 3 hard-stop logic.
    # Repeated rate-limiting is a much weaker signal than active bot detection.

    def __init__(self, company: str, platform: str, attempts: int):
        self.company = company
        self.platform = platform
        self.attempts = attempts
        super().__init__(
            f"{company} ({platform}) skipped after {attempts} rate-limited attempts"
        )
 
 
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
 
    # Since adapters run sequentially (no threads/asyncio), this doesn't need
    # locks. If the scheduler ever moves to concurrent adapters, add a lock
    # per company in _wait_for_capacity before request.get() is called from
    # more than one thread.
 
    def __init__(self, configs: Optional[Dict[str, PlatformConfig]] = None):
        self.configs = configs or PLATFORM_CONFIGS
        self._trackers: Dict[str, _DomainTracker] = {}
 
    def _tracker_for(self, company: str) -> _DomainTracker:
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
        attempt = 0
 
        while True:
            self._wait_for_capacity(company, config)
            tracker.record()
 
            try:
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
                if attempt > config.max_retries:
                    log_audit_event("TIER3_HARDSTOP", platform=platform, company=company, reason="throttle_exhausted", attempts=attempt)
                    raise RateLimitExceeded(company, platform, attempt)

                wait = self._compute_backoff(attempt, config)
                retry_after = response.headers.get("Retry-After")
                if retry_after:
                    try:
                        wait = max(wait, float(retry_after))
                    except ValueError:
                        pass
 
                logger.info(
                    "%s: throttled (attempt %d/%d), backing off %.1fs",
                    company, attempt, config.max_retries, wait,
                )
                time.sleep(wait)
                continue
 
            reasons = check_hardstop(response, platform)
            if reasons:
                log_audit_event("TIER3_HARDSTOP", platform=platform, company=company, reasons=reasons, status=response.status_code)

            return response