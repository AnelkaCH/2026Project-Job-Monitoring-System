import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dashboard import (
    DASHBOARD_CSS,
    DASHBOARD_CSS_PATH,
    DEFAULT_SEEN_JOBS_FILE,
    apply_filters,
    build_dataframe,
    flatten_seen_jobs,
    render_badge,
    render_metric_card,
    render_title_bar,
    resolve_seen_jobs_path,
)


NEW_FORMAT = {
    "Acme": {
        "matched_ids": ["a"],
        "ambiguous_ids": ["b"],
        "details": {
            "a": {
                "title": "Security Engineer", "location": "Singapore", "posted": "",
                "posted_days_ago": 3, "link": "https://x/a", "ats": "greenhouse",
                "first_seen": "2026-08-01T00:00:00+00:00",
            },
            "b": {
                "title": "IT Analyst", "location": "", "posted": "",
                "posted_days_ago": None, "link": "https://x/b", "ats": "greenhouse",
                "first_seen": "2026-08-02T00:00:00+00:00",
            },
        },
    }
}

OLD_FORMAT = {
    "Acme": {"matched_ids": ["a"], "ambiguous_ids": ["b"]},
}


def test_flatten_new_format():
    rows = flatten_seen_jobs(NEW_FORMAT)
    assert len(rows) == 2
    by_id = {r["id"]: r for r in rows}
    assert by_id["a"]["company"] == "Acme"
    assert by_id["a"]["title"] == "Security Engineer"
    assert by_id["a"]["ats"] == "greenhouse"
    assert by_id["a"]["date_matched"] == "2026-08-01T00:00:00+00:00"
    print("PASS: new-format records flatten with full detail")


def test_flatten_old_format_does_not_crash():
    rows = flatten_seen_jobs(OLD_FORMAT)
    assert len(rows) == 2
    titles = {r["id"]: r["title"] for r in rows}
    assert titles["a"] == "a"  # id stands in for the missing title
    assert all(r["ats"] == "" for r in rows)
    assert all(r["date_matched"] == "" for r in rows)
    print("PASS: pre-v3.0 records flatten without crashing")


def test_tier_derivation():
    rows = flatten_seen_jobs(NEW_FORMAT)
    tiers = {r["id"]: r["tier"] for r in rows}
    assert tiers["a"] == "match"
    assert tiers["b"] == "ambiguous"
    print("PASS: tier derived from matched/ambiguous id lists")


def test_build_dataframe():
    df = build_dataframe(flatten_seen_jobs(NEW_FORMAT))
    assert len(df) == 2
    assert df["date_matched"].notna().all()
    assert list(df.columns) == [
        "company", "title", "location", "link", "ats",
        "tier", "date_matched", "posted_days_ago", "id",
    ]
    print("PASS: dataframe builds with parseable datetime column")


def test_build_dataframe_normalizes_tz():
    # first_seen is written as UTC-aware ISO strings; the column must be
    # tz-naive so the date-range filter (naive dates from the widget) works.
    df = build_dataframe(flatten_seen_jobs(NEW_FORMAT))
    assert df["date_matched"].dtype.kind == "M"  # datetime64
    assert df["date_matched"].dt.tz is None      # tz-naive
    assert df["date_matched"].notna().all()
    print("PASS: tz-aware dates normalized to tz-naive")


def test_date_range_filter_works():
    df = build_dataframe(flatten_seen_jobs(NEW_FORMAT))
    result = apply_filters(df, [], [], ("2026-08-02", "2026-08-02"))
    assert len(result) == 1
    assert result.iloc[0]["id"] == "b"
    print("PASS: date range filter matches within the range")


def test_filters_by_company_and_tier():
    df = build_dataframe(flatten_seen_jobs(NEW_FORMAT))
    result = apply_filters(df, ["Acme"], ["match"], (None, None))
    assert len(result) == 1
    assert result.iloc[0]["id"] == "a"
    print("PASS: company and tier filters compose correctly")


def test_filters_without_date_range_keep_nat_rows():
    df = build_dataframe(flatten_seen_jobs(OLD_FORMAT))
    result = apply_filters(df, [], [], (None, None))
    assert len(result) == 2
    print("PASS: rows with unparseable dates survive when no date range is set")


