import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
from urllib.error import URLError

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
    def test_returns_parser_on_success(self, checker):
        with patch("utils.robots_check.RobotFileParser") as mock_class:
            mock_parser = MagicMock()
            mock_class.return_value = mock_parser

            result = checker._fetch_parser("https://example.com")

            assert result is mock_parser
            mock_parser.set_url.assert_called_once_with(
                "https://example.com/robots.txt"
            )
            mock_parser.read.assert_called_once()

    def test_returns_none_on_urlerror(self, checker):
        with patch("utils.robots_check.RobotFileParser") as mock_class:
            mock_parser = MagicMock()
            mock_parser.read.side_effect = URLError("Connection refused")
            mock_class.return_value = mock_parser

            result = checker._fetch_parser("https://example.com")

            assert result is None


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
