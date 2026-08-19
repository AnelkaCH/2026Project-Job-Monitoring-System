import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dashboard import (
    DASHBOARD_CSS,
    DASHBOARD_CSS_PATH,
    apply_filters,
    assign_cycles,
    build_dataframe,
    classify_cycles,
    filter_hardstops,
    flatten_rows,
    parse_audit_log,
    parse_operational_log,
    render_badge,
    render_metric_card,
    render_title_bar,
    resolve_db_path,
    resolve_log_dir,
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


def _write_temp_log(content):
    fd, path = tempfile.mkstemp(suffix=".log", text=True)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
    return path


AUDIT_LINES = """\
{"ats": "workday", "company": "Acme", "event": "QUERY", "timestamp": "2026-08-18T03:27:31+00:00"}
{"ats": "workday", "company": "Acme", "event": "CLASSIFY", "match_count": 3, "ambiguous_count": 1, "new_count": 2, "timestamp": "2026-08-18T03:27:34+00:00"}
{"company": "Locad", "event": "TIER3_HARDSTOP", "platform": "personio", "reason": "throttle_exhausted", "attempts": 5, "timestamp": "2026-08-18T03:31:36+00:00"}
{"ats": "workday", "company": "Acme", "event": "CLASSIFY", "match_count": 1, "ambiguous_count": 0, "new_count": 0, "timestamp": "2026-08-18T05:00:00+00:00"}
{"event": "TIER3_HARDSTOP", "company": "Globex", "ats": "lever", "reasons": ["HTTP 403 - access denied / bot detection"], "status": 403, "timestamp": "2026-08-18T05:00:10+00:00"}
{"this line is not valid json
"""

OPERATIONAL_LINES = """\
2026-08-18 10:30:03,346 [INFO] Checking NTT DATA (workday)...
2026-08-18 10:30:03,346 [WARNING] Skipping NTT DATA this cycle: robots.txt disallows /services/recruiting/v1/jobs (streak: 2)
2026-08-18 10:30:03,347 [WARNING] [NTT DATA] [SKIPPED] not allowed by robots.txt.
2026-08-18 11:45:08,453 [INFO] [Zoox] 2 new matching posting(s):
2026-08-18 11:50:00,124 [WARNING] Skipping Locad this cycle: Locad (personio) skipped after 5 attempt(s): rate-limited (streak: 3)
"""


def test_parse_audit_log_skips_malformed_lines():
    path = _write_temp_log(AUDIT_LINES)
    try:
        records = parse_audit_log(path)
        assert len(records) == 5
        assert {r["event"] for r in records} == {"QUERY", "CLASSIFY", "TIER3_HARDSTOP"}
        assert all(isinstance(r, dict) for r in records)
    finally:
        os.remove(path)
    print("PASS: audit log parses valid JSON lines and skips the malformed one")


def test_parse_audit_log_missing_file_returns_empty():
    assert parse_audit_log("C:/does/not/exist/audit.log") == []
    print("PASS: missing audit log returns an empty list")


def test_filter_hardstops_normalizes_reason_and_platform():
    path = _write_temp_log(AUDIT_LINES)
    try:
        rows = filter_hardstops(parse_audit_log(path))
        assert len(rows) == 2
        by_company = {r["company"]: r for r in rows}
        assert by_company["Locad"]["reason"] == "throttle_exhausted"
        assert by_company["Locad"]["platform"] == "personio"
        assert by_company["Globex"]["reason"] == "HTTP 403 - access denied / bot detection"
        assert by_company["Globex"]["platform"] == "lever"
    finally:
        os.remove(path)
    print("PASS: hard-stop rows normalize string and list reasons and ats/platform keys")


def test_filter_hardstops_empty_when_none_present():
    path = _write_temp_log('{"event": "QUERY", "company": "Acme", "timestamp": "2026-08-18T03:27:31+00:00"}\n')
    try:
        assert filter_hardstops(parse_audit_log(path)) == []
    finally:
        os.remove(path)
    print("PASS: no hard-stop rows when only QUERY events exist")


def test_classify_cycles_aggregates_per_cycle():
    path = _write_temp_log(AUDIT_LINES)
    try:
        cycles = classify_cycles(parse_audit_log(path))
        assert len(cycles) == 2
        by_start = {c["cycle_label"]: c for c in cycles.to_dict("records")}
        assert by_start["18 Aug 03:27"]["match_count"] == 3
        assert by_start["18 Aug 03:27"]["ambiguous_count"] == 1
        assert by_start["18 Aug 03:27"]["new_count"] == 2
        assert by_start["18 Aug 05:00"]["match_count"] == 1
        assert by_start["18 Aug 05:00"]["ambiguous_count"] == 0
        assert by_start["18 Aug 05:00"]["new_count"] == 0
    finally:
        os.remove(path)
    print("PASS: CLASSIFY events are bucketed into cycles and summed")


def test_classify_cycles_empty_returns_empty_frame():
    path = _write_temp_log('{"event": "QUERY", "company": "Acme", "timestamp": "2026-08-18T03:27:31+00:00"}\n')
    try:
        df = classify_cycles(parse_audit_log(path))
        assert df.empty
        assert "cycle_label" in df.columns
    finally:
        os.remove(path)
    print("PASS: classify_cycles returns an empty frame when no CLASSIFY events exist")


def test_assign_cycles_gap_splits_runs():
    records = [
        {"timestamp": "2026-08-18T03:27:00+00:00"},
        {"timestamp": "2026-08-18T03:28:00+00:00"},
        {"timestamp": "2026-08-18T10:00:00+00:00"},
        {"timestamp": "not-a-date"},
    ]
    tagged = assign_cycles(records, gap_seconds=600)
    cycles = [r["cycle"] for r in tagged]
    assert cycles[0] == 0 and cycles[1] == 0 and cycles[2] == 1
    assert cycles[3] == 1  # unparseable timestamp keeps the current cycle
    print("PASS: gap-based clustering splits runs and keeps bad timestamps")


def test_parse_operational_log_extracts_skips():
    path = _write_temp_log(OPERATIONAL_LINES)
    try:
        frame = parse_operational_log(path)
        assert len(frame) == 2
        by_company = frame.set_index("company")
        assert by_company.loc["NTT DATA", "reason"] == "robots.txt disallows /services/recruiting/v1/jobs"
        assert by_company.loc["NTT DATA", "streak"] == 2
        assert by_company.loc["Locad", "reason"] == "Locad (personio) skipped after 5 attempt(s): rate-limited"
        assert by_company.loc["Locad", "streak"] == 3
        assert len(frame.columns.tolist()) == 6  # cycle, cycle_start, company, reason, streak, timestamp
    finally:
        os.remove(path)
    print("PASS: operational log skips parse with reason, streak, and cycle")


def test_parse_operational_log_missing_file_empty_frame():
    frame = parse_operational_log("C:/does/not/exist/operational.log")
    assert frame.empty
    assert "company" in frame.columns
    print("PASS: missing operational log returns an empty frame")


def test_resolve_log_dir_defaults_to_repo_logs():
    from utils.audit_log import LOG_DIR
    with patch.dict("os.environ", {}, clear=True):
        with patch("sys.argv", ["dashboard.py"]):
            assert resolve_log_dir() == str(LOG_DIR)
    print("PASS: default log dir is the repo logs directory")


def test_resolve_log_dir_env_var_wins():
    with patch.dict("os.environ", {"LOG_DIR": "C:/other/logs"}):
        with patch("sys.argv", ["dashboard.py"]):
            assert resolve_log_dir() == "C:/other/logs"
    print("PASS: LOG_DIR env var overrides the default")


def test_resolve_log_dir_cli_flag_wins():
    with patch.dict("os.environ", {}, clear=True):
        with patch("sys.argv", ["dashboard.py", "--log-dir", "D:/custom/logs"]):
            assert resolve_log_dir() == "D:/custom/logs"
    print("PASS: --log-dir CLI flag overrides everything")


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
    test_parse_audit_log_skips_malformed_lines()
    test_parse_audit_log_missing_file_returns_empty()
    test_filter_hardstops_normalizes_reason_and_platform()
    test_filter_hardstops_empty_when_none_present()
    test_classify_cycles_aggregates_per_cycle()
    test_classify_cycles_empty_returns_empty_frame()
    test_assign_cycles_gap_splits_runs()
    test_parse_operational_log_extracts_skips()
    test_parse_operational_log_missing_file_empty_frame()
    test_resolve_log_dir_defaults_to_repo_logs()
    test_resolve_log_dir_env_var_wins()
    test_resolve_log_dir_cli_flag_wins()
    print("\nAll tests passed.")
