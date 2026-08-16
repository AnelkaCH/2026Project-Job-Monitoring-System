import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import requests
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.robots_check import RobotsChecker, SkipReason


@pytest.fixture
def checker():
    return RobotsChecker()


class TestDomainUrl:
    def test_strips_path(self, checker):
        assert (
            checker._domain_url("https://api.greenhouse.io/v1/boards")
            == "https://api.greenhouse.io"
        )

    def test_preserves_port(self, checker):
        assert (
            checker._domain_url("http://example.com:8080/path")
            == "http://example.com:8080"
        )


class TestFetchParser:
    def _response(self, status_code, text=""):
        resp = MagicMock()
        resp.status_code = status_code
        resp.text = text
        return resp

    def test_returns_parser_on_success(self, checker):
        with patch(
            "utils.robots_check.requests.get",
            return_value=self._response(200, "User-agent: *\nAllow: /"),
        ) as mock_get:
            with patch("utils.robots_check.RobotFileParser") as mock_class:
                mock_parser = MagicMock()
                mock_class.return_value = mock_parser

                result = checker._fetch_parser("https://example.com")

                assert result is mock_parser
                mock_get.assert_called_once_with(
                    "https://example.com/robots.txt",
                    timeout=10,
                    headers={"User-Agent": "JobMonitorBot/1.0 (+https://github.com/AnelkaCH/job-monitoring-system)"},
                )
                mock_parser.set_url.assert_called_once_with(
                    "https://example.com/robots.txt"
                )
                mock_parser.parse.assert_called_once_with(["User-agent: *", "Allow: /"])

    def test_returns_none_on_network_error(self, checker):
        # Fail-conservative: an unreachable robots.txt is treated as disallowed.
        with patch(
            "utils.robots_check.requests.get",
            side_effect=requests.exceptions.ConnectionError("Connection refused"),
        ):
            assert checker._fetch_parser("https://example.com", retries=0) is None

    def test_returns_none_on_explicit_denial(self, checker):
        # A 401 or 403 is an explicit access denial, fail conservative with no retry.
        for status in (401, 403):
            with patch(
                "utils.robots_check.requests.get",
                return_value=self._response(status),
            ):
                assert checker._fetch_parser("https://example.com") is None

    def test_returns_allow_all_parser_on_missing_robots(self, checker):
        # A 404 means the platform publishes no robots.txt, allow-all per RFC 9309.
        with patch("utils.robots_check.requests.get", return_value=self._response(404)):
            result = checker._fetch_parser("https://example.com")
            assert result is not None
            assert result.can_fetch("*", "/anything") is True

    def test_5xx_retries_once_then_disallow(self, checker):
        # Persistent server-side errors fail conservative, treated as disallowed.
        with patch(
            "utils.robots_check.requests.get",
            side_effect=[self._response(503), self._response(503)],
        ), patch("utils.robots_check.time.sleep"):
            assert checker._fetch_parser("https://example.com") is None


class TestIsAllowed:
    def test_fail_conservative_when_fetch_fails(self, checker):
        with patch.object(checker, "_fetch_parser", return_value=None):
            assert checker.is_allowed("https://example.com", "/path") is False

    def test_returns_true_when_allowed(self, checker):
        mock_parser = MagicMock()
        mock_parser.can_fetch.return_value = True

        with patch.object(checker, "_fetch_parser", return_value=mock_parser):
            assert checker.is_allowed("https://example.com", "/jobs") is True
            mock_parser.can_fetch.assert_called_once_with("*", "/jobs")

    def test_returns_false_when_disallowed(self, checker):
        mock_parser = MagicMock()
        mock_parser.can_fetch.return_value = False

        with patch.object(checker, "_fetch_parser", return_value=mock_parser):
            assert checker.is_allowed("https://example.com", "/admin") is False

    def test_caches_successful_parser(self, checker):
        mock_parser = MagicMock()
        mock_parser.can_fetch.return_value = True

        with patch.object(
            checker, "_fetch_parser", return_value=mock_parser
        ) as mock_fetch:
            assert checker.is_allowed("https://example.com", "/a") is True
            assert checker.is_allowed("https://example.com", "/b") is True
            mock_fetch.assert_called_once_with("https://example.com")

    def test_does_not_cache_failed_fetch(self, checker):
        mock_success = MagicMock()
        mock_success.can_fetch.return_value = True

        with patch.object(
            checker, "_fetch_parser", side_effect=[None, mock_success]
        ) as mock_fetch:
            assert checker.is_allowed("https://example.com", "/a") is False
            assert checker.is_allowed("https://example.com", "/b") is True
            assert mock_fetch.call_count == 2

    def test_uses_custom_user_agent(self, checker):
        mock_parser = MagicMock()

        with patch.object(checker, "_fetch_parser", return_value=mock_parser):
            checker.is_allowed(
                "https://example.com", "/path", user_agent="MyBot/1.0"
            )
            mock_parser.can_fetch.assert_called_once_with("MyBot/1.0", "/path")


class TestSkipReason:
    def test_default_detail(self):
        r = SkipReason(reason="robots.txt")
        assert r.reason == "robots.txt"
        assert r.detail == ""

    def test_with_detail(self):
        r = SkipReason(reason="rate_limited", detail="retries exhausted")
        assert r.reason == "rate_limited"
        assert r.detail == "retries exhausted"
