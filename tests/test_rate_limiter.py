import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils import rate_limiter
from utils.rate_limiter import (
    PAUSED_REASON,
    PlatformConfig,
    RateLimiter,
    RateLimitExceeded,
    is_platform_paused,
    paused_platforms,
    reset_platform_circuits,
)
 
 
def fake_response(status_code, headers=None):
    r = MagicMock()
    r.status_code = status_code
    r.headers = headers or {}
    return r
 
 
def test_success_first_try():
    limiter = RateLimiter()
    with patch("utils.rate_limiter.requests.get", return_value=fake_response(200)) as m:
        resp = limiter.get("http://x.test/jobs", platform="greenhouse", company="acme")
        assert resp.status_code == 200
        assert m.call_count == 1
    print("PASS: success on first try, one call made")
 
 
def test_retries_then_succeeds():
    limiter = RateLimiter(configs={"greenhouse": PlatformConfig(backoff_base_seconds=0.01, max_retries=3)})
    responses = [fake_response(429), fake_response(429), fake_response(200)]
    with patch("utils.rate_limiter.requests.get", side_effect=responses) as m:
        resp = limiter.get("http://x.test/jobs", platform="greenhouse", company="acme")
        assert resp.status_code == 200
        assert m.call_count == 3
    print("PASS: recovered after 2 retries")
 
 
def test_exhausts_retries_and_raises():
    limiter = RateLimiter(configs={"greenhouse": PlatformConfig(backoff_base_seconds=0.01, max_retries=2)})
    with patch("utils.rate_limiter.requests.get", return_value=fake_response(429)):
        try:
            limiter.get("http://x.test/jobs", platform="greenhouse", company="acme")
            assert False, "should have raised"
        except RateLimitExceeded as e:
            assert e.company == "acme"
            assert e.attempts == 3  # initial + 2 retries
    print("PASS: raises RateLimitExceeded after exhausting retries")
 
 
def test_respects_retry_after_header():
    limiter = RateLimiter(configs={"greenhouse": PlatformConfig(backoff_base_seconds=0.01, max_retries=2)})
    responses = [fake_response(429, headers={"Retry-After": "0.2"}), fake_response(200)]
    with patch("utils.rate_limiter.requests.get", side_effect=responses):
        start = time.monotonic()
        limiter.get("http://x.test/jobs", platform="greenhouse", company="acme")
        elapsed = time.monotonic() - start
        assert elapsed >= 0.2
    print(f"PASS: honored Retry-After header (waited {elapsed:.2f}s)")
 
 
def test_per_minute_cap_throttles():
    limiter = RateLimiter(configs={"greenhouse": PlatformConfig(max_requests_per_minute=2, backoff_base_seconds=0.01)})
    with patch("utils.rate_limiter.requests.get", return_value=fake_response(200)):
        limiter.get("http://x.test/jobs", platform="greenhouse", company="acme")
        limiter.get("http://x.test/jobs", platform="greenhouse", company="acme")
        tracker = limiter._tracker_for("acme")
        assert tracker.count_in_window() == 2
    print("PASS: per-minute cap tracked correctly per company")
 
 
def test_different_companies_dont_share_cap():
    limiter = RateLimiter(configs={"greenhouse": PlatformConfig(max_requests_per_minute=1, backoff_base_seconds=0.01)})
    with patch("utils.rate_limiter.requests.get", return_value=fake_response(200)):
        limiter.get("http://x.test/jobs", platform="greenhouse", company="acme")
        limiter.get("http://x.test/jobs", platform="greenhouse", company="beta")
        assert limiter._tracker_for("acme").count_in_window() == 1
        assert limiter._tracker_for("beta").count_in_window() == 1
    print("PASS: per-company caps are independent")
 


# Platform circuit breaker, concurrency cap, and status-code audit (v4.2)

def fast_config(**overrides):
    settings = {"backoff_base_seconds": 0.01, "max_retries": 1}
    settings.update(overrides)
    return PlatformConfig(**settings)


def test_throttle_exhausted_pauses_platform_for_other_companies():
    limiter = RateLimiter(configs={"workday": fast_config()})
    with patch("utils.rate_limiter.requests.post", return_value=fake_response(429)) as m:
        try:
            limiter.post("http://x.test/jobs", platform="workday", company="acme")
            assert False, "should have raised"
        except RateLimitExceeded as e:
            assert e.reason == "rate-limited"
            assert e.status_code == 429
        calls_after_first = m.call_count

        try:
            limiter.post("http://x.test/jobs", platform="workday", company="beta")
            assert False, "should have raised"
        except RateLimitExceeded as e:
            assert e.reason == PAUSED_REASON
            assert e.attempts == 0
        # No request was sent for the second company.
        assert m.call_count == calls_after_first
    assert is_platform_paused("workday")
    print("PASS: exhausted throttle pauses the platform, next company sends nothing")


def test_bot_detection_pauses_platform():
    limiter = RateLimiter(configs={"greenhouse": fast_config()})
    with patch("utils.rate_limiter.requests.get", return_value=fake_response(403)):
        try:
            limiter.get("http://x.test/jobs", platform="greenhouse", company="acme")
            assert False, "should have raised"
        except RateLimitExceeded as e:
            assert e.reason == "bot-detection"
            assert e.status_code == 403
    info = paused_platforms()["greenhouse"]
    assert info == {"triggered_by": "acme", "reason": "bot-detection", "status": 403}
    print("PASS: bot-detection hard stop pauses the platform")


