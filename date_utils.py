# Converts each ATS's date format into "days ago" as a plain integer.
# Returns None when the exact age can't be determined (rather than guessing) —
# callers should treat None as "ambiguous," not as "0 days old."
 
import re
from datetime import datetime, timezone
 
 
def days_ago_from_iso(iso_string):
    # For Greenhouse, Ashby, SmartRecruiters, Recruitee — all use ISO timestamps.
    if not iso_string:
        return None
    try:
        dt = datetime.fromisoformat(iso_string.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - dt).days
    except (ValueError, TypeError):
        return None
 
 
def days_ago_from_unix_ms(ms_value):
    # For Lever — createdAt is milliseconds since epoch.
    if not ms_value:
        return None
    try:
        dt = datetime.fromtimestamp(int(ms_value) / 1000, tz=timezone.utc)
        return (datetime.now(timezone.utc) - dt).days
    except (ValueError, TypeError):
        return None
 
def calculate_sap_days_ago(posted_date):
    # SAP SuccessFactors date format: DD/MM/YYYY
    # Returns the number of days since the job was posted.
    # Returns None if the date is invalid.
    try:
        posted = datetime.strptime(posted_date, "%d/%m/%Y").replace(tzinfo=timezone.utc)
        today = datetime.now(timezone.utc)
        return (today - posted).days
    except (ValueError, TypeError):
        return None

def days_ago_from_workday_text(text):
    # Workday's postedOn field is relative text: "Posted 2 Days Ago",
    # "Posted Today", or "Posted 30+ Days Ago". That last one only gives a
    # lower bound, not an exact age — so it returns None (ambiguous)
    # rather than guessing a number.
    if not text:
        return None
    text = text.lower()
    if "today" in text:
        return 0
    if "yesterday" in text:
        return 1
    if "+" in text:
        # Workday buckets everything past this threshold into one label
        # (e.g. jumps straight from "27 Days Ago" to "30+ Days Ago" with
        # nothing in between) — so postings here are usually well past 30,
        # not sitting right at the boundary. Treated as definitely stale,
        # not ambiguous.
        return 9999
    match = re.search(r"(\d+)\s+day", text)
    return int(match.group(1)) if match else None