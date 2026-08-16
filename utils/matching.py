# Keyword matching helpers used by the filter layer in job_monitor.py.
#
# Matching is word-boundary based, not raw substring: a term like "engineer"
# should only match a standalone word, so "Process Support Engineer" no
# longer trips on generic words shared by unrelated domains.

import re


def keyword_matches(text: str, keywords) -> str | None:
    # Returns the first matching keyword, or None if no match.
    #
    # Keywords are stripped of surrounding whitespace before building the
    # pattern. Older config entries like "sr " relied on a trailing space to
    # fake a word boundary for substring matching; with real \b regex that
    # space is unnecessary and is dropped so \b handles the edge itself
    # (e.g. "sr " still matches "SR ENGINEER" but not "senior").
    text_lower = text.lower()
    for kw in keywords:
        kw_clean = kw.strip().lower()
        pattern = r"\b" + re.escape(kw_clean) + r"\b"
        if re.search(pattern, text_lower):
            return kw
    return None


# Detects a fixed-term date range in a job title, e.g. "(January to June 2027)"
# or "[August - December 2026]". A specific month paired with a specific year
# inside parentheses or brackets is a strong signal of an internship-style
# posting even when no literal role keyword appears in the title. A bare year
# on its own is deliberately not enough (e.g. "2027 Internship" is already
# covered by role keywords anyway).
MONTH_YEAR_RANGE_PATTERN = re.compile(
    r"[\(\[]"
    r"(?:january|february|march|april|may|june|july|august|september|"
    r"october|november|december)[a-z]*"
    r".{0,20}?"
    r"(20[2-3]\d)"
    r"[\)\]]",
    re.IGNORECASE,
)


def has_date_range_signal(title: str) -> bool:
    return bool(MONTH_YEAR_RANGE_PATTERN.search(title))
