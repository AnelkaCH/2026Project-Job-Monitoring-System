# Structured audit logging for the job monitoring system.
#
# Two log streams:
#   audit      – security-relevant events (queries, tier classifications,
#                Tier 3 hard-stops) as JSON lines for SIEM/grep consumption
#   operational – routine run output with timestamps and log levels
#
# Both use RotatingFileHandler so unbounded log growth is prevented when
# the scheduler runs continuously instead of on-demand.

import json
import logging
import os
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler

LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
AUDIT_FILE = os.path.join(LOG_DIR, "audit.log")
OPERATIONAL_FILE = os.path.join(LOG_DIR, "operational.log")

_AUDIT_LOGGER_NAME = "job_monitor.audit"
_OPERATIONAL_LOGGER_NAME = "job_monitor.operational"

# Max 5 MB per file, keep 5 audit backups and 3 operational backups.
_AUDIT_MAX_BYTES = 5_242_880
_AUDIT_BACKUP_COUNT = 5
_OPERATIONAL_MAX_BYTES = 5_242_880
_OPERATIONAL_BACKUP_COUNT = 3


# Environment variables whose values must never appear in any log line.
# Since v4.0 the logs are committed to the private repo's state branch by
# the scheduled workflow, and GitHub only masks secrets in the Actions
# console, not inside files. So every audit and operational line is passed
# through redact_secrets() before it is written.
SECRET_ENV_VARS = ("EMAIL_ADDRESS", "EMAIL_APP_PASSWORD", "RECIPIENT_EMAIL")
REDACTED = "[REDACTED]"

# Identifies the current monitoring run in every audit event (v4.0). Set
# once by job_monitor.main() before any worker starts, cleared at the end.
_current_run_id = None


def set_run_id(run_id):
    global _current_run_id
    _current_run_id = run_id


def redact_secrets(text):
    # Replaces the value of every secret env var found in text. Very short
    # values are skipped so a stray one-character variable cannot blank out
    # ordinary log text.
    if not isinstance(text, str):
        return text
    for name in SECRET_ENV_VARS:
        value = os.environ.get(name)
        if value and len(value) >= 4 and value in text:
            text = text.replace(value, REDACTED)
    return text


def _redact_value(value):
    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _redact_value(item) for key, item in value.items()}
    return value


class RedactingFilter(logging.Filter):
    # Logging filter that redacts secrets from the fully formatted message,
    # so exception text passed as a %s argument is covered too.
    def filter(self, record):
        message = record.getMessage()
        redacted = redact_secrets(message)
        if redacted != message:
            record.msg = redacted
            record.args = None
        return True


def _add_redacting_filter(target):
    # Idempotent: setup_logging() may run more than once per process.
    if not any(isinstance(f, RedactingFilter) for f in target.filters):
        target.addFilter(RedactingFilter())


def _ensure_log_dir():
    os.makedirs(LOG_DIR, exist_ok=True)


def _make_handler(path, max_bytes, backup_count, formatter):
    handler = RotatingFileHandler(
        path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
    )
    handler.setFormatter(formatter)
    return handler


def _has_file_handler(logger):
    # Idempotency is keyed on our own rotating file handler, not on "any
    # handler": other tooling (pytest's log capture, for one) can attach
    # handlers of its own, which must not stop the log file being set up.
    return any(isinstance(h, RotatingFileHandler) for h in logger.handlers)


def setup_logging():
    # Idempotent – safe to call on every run without stacking handlers.
    _ensure_log_dir()

    # Audit logger
    audit_logger = logging.getLogger(_AUDIT_LOGGER_NAME)
    audit_logger.setLevel(logging.INFO)
    if not _has_file_handler(audit_logger):
        handler = _make_handler(
            AUDIT_FILE, _AUDIT_MAX_BYTES, _AUDIT_BACKUP_COUNT,
            logging.Formatter("%(message)s"),
        )
        audit_logger.addHandler(handler)
        audit_logger.propagate = False

    # Operational logger
    op_logger = logging.getLogger(_OPERATIONAL_LOGGER_NAME)
    op_logger.setLevel(logging.DEBUG)
    if not _has_file_handler(op_logger):
        handler = _make_handler(
            OPERATIONAL_FILE, _OPERATIONAL_MAX_BYTES, _OPERATIONAL_BACKUP_COUNT,
            logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"),
        )
        op_logger.addHandler(handler)
        op_logger.propagate = False
    # Logger-level filter, so every handler on this logger (file, tests,
    # anything added later) receives the redacted message.
    _add_redacting_filter(op_logger)

    # Console output (hybrid mode: user still sees summary)
    root = logging.getLogger()
    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        console = logging.StreamHandler()
        console.setLevel(logging.INFO)
        console.setFormatter(logging.Formatter("%(message)s"))
        root.addHandler(console)
    for handler in root.handlers:
        _add_redacting_filter(handler)

    root.setLevel(logging.WARNING)


def log_audit_event(event_type, **fields):
    # Write a structured JSON audit line.
    # Every string value is redacted before the line is serialized, and the
    # current run id is attached so the dashboard can group events by run.
    record = {"event": event_type, "timestamp": datetime.now(timezone.utc).isoformat()}
    if _current_run_id is not None:
        record["run_id"] = _current_run_id
    record.update({key: _redact_value(value) for key, value in fields.items()})
    logger = logging.getLogger(_AUDIT_LOGGER_NAME)
    logger.info(json.dumps(record, sort_keys=True))


# Tier 3 hard-stop detection
# These checks look for concrete signals that indicate bot detection,
# CAPTCHA challenges, or anomalous non-API responses.  No DOM walking
# or HTML parsing – only status codes, Content-Type, and simple substring
# checks on the response body.

_HARDSTOP_KEYWORDS = ["captcha", "robot", "access denied", "blocked"]


def check_hardstop(response, platform):
    # Inspect a requests.Response for Tier 3 hard-stop indicators.

    # Returns a list of reason strings (empty = no hard-stop detected).
    # The response body is not consumed destructively – ``.text`` is a
    # cached property in ``requests``.

    reasons = []

    if response.status_code == 403:
        reasons.append("HTTP 403 - access denied / bot detection")

    if response.status_code == 503 and platform != "workday":
        reasons.append("HTTP 503 - service unavailable")

    content_type = response.headers.get("Content-Type", "")
    if "text/html" in content_type:
        body_lower = response.text.lower()
        found = [kw for kw in _HARDSTOP_KEYWORDS if kw in body_lower]
        if found:
            reasons.append(f"Anomalous HTML response containing: {', '.join(found)}")

    return reasons
