import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dashboard import (
    DASHBOARD_CSS,
    DASHBOARD_CSS_PATH,
    apply_filters,
    build_dataframe,
    flatten_rows,
    render_badge,
    render_metric_card,
    render_title_bar,
    resolve_db_path,
)


def _row(job_id, tier="match", title="Security Engineer", first_seen="2026-08-01T00:00:00+00:00", **overrides):
    row = {
        "company": "Acme",
        "job_id": job_id,
        "tier": tier,
        "title": title,
        "location": "Singapore",
        "posted": "",
        "posted_days_ago": 3,
        "url": f"https://x/{job_id}",
        "ats_platform": "greenhouse",
        "first_seen_at": first_seen,
    }
    row.update(overrides)
    return row


ROWS = [
    _row("a"),
    _row("b", tier="ambiguous", title="IT Analyst", first_seen="2026-08-02T00:00:00+00:00"),
]


def test_flatten_rows_full_detail():
    rows = flatten_rows(ROWS)
    assert len(rows) == 2
    by_id = {r["id"]: r for r in rows}
    assert by_id["a"]["company"] == "Acme"
    assert by_id["a"]["title"] == "Security Engineer"
    assert by_id["a"]["ats"] == "greenhouse"
    assert by_id["a"]["date_matched"] == "2026-08-01T00:00:00+00:00"
    print("PASS: database rows flatten with full detail")


def test_flatten_rows_blank_title_falls_back_to_id():
    rows = flatten_rows([_row("a", title="")])
    assert rows[0]["title"] == "a"  # job id stands in for the missing title
    assert rows[0]["ats"] == "greenhouse"
    assert rows[0]["date_matched"] == "2026-08-01T00:00:00+00:00"
    print("PASS: rows with a blank title flatten without crashing")


def test_tier_preserved_in_flatten():
    rows = flatten_rows(ROWS)
    tiers = {r["id"]: r["tier"] for r in rows}
    assert tiers["a"] == "match"
    assert tiers["b"] == "ambiguous"
    print("PASS: tier carried through from the database row")


def test_build_dataframe():
    df = build_dataframe(flatten_rows(ROWS))
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
    df = build_dataframe(flatten_rows(ROWS))
    assert df["date_matched"].dtype.kind == "M"  # datetime64
    assert df["date_matched"].dt.tz is None      # tz-naive
    assert df["date_matched"].notna().all()
    print("PASS: tz-aware dates normalized to tz-naive")


def test_date_range_filter_works():
    df = build_dataframe(flatten_rows(ROWS))
    result = apply_filters(df, [], [], ("2026-08-02", "2026-08-02"))
    assert len(result) == 1
    assert result.iloc[0]["id"] == "b"
    print("PASS: date range filter matches within the range")


def test_filters_by_company_and_tier():
    df = build_dataframe(flatten_rows(ROWS))
    result = apply_filters(df, ["Acme"], ["match"], (None, None))
    assert len(result) == 1
    assert result.iloc[0]["id"] == "a"
    print("PASS: company and tier filters compose correctly")


def test_filters_without_date_range_keep_nat_rows():
    df = build_dataframe(flatten_rows([_row("a", first_seen="")]))
    result = apply_filters(df, [], [], (None, None))
    assert len(result) == 1
    print("PASS: rows with unparseable dates survive when no date range is set")


def test_path_defaults_to_project_root():
    from db import repository
    with patch.dict("os.environ", {}, clear=True):
        with patch("sys.argv", ["dashboard.py"]):
            assert resolve_db_path() == str(repository.get_db_path())
    print("PASS: default path is the repo-relative database file")


def test_path_env_var_wins():
    with patch.dict("os.environ", {"DB_PATH": "C:/other/jobmonitor.db"}):
        with patch("sys.argv", ["dashboard.py"]):
            assert resolve_db_path() == "C:/other/jobmonitor.db"
    print("PASS: DB_PATH env var overrides the default")


def test_path_cli_flag_wins():
    with patch.dict("os.environ", {}, clear=True):
        with patch("sys.argv", ["dashboard.py", "--db-path", "D:/custom/jobmonitor.db"]):
            assert resolve_db_path() == "D:/custom/jobmonitor.db"
    print("PASS: --db-path CLI flag overrides everything")


def test_path_cli_flag_beats_env_var():
    with patch.dict("os.environ", {"DB_PATH": "C:/other/jobmonitor.db"}):
        with patch("sys.argv", ["dashboard.py", "--db-path", "D:/custom/jobmonitor.db"]):
            assert resolve_db_path() == "D:/custom/jobmonitor.db"
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
    test_flatten_rows_full_detail()
    test_flatten_rows_blank_title_falls_back_to_id()
    test_tier_preserved_in_flatten()
    test_build_dataframe()
    test_build_dataframe_normalizes_tz()
    test_date_range_filter_works()
    test_filters_by_company_and_tier()
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
