import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils import audit_log

_LOGGER_NAMES = ("job_monitor.audit", "job_monitor.operational")


@pytest.fixture
def isolated_logs(tmp_path, monkeypatch):
    # Points the audit and operational log files at a temp directory for one
    # test, so tests that run job_monitor.main() never write fake runs into
    # the real logs/ folder the dashboard reads. Handlers attached before the
    # test are detached and restored afterwards.
    monkeypatch.setattr(audit_log, "LOG_DIR", str(tmp_path))
    monkeypatch.setattr(audit_log, "AUDIT_FILE", str(tmp_path / "audit.log"))
    monkeypatch.setattr(audit_log, "OPERATIONAL_FILE", str(tmp_path / "operational.log"))
    # main() writes DB_PATH into os.environ when --db-path is given. Setting
    # it through monkeypatch first means pytest restores the original value
    # afterwards, so one test's database path never leaks into the next.
    monkeypatch.setenv("DB_PATH", str(tmp_path / "default.db"))

    saved = {}
    for name in _LOGGER_NAMES:
        logger = logging.getLogger(name)
        saved[name] = logger.handlers[:]
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)

    yield tmp_path

    for name, handlers in saved.items():
        logger = logging.getLogger(name)
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
            handler.close()
        for handler in handlers:
            logger.addHandler(handler)
    audit_log.set_run_id(None)