def test_path_defaults_to_project_root():
    with patch.dict("os.environ", {}, clear=True):
        with patch("sys.argv", ["dashboard.py"]):
            assert resolve_seen_jobs_path() == DEFAULT_SEEN_JOBS_FILE
    print("PASS: default path is the repo-relative seen_jobs.json")


def test_path_env_var_wins():
    with patch.dict("os.environ", {"SEEN_JOBS_PATH": "C:/other/seen_jobs.json"}):
        with patch("sys.argv", ["dashboard.py"]):
            assert resolve_seen_jobs_path() == "C:/other/seen_jobs.json"
    print("PASS: SEEN_JOBS_PATH env var overrides the default")


def test_path_cli_flag_wins():
    with patch.dict("os.environ", {}, clear=True):
        with patch("sys.argv", ["dashboard.py", "--seen-jobs", "D:/custom/seen.json"]):
            assert resolve_seen_jobs_path() == "D:/custom/seen.json"
    print("PASS: --seen-jobs CLI flag overrides everything")


def test_path_cli_flag_beats_env_var():
    with patch.dict("os.environ", {"SEEN_JOBS_PATH": "C:/other/seen_jobs.json"}):
        with patch("sys.argv", ["dashboard.py", "--seen-jobs", "D:/custom/seen.json"]):
            assert resolve_seen_jobs_path() == "D:/custom/seen.json"
    print("PASS: CLI flag beats env var when both are set")


def test_render_badge_escapes_message():
    # Message content may carry file paths and exception text, so it must be
    # HTML-escaped before insertion into our own static markup.
    out = render_badge("error", 'bad <script>alert(1)</script> & <b>path</b>')
    assert "&lt;script&gt;" in out
    assert "&amp;" in out
    assert "<script>" not in out
    print("PASS: badge message content is HTML-escaped")


def test_render_badge_kind_class():
    assert 'badge-card--error' in render_badge("error", "x")
    assert 'badge-card--warn' in render_badge("warn", "x")
    assert 'badge-card--info' in render_badge("info", "x")
    print("PASS: badge kind maps to its CSS variant class")


def test_dashboard_css_wrapped_in_style_tag():
    # Without a <style> wrapper, st.markdown prints the CSS as literal text
    # instead of applying it. Guard against that regression.
    assert DASHBOARD_CSS.lstrip().startswith("<style>")
    assert DASHBOARD_CSS.rstrip().endswith("</style>")
    assert "win-gray" in DASHBOARD_CSS
    print("PASS: dashboard CSS is wrapped in a <style> tag")


def test_win95_theme_css_file_exists():
    assert os.path.exists(DASHBOARD_CSS_PATH)
    with open(DASHBOARD_CSS_PATH, "r", encoding="utf-8") as f:
        assert len(f.read().strip()) > 0
    print("PASS: win95_theme.css exists and is non-empty")


def test_render_title_bar_is_static():
    # The title bar carries no dynamic data, so nothing to escape; it should
    # just present the retro chrome.
    out = render_title_bar()
    assert "title-bar" in out
    assert "win-btn" in out
    assert "title-bar-text" in out
    print("PASS: title bar renders static retro chrome")


def test_render_metric_card_escapes_value():
    out = render_metric_card("Companies", "<b>5</b>")
    assert "<div class=\"group-box\">" in out
    assert "group-box-label" in out and "group-box-value" in out
    assert "<b>5</b>" not in out
    assert "&lt;b&gt;5&lt;/b&gt;" in out
    print("PASS: metric card label and value are HTML-escaped")


if __name__ == "__main__":
    test_flatten_new_format()
    test_flatten_old_format_does_not_crash()
    test_tier_derivation()
    test_build_dataframe()
    test_build_dataframe_normalizes_tz()
    test_date_range_filter_works()
    test_filters_by_company_ats_tier()
    test_filters_without_date_range_keep_nat_rows()
    test_path_defaults_to_project_root()
    test_path_env_var_wins()
    test_path_cli_flag_wins()
    test_path_cli_flag_beats_env_var()
    test_render_badge_escapes_message()
    test_render_badge_kind_class()
    test_dashboard_css_wrapped_in_style_tag()
    test_win95_theme_css_file_exists()
    test_render_title_bar_is_static()
    test_render_metric_card_escapes_value()
    print("\nAll tests passed.")