def test_pause_does_not_affect_other_platforms():
    limiter = RateLimiter(configs={"greenhouse": fast_config(), "lever": fast_config()})
    with patch("utils.rate_limiter.requests.get", return_value=fake_response(429)):
        try:
            limiter.get("http://x.test/jobs", platform="greenhouse", company="acme")
        except RateLimitExceeded:
            pass
    with patch("utils.rate_limiter.requests.get", return_value=fake_response(200)):
        resp = limiter.get("http://y.test/jobs", platform="lever", company="beta")
        assert resp.status_code == 200
    print("PASS: a paused platform leaves other platforms alone")


def test_network_error_does_not_pause_platform():
    limiter = RateLimiter(configs={"greenhouse": fast_config()})
    with patch("utils.rate_limiter.requests.get", side_effect=requests.ConnectionError("down")):
        try:
            limiter.get("http://x.test/jobs", platform="greenhouse", company="acme")
        except RateLimitExceeded:
            pass
    assert not is_platform_paused("greenhouse")
    print("PASS: exhausted network errors do not pause the platform")


def test_company_mid_backoff_stops_once_platform_paused():
    limiter = RateLimiter(configs={"workday": fast_config(max_retries=3)})
    calls = []

    def first_call_then_pause(url, **kwargs):
        calls.append(url)
        # Simulates another worker tripping the breaker while this company
        # is backing off after its first throttled response.
        rate_limiter._pause_platform("workday", "other-company", "throttle_exhausted", 429)
        return fake_response(429)

    with patch("utils.rate_limiter.requests.post", side_effect=first_call_then_pause):
        try:
            limiter.post("http://x.test/jobs", platform="workday", company="acme")
            assert False, "should have raised"
        except RateLimitExceeded as e:
            assert e.reason == PAUSED_REASON
    assert len(calls) == 1
    print("PASS: a company already retrying stops as soon as the platform is paused")


def test_reset_clears_pauses():
    rate_limiter._pause_platform("workday", "acme", "bot-detection", 403)
    assert is_platform_paused("workday")
    reset_platform_circuits()
    assert not is_platform_paused("workday")
    print("PASS: reset_platform_circuits() clears pauses for the next run")


def test_pause_writes_one_audit_event():
    with patch("utils.rate_limiter.log_audit_event") as audit:
        rate_limiter._pause_platform("workday", "acme", "throttle_exhausted", 503)
        rate_limiter._pause_platform("workday", "beta", "throttle_exhausted", 503)
    paused_events = [c for c in audit.call_args_list if c.args[0] == "PLATFORM_PAUSED"]
    assert len(paused_events) == 1
    assert paused_events[0].kwargs["triggered_by"] == "acme"
    print("PASS: only the first trigger is audited as PLATFORM_PAUSED")


def test_throttle_audit_records_status_and_retry_after():
    limiter = RateLimiter(configs={"workday": fast_config(max_retries=0)})
    response = fake_response(503, headers={"Retry-After": "0"})
    with patch("utils.rate_limiter.requests.post", return_value=response), \
         patch("utils.rate_limiter.log_audit_event") as audit:
        try:
            limiter.post("http://x.test/jobs", platform="workday", company="acme")
        except RateLimitExceeded:
            pass
    hardstop = [c for c in audit.call_args_list if c.args[0] == "TIER3_HARDSTOP"][0]
    assert hardstop.kwargs["status"] == 503
    assert hardstop.kwargs["retry_after"] == "0"
    assert hardstop.kwargs["redirected_to"] is None
    print("PASS: throttle hard stop audit records the status code and Retry-After")


def test_workday_maintenance_redirect_counts_as_throttle():
    limiter = RateLimiter(configs={"workday": fast_config(max_retries=0)})
    response = fake_response(200, headers={"Content-Type": "text/html"})
    response.history = [fake_response(303)]
    response.url = "https://community.workday.com/maintenance-page"
    with patch("utils.rate_limiter.requests.post", return_value=response), \
         patch("utils.rate_limiter.log_audit_event") as audit:
        try:
            limiter.post("http://x.test/jobs", platform="workday", company="acme")
            assert False, "should have raised"
        except RateLimitExceeded as e:
            assert e.reason == "rate-limited"
    hardstop = [c for c in audit.call_args_list if c.args[0] == "TIER3_HARDSTOP"][0]
    assert hardstop.kwargs["redirected_to"] == "https://community.workday.com/maintenance-page"
    print("PASS: a Workday maintenance redirect is treated as service unavailable")


def test_concurrency_cap_limits_requests_in_flight():
    limiter = RateLimiter(configs={"workday": PlatformConfig(max_concurrent_requests=2)})
    lock = threading.Lock()
    state = {"in_flight": 0, "peak": 0}

    def slow_post(url, **kwargs):
        with lock:
            state["in_flight"] += 1
            state["peak"] = max(state["peak"], state["in_flight"])
        time.sleep(0.05)
        with lock:
            state["in_flight"] -= 1
        return fake_response(200)

    with patch("utils.rate_limiter.requests.post", side_effect=slow_post):
        threads = [
            threading.Thread(target=limiter.post, args=("http://x.test/jobs",),
                             kwargs={"platform": "workday", "company": f"company-{i}"})
            for i in range(6)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    assert state["peak"] == 2
    print("PASS: no more than max_concurrent_requests in flight at once")


def test_workday_default_config_caps_concurrency():
    assert rate_limiter.PLATFORM_CONFIGS["workday"].max_concurrent_requests == 2
    print("PASS: Workday ships with a concurrency cap of 2")


if __name__ == "__main__":
    test_success_first_try()
    test_retries_then_succeeds()
    test_exhausts_retries_and_raises()
    test_respects_retry_after_header()
    test_per_minute_cap_throttles()
    test_different_companies_dont_share_cap()
    print("\nAll tests passed.")