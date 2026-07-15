import json
import logging
from pathlib import Path
from typing import Dict
 
logger = logging.getLogger(__name__)
 
# Anchored to this file's own location, not the working directory, so it
# lands next to seen_jobs.json regardless of where the script is invoked
# from. This matters in GitHub Actions specifically: whatever workflow step
# already commits seen_jobs.json back to the repo (needed for its own
# cross-cycle dedup) should pick this file up too, since it sits right
# beside it - no separate persistence mechanism needed for this file alone.
DEFAULT_PATH = Path(__file__).resolve().parent / "skip_history.json"
DEFAULT_FLAG_THRESHOLD = 3  # flag in email after this many consecutive skips
 
 
class SkipTracker:
    def __init__(self, path: Path = DEFAULT_PATH, flag_threshold: int = DEFAULT_FLAG_THRESHOLD):
        self.path = Path(path)
        self.flag_threshold = flag_threshold
        self.data: Dict[str, int] = self._load()
 
    def _load(self) -> Dict[str, int]:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text())
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Could not read %s, starting fresh: %s", self.path, exc)
        return {}
 
    def _save(self):
        self.path.write_text(json.dumps(self.data, indent=2))
 
    def record_skip(self, company: str) -> int:
        """Call when a company is skipped this cycle due to rate limiting.
        Returns the new consecutive-skip count."""
        self.data[company] = self.data.get(company, 0) + 1
        self._save()
        return self.data[company]
 
    def record_success(self, company: str):
        """Call when a company completes successfully, resetting its streak."""
        if company in self.data:
            del self.data[company]
            self._save()
 
    def get_flagged(self) -> Dict[str, int]:
        """Companies at or above the flag threshold, for the email notification."""
        return {c: n for c, n in self.data.items() if n >= self.flag_threshold}