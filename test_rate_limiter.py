import time
from unittest.mock import patch, MagicMock
 
from rate_limiter import RateLimiter, RateLimitExceeded, PlatformConfig
 
 
def fake_response(status_code, headers=None):
    r = MagicMock()
    r.status_code = status_code
    r.headers = headers or {}
    return r
 
 
def test_success_first_try():
    limiter = RateLimiter()
    with patch("rate_limiter.requests.get", return_value=fake_response(200)) as m:
        resp = limiter.get("http://x.test/jobs", platform="greenhouse", company="acme")
        assert resp.status_code == 200
        assert m.call_count == 1
    print("PASS: success on first try, one call made")
 
 
def test_retries_then_succeeds():
    limiter = RateLimiter(configs={"greenhouse": PlatformConfig(backoff_base_seconds=0.01, max_retries=3)})
    responses = [fake_response(429), fake_response(429), fake_response(200)]
    with patch("rate_limiter.requests.get", side_effect=responses) as m:
        resp = limiter.get("http://x.test/jobs", platform="greenhouse", company="acme")
        assert resp.status_code == 200
        assert m.call_count == 3
    print("PASS: recovered after 2 retries")
 
 
def test_exhausts_retries_and_raises():
    limiter = RateLimiter(configs={"greenhouse": PlatformConfig(backoff_base_seconds=0.01, max_retries=2)})
    with patch("rate_limiter.requests.get", return_value=fake_response(429)):
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
    with patch("rate_limiter.requests.get", side_effect=responses):
        start = time.monotonic()
        limiter.get("http://x.test/jobs", platform="greenhouse", company="acme")
        elapsed = time.monotonic() - start
        assert elapsed >= 0.2
    print(f"PASS: honored Retry-After header (waited {elapsed:.2f}s)")
 
 
def test_per_minute_cap_throttles():
    limiter = RateLimiter(configs={"greenhouse": PlatformConfig(max_requests_per_minute=2, backoff_base_seconds=0.01)})
    with patch("rate_limiter.requests.get", return_value=fake_response(200)):
        limiter.get("http://x.test/jobs", platform="greenhouse", company="acme")
        limiter.get("http://x.test/jobs", platform="greenhouse", company="acme")
        tracker = limiter._tracker_for("acme")
        assert tracker.count_in_window() == 2
    print("PASS: per-minute cap tracked correctly per company")
 
 
def test_different_companies_dont_share_cap():
    limiter = RateLimiter(configs={"greenhouse": PlatformConfig(max_requests_per_minute=1, backoff_base_seconds=0.01)})
    with patch("rate_limiter.requests.get", return_value=fake_response(200)):
        limiter.get("http://x.test/jobs", platform="greenhouse", company="acme")
        limiter.get("http://x.test/jobs", platform="greenhouse", company="beta")
        assert limiter._tracker_for("acme").count_in_window() == 1
        assert limiter._tracker_for("beta").count_in_window() == 1
    print("PASS: per-company caps are independent")
 
 
if __name__ == "__main__":
    test_success_first_try()
    test_retries_then_succeeds()
    test_exhausts_retries_and_raises()
    test_respects_retry_after_header()
    test_per_minute_cap_throttles()
    test_different_companies_dont_share_cap()
    print("\nAll tests passed.")