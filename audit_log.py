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

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
AUDIT_FILE = os.path.join(LOG_DIR, "audit.log")
OPERATIONAL_FILE = os.path.join(LOG_DIR, "operational.log")

_AUDIT_LOGGER_NAME = "job_monitor.audit"
_OPERATIONAL_LOGGER_NAME = "job_monitor.operational"

# Max 5 MB per file, keep 5 audit backups and 3 operational backups.
_AUDIT_MAX_BYTES = 5_242_880
_AUDIT_BACKUP_COUNT = 5
_OPERATIONAL_MAX_BYTES = 5_242_880
_OPERATIONAL_BACKUP_COUNT = 3


def _ensure_log_dir():
    os.makedirs(LOG_DIR, exist_ok=True)


def _make_handler(path, max_bytes, backup_count, formatter):
    handler = RotatingFileHandler(
        path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
    )
    handler.setFormatter(formatter)
    return handler


def setup_logging():
    """Idempotent – safe to call on every run without stacking handlers."""
    _ensure_log_dir()

    # ── Audit logger ──────────────────────────────────────────────────
    audit_logger = logging.getLogger(_AUDIT_LOGGER_NAME)
    audit_logger.setLevel(logging.INFO)
    if not audit_logger.handlers:
        handler = _make_handler(
            AUDIT_FILE, _AUDIT_MAX_BYTES, _AUDIT_BACKUP_COUNT,
            logging.Formatter("%(message)s"),
        )
        audit_logger.addHandler(handler)
        audit_logger.propagate = False

    # ── Operational logger ────────────────────────────────────────────
    op_logger = logging.getLogger(_OPERATIONAL_LOGGER_NAME)
    op_logger.setLevel(logging.DEBUG)
    if not op_logger.handlers:
        handler = _make_handler(
            OPERATIONAL_FILE, _OPERATIONAL_MAX_BYTES, _OPERATIONAL_BACKUP_COUNT,
            logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"),
        )
        op_logger.addHandler(handler)
        op_logger.propagate = False

    # ── Console output (hybrid mode: user still sees summary) ─────────
    root = logging.getLogger()
    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        console = logging.StreamHandler()
        console.setLevel(logging.INFO)
        console.setFormatter(logging.Formatter("%(message)s"))
        root.addHandler(console)

    root.setLevel(logging.WARNING)


def log_audit_event(event_type, **fields):
    """Write a structured JSON audit line."""
    record = {"event": event_type, "timestamp": datetime.now(timezone.utc).isoformat()}
    record.update(fields)
    logger = logging.getLogger(_AUDIT_LOGGER_NAME)
    logger.info(json.dumps(record, sort_keys=True))


# ── Tier 3 hard-stop detection ───────────────────────────────────────
# These checks look for concrete signals that indicate bot detection,
# CAPTCHA challenges, or anomalous non-API responses.  No DOM walking
# or HTML parsing – only status codes, Content-Type, and simple substring
# checks on the response body.

_HARDSTOP_KEYWORDS = ["captcha", "robot", "access denied", "blocked"]


def check_hardstop(response, platform):
    """Inspect a requests.Response for Tier 3 hard-stop indicators.

    Returns a list of reason strings (empty = no hard-stop detected).
    The response body is not consumed destructively – ``.text`` is a
    cached property in ``requests``.
    """
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
