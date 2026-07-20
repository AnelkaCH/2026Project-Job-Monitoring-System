import json
import logging
import logging.handlers
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.audit_log import (
    check_hardstop,
    log_audit_event,
    setup_logging,
    AUDIT_FILE,
    LOG_DIR,
    OPERATIONAL_FILE,
)


def fake_response(status_code, headers=None, text=""):
    r = MagicMock()
    r.status_code = status_code
    r.headers = headers or {}
    r.text = text
    return r


_HARDSTOP_KEYWORDS = ["captcha", "robot", "access denied", "blocked"]


class TestCheckHardstop:
    def test_403_detected(self):
        resp = fake_response(403)
        reasons = check_hardstop(resp, "greenhouse")
        assert "HTTP 403 - access denied / bot detection" in reasons
        assert len(reasons) == 1

    def test_503_non_workday_detected(self):
        resp = fake_response(503)
        reasons = check_hardstop(resp, "greenhouse")
        assert "HTTP 503 - service unavailable" in reasons
        assert len(reasons) == 1

    def test_503_workday_ignored(self):
        resp = fake_response(503)
        reasons = check_hardstop(resp, "workday")
        assert reasons == []

    def test_html_with_keyword_detected(self):
        resp = fake_response(
            200,
            headers={"Content-Type": "text/html"},
            text="Please solve the captcha to continue",
        )
        reasons = check_hardstop(resp, "greenhouse")
        assert any("captcha" in r for r in reasons)
        assert len(reasons) == 1

    def test_html_without_keyword_clean(self):
        resp = fake_response(
            200,
            headers={"Content-Type": "text/html"},
            text="Welcome to our careers page",
        )
        reasons = check_hardstop(resp, "greenhouse")
        assert reasons == []

    def test_non_html_keyword_ignored(self):
        resp = fake_response(
            200,
            headers={"Content-Type": "application/json"},
            text='{"message": "captcha bypass"}',
        )
        reasons = check_hardstop(resp, "greenhouse")
        assert reasons == []

    def test_clean_response_returns_empty(self):
        resp = fake_response(
            200, headers={"Content-Type": "text/plain"}, text="OK"
        )
        reasons = check_hardstop(resp, "greenhouse")
        assert reasons == []

    def test_accumulates_multiple_reasons(self):
        resp = fake_response(
            403,
            headers={"Content-Type": "text/html"},
            text="Automated access denied by robot detection",
        )
        reasons = check_hardstop(resp, "greenhouse")
        assert len(reasons) == 2
        assert any("403" in r for r in reasons)
        assert any("access denied" in r or "robot" in r for r in reasons)

    def test_workday_503_still_checks_keywords(self):
        resp = fake_response(
            503,
            headers={"Content-Type": "text/html"},
            text="you have been blocked",
        )
        reasons = check_hardstop(resp, "workday")
        assert len(reasons) == 1
        assert any("blocked" in r for r in reasons)


class TestLogAuditEvent:
    @pytest.fixture
    def mock_audit_logger(self):
        with patch("utils.audit_log.logging.getLogger") as mock_get:
            mock_logger = MagicMock()
            mock_get.return_value = mock_logger
            yield mock_logger

    def test_writes_valid_json(self, mock_audit_logger):
        log_audit_event("test_event")
        call_args = mock_audit_logger.info.call_args[0][0]
        record = json.loads(call_args)
        assert record["event"] == "test_event"
        assert "timestamp" in record

    def test_includes_extra_fields(self, mock_audit_logger):
        log_audit_event("scan", company="Acme", tier=2)
        call_args = mock_audit_logger.info.call_args[0][0]
        record = json.loads(call_args)
        assert record["company"] == "Acme"
        assert record["tier"] == 2

    def test_sorted_keys(self, mock_audit_logger):
        log_audit_event("x", a=1, b=2)
        call_args = mock_audit_logger.info.call_args[0][0]
        record = json.loads(call_args)
        keys = list(record.keys())
        assert keys == sorted(keys)


class TestSetupLogging:
    _AUDIT_LOGGER = "job_monitor.audit"
    _OP_LOGGER = "job_monitor.operational"

    @pytest.fixture(autouse=True)
    def isolate_loggers(self):
        for name in (self._AUDIT_LOGGER, self._OP_LOGGER):
            logging.getLogger(name).handlers.clear()
        yield
        for name in (self._AUDIT_LOGGER, self._OP_LOGGER):
            logger = logging.getLogger(name)
            for h in list(logger.handlers):
                logger.removeHandler(h)
        root = logging.getLogger()
        root.setLevel(logging.WARNING)

    @staticmethod
    def _clear_audit_loggers():
        logging.getLogger("job_monitor.audit").handlers.clear()
        logging.getLogger("job_monitor.operational").handlers.clear()

    def test_creates_log_directory(self, tmp_path):
        self._clear_audit_loggers()
        log_dir = tmp_path / "logs"
        with (
            patch("utils.audit_log.LOG_DIR", log_dir),
            patch("utils.audit_log.AUDIT_FILE", log_dir / "audit.log"),
            patch("utils.audit_log.OPERATIONAL_FILE", log_dir / "operational.log"),
        ):
            setup_logging()
            assert log_dir.exists()

    def test_attaches_handlers(self, tmp_path):
        self._clear_audit_loggers()
        log_dir = tmp_path / "logs"
        with (
            patch("utils.audit_log.LOG_DIR", log_dir),
            patch("utils.audit_log.AUDIT_FILE", log_dir / "audit.log"),
            patch("utils.audit_log.OPERATIONAL_FILE", log_dir / "operational.log"),
        ):
            setup_logging()
            audit_logger = logging.getLogger(self._AUDIT_LOGGER)
            op_logger = logging.getLogger(self._OP_LOGGER)
            rfh_audit = [
                h
                for h in audit_logger.handlers
                if isinstance(h, logging.handlers.RotatingFileHandler)
            ]
            rfh_op = [
                h
                for h in op_logger.handlers
                if isinstance(h, logging.handlers.RotatingFileHandler)
            ]
            assert len(rfh_audit) == 1
            assert len(rfh_op) == 1

    def test_idempotent(self, tmp_path):
        self._clear_audit_loggers()
        log_dir = tmp_path / "logs"
        with (
            patch("utils.audit_log.LOG_DIR", log_dir),
            patch("utils.audit_log.AUDIT_FILE", log_dir / "audit.log"),
            patch("utils.audit_log.OPERATIONAL_FILE", log_dir / "operational.log"),
        ):
            setup_logging()
            setup_logging()
            audit_logger = logging.getLogger(self._AUDIT_LOGGER)
            rfh = [
                h
                for h in audit_logger.handlers
                if isinstance(h, logging.handlers.RotatingFileHandler)
            ]
            assert len(rfh) == 1
