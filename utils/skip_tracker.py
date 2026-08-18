import logging
from typing import Dict

from db import repository

logger = logging.getLogger(__name__)

DEFAULT_FLAG_THRESHOLD = 3  # flag in email after this many consecutive skips


class SkipTracker:
    # Consecutive-skip streaks now live in the SQLite database via
    # db/repository.py instead of a JSON file. Every adapter builds its own
    # SkipTracker instance, but they all write the same database; each
    # repository call opens its own connection, so concurrent workers
    # (job_monitor runs adapters via ThreadPoolExecutor) never race on a
    # shared handle.

    def __init__(self, db_path=None, flag_threshold: int = DEFAULT_FLAG_THRESHOLD):
        self.db_path = db_path
        self.flag_threshold = flag_threshold

    def record_skip(self, company: str) -> int:
        """Call when a company is skipped this cycle due to rate limiting.
        Returns the new consecutive-skip count."""
        return repository.record_skip(company, db_path=self.db_path)

    def record_success(self, company: str):
        # Call when a company completes successfully, resetting its streak.
        repository.reset_skip_streak(company, db_path=self.db_path)

    def get_flagged(self) -> Dict[str, int]:
        # Companies at or above the flag threshold, for the email notification.
        streaks = repository.list_skip_streaks(db_path=self.db_path)
        return {c: n for c, n in streaks.items() if n >= self.flag_threshold}