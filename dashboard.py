# Streamlit dashboard for the job monitoring system (v3.4).
#
# Three tabs:
#   Postings         - the filterable matched / ambiguous table from the
#                      SQLite database written by job_monitor.py
#   Audit / Security - Tier 3 hard-stop events and per-cycle classification
#                      counts, parsed from logs/audit.log (JSON lines)
#   Operations       - skip events (robots.txt, rate limits, bot-detection)
#                      parsed from logs/operational.log
#
# The database and log paths are configurable so the tool stays reusable for
# anyone who forks the repo and runs their own monitor. Resolution order:
#     DB_PATH env var  overrides  repo default
#     --db-path CLI flag  overrides  DB_PATH env var
#     LOG_DIR env var  overrides  repo logs dir
#     --log-dir CLI flag  overrides  LOG_DIR env var
#
# Usage:
#     streamlit run dashboard.py
#     DB_PATH=/path/to/jobmonitor.db streamlit run dashboard.py
#     streamlit run dashboard.py -- --db-path /path/to/jobmonitor.db
#     streamlit run dashboard.py -- --log-dir /path/to/logs

import argparse
import html
import json
import os
import re
import sqlite3

from db import repository
from utils.audit_log import LOG_DIR

try:
    import pandas as pd
    import streamlit as st
    _STREAMLIT_AVAILABLE = True
except ImportError:
    pd = None
    st = None
    _STREAMLIT_AVAILABLE = False

# Dashboard theming (v3.1.1). The stylesheet lives in its own file so it
# stays maintainable, and it is wrapped in a <style> tag here (required for
# st.markdown to apply it rather than print it). This CSS is our own static
# markup, never derived from ATS-sourced posting data, so unsafe_allow_html
# is safe. Posting text is still never passed through it (see the table note
# below); the only dynamic values rendered into markup go through
# html.escape() first.
DASHBOARD_CSS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "win95_theme.css")


def load_dashboard_css():
    # Reads win95_theme.css and wraps it in a <style> tag. A missing
    # stylesheet leaves the dashboard unstyled rather than crashing it.
    if not os.path.exists(DASHBOARD_CSS_PATH):
        return ""
    with open(DASHBOARD_CSS_PATH, "r", encoding="utf-8") as f:
        return f"<style>\n{f.read()}\n</style>"


DASHBOARD_CSS = load_dashboard_css()


def render_badge(kind, message):
    # Inset status/error card. kind is one of "info", "warn", "error" and
    # picks the severity tint. message is escaped because it can hold file
    # paths and exception text.
    return f'<div class="badge-card badge-card--{html.escape(kind)}">{html.escape(message)}</div>'


def render_title_bar():
    # Retro window title bar for the dashboard header, with fake window
    # buttons. All text here is our own static markup.
    return """
    <div class="title-bar">
        <span class="title-bar-text">Job Monitor Dashboard</span>
        <span class="title-bar-buttons">
            <span class="win-btn">_</span>
            <span class="win-btn">&#9633;</span>
            <span class="win-btn">&#10005;</span>
        </span>
    </div>
    """


def render_metric_card(label, value):
    # Win95 group box metric tile. label and value are escaped before
    # insertion into our own static markup.
    return f"""
    <div class="group-box">
        <span class="group-box-label">{html.escape(label)}</span>
        <span class="group-box-value">{html.escape(str(value))}</span>
    </div>
    """


def resolve_db_path():
    # Env var first, then CLI flag, then the repo default. argparse's default
    # reads the env var so a flag still wins when both are set.
    parser = argparse.ArgumentParser(description="Job Monitor Dashboard")
    parser.add_argument(
        "--db-path",
        default=os.environ.get("DB_PATH") or str(repository.get_db_path()),
        help="Path to the SQLite database (default: DB_PATH env var or repo file)",
    )
    args, _ = parser.parse_known_args()
    return args.db_path


def resolve_log_dir():
    # --log-dir flag first, then LOG_DIR env var, then the repo logs dir.
    parser = argparse.ArgumentParser(description="Job Monitor Dashboard")
    parser.add_argument(
        "--log-dir",
        default=os.environ.get("LOG_DIR") or str(LOG_DIR),
        help="Path to the logs directory (default: LOG_DIR env var or repo logs dir)",
    )
    args, _ = parser.parse_known_args()
    return args.log_dir


# Audit / operational log parsing (v3.4).
# The audit log is JSON lines written by utils/audit_log.py; the operational
# log is a timestamped plain-text stream. Both are read with stdlib json/re so
# the dashboard adds no new dependencies. The logs are runtime output, not
# trusted input, but they are still rendered through Streamlit's default
# escaping and the same render_badge / render_metric_card escape helpers.

# A monitoring run carries no explicit id, so cycles are inferred from
# timestamps: a new cycle starts when consecutive events are more than this
# many seconds apart.
AUDIT_CYCLE_GAP_SECONDS = 30 * 60

_SKIP_LINE_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ \[WARNING\] Skipping (.+?) this cycle: (.+)$"
)
_STREAK_RE = re.compile(r"\(streak: (\d+)\)\s*$")


def _parse_timestamp(value):
    # Tolerant parse of ISO (audit) and log-local (operational) timestamps into
    # a pandas Timestamp; unparseable values become NaT.
    return pd.to_datetime(value, errors="coerce")


def parse_audit_log(path):
    # Reads the JSON-lines audit log into a list of dicts. A malformed or
    # partial trailing line (possible after a rotation) is skipped rather than
    # failing the whole panel.
    records = []
    if not os.path.exists(path):
        return records
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(record, dict):
                records.append(record)
    return records


def filter_hardstops(records):
    # TIER3_HARDSTOP events into flat rows. The reason is a string from the
    # monitor or a list from check_hardstop(), and the platform key is either
    # 'platform' (rate limiter) or 'ats' (monitor), so both are normalized.
    rows = []
    for record in records:
        if record.get("event") != "TIER3_HARDSTOP":
            continue
        reason = record.get("reason")
        if reason is None:
            reasons = record.get("reasons") or []
            reason = "; ".join(reasons)
        elif isinstance(reason, list):
            reason = "; ".join(reason)
        rows.append({
            "company": record.get("company", ""),
            "platform": record.get("platform") or record.get("ats") or "",
            "reason": reason or "",
            "timestamp": record.get("timestamp", ""),
        })
    return rows


def assign_cycles(records, gap_seconds=AUDIT_CYCLE_GAP_SECONDS):
    # Tags a list of dicts (each with a 'timestamp') with a 'cycle' index and
    # a 'cycle_start' Timestamp. Records with an unparseable timestamp sort
    # last and keep the current cycle, so one bad line cannot split a run.
    ordered = sorted(
        records,
        key=lambda r: (
            pd.isna(_parse_timestamp(r.get("timestamp", ""))),
            _parse_timestamp(r.get("timestamp", "")),
        ),
    )
    cycle = 0
    prev_ts = None
    for record in ordered:
        ts = _parse_timestamp(record.get("timestamp", ""))
        if prev_ts is not None and not pd.isna(ts):
            if (ts - prev_ts).total_seconds() > gap_seconds:
                cycle += 1
        record["cycle"] = cycle
        record["cycle_start"] = ts
        if not pd.isna(ts):
            prev_ts = ts
    return ordered


def classify_cycles(records, gap_seconds=AUDIT_CYCLE_GAP_SECONDS):
    # CLASSIFY events aggregated per run cycle: one row per cycle with the
    # summed match / ambiguous / new counts for the st.bar_chart.
    columns = ["cycle", "cycle_start", "match_count", "ambiguous_count", "new_count", "cycle_label"]
    classify_rows = []
    for record in records:
        if record.get("event") != "CLASSIFY":
            continue
        classify_rows.append({
            "company": record.get("company", ""),
            "match_count": int(record.get("match_count", 0) or 0),
            "ambiguous_count": int(record.get("ambiguous_count", 0) or 0),
            "new_count": int(record.get("new_count", 0) or 0),
            "timestamp": record.get("timestamp", ""),
        })
    if not classify_rows:
        return pd.DataFrame(columns=columns)
    tagged = assign_cycles(classify_rows, gap_seconds)
    df = pd.DataFrame(tagged)
    grouped = df.groupby("cycle", as_index=False).agg(
        cycle_start=("cycle_start", "min"),
        match_count=("match_count", "sum"),
        ambiguous_count=("ambiguous_count", "sum"),
        new_count=("new_count", "sum"),
    )
    grouped = grouped.sort_values("cycle_start").reset_index(drop=True)
    grouped["cycle_label"] = grouped["cycle_start"].dt.strftime("%d %b %H:%M")
    return grouped


def parse_operational_log(path, gap_seconds=AUDIT_CYCLE_GAP_SECONDS):
    # Reads the operational log's skip warnings into a DataFrame tagged with
    # the inferred cycle. Only the 'Skipping X this cycle:' lines are used so
    # the paired '[SKIPPED]' follow-up line is not double counted.
    columns = ["cycle", "cycle_start", "company", "reason", "streak", "timestamp"]
    rows = []
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                m = _SKIP_LINE_RE.match(line)
                if not m:
                    continue
                timestamp, company, detail = m.group(1), m.group(2), m.group(3)
                streak = None
                sm = _STREAK_RE.search(detail)
                if sm:
                    streak = int(sm.group(1))
                    detail = detail[: sm.start()].rstrip().rstrip(":").strip()
                rows.append({
                    "company": company,
                    "reason": detail,
                    "streak": streak,
                    "timestamp": timestamp,
                })
    if not rows:
        return pd.DataFrame(columns=columns)
    tagged = assign_cycles(rows, gap_seconds)
    frame = pd.DataFrame(tagged)
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], errors="coerce")
    frame["cycle_start"] = pd.to_datetime(frame["cycle_start"], errors="coerce")
    return frame


def flatten_rows(rows):
    # Maps repository rows into the dashboard row shape: one row per posting
    # with company, tier, title, location, link, ats, and first-seen date.
    flattened = []
    for row in rows:
        flattened.append({
            "company": row["company"],
            "title": row["title"] or row["job_id"],
            "location": row["location"] or "",
            "link": row["url"] or "",
            "ats": row["ats_platform"] or "",
            "tier": row["tier"],
            "date_matched": row["first_seen_at"] or "",
            "posted_days_ago": row["posted_days_ago"],
            "id": row["job_id"],
        })
    return flattened


def build_dataframe(rows):
    # Converts flattened rows into a DataFrame with a real datetime column so
    # the date-range filter and the formatted table column work off the same
    # values. Unparseable / empty dates become NaT.
    #
    # first_seen values are written as UTC-aware ISO strings, which pandas
    # would keep as a tz-aware datetime dtype. The date filter compares
    # against naive dates from st.date_input, so the column is normalized to
    # tz-naive here to keep every comparison and the rendered column simple.
    df = pd.DataFrame(rows)
    if not df.empty:
        df["date_matched"] = pd.to_datetime(df["date_matched"], errors="coerce").dt.tz_localize(None)
    return df


def apply_filters(df, selected_companies, selected_tiers, date_range):
    # Applies the sidebar filter selections to the dataframe. date_range is
    # whatever st.date_input returned: a (start, end) tuple when a range is
    # active, otherwise None or a single date. Rows with an unparseable date
    # (NaT) only survive when no date range is active.
    filtered = df
    if selected_companies:
        filtered = filtered[filtered["company"].isin(selected_companies)]
    if selected_tiers:
        filtered = filtered[filtered["tier"].isin(selected_tiers)]
    if isinstance(date_range, tuple) and len(date_range) == 2 and date_range[0] is not None:
        start = pd.Timestamp(date_range[0])
        end = pd.Timestamp(date_range[1]) + pd.Timedelta(days=1)
        filtered = filtered[filtered["date_matched"].notna()]
        filtered = filtered[(filtered["date_matched"] >= start) & (filtered["date_matched"] < end)]
    return filtered


def render_postings_tab(path):
    # The original single-page postings table, moved into its own tab so the
    # audit and operations tabs render even when the database is empty.
    try:
        db_rows = repository.list_jobs(db_path=path)
    except sqlite3.Error as exc:
        st.markdown(render_badge("error", f"Could not read database {path}: {exc}"), unsafe_allow_html=True)
        return

    rows = flatten_rows(db_rows)
    if not rows:
        st.info("No job records yet. Run `python job_monitor.py` to start tracking postings.")
        return

    df = build_dataframe(rows)

    with st.sidebar:
        st.header("Filters")
        companies = sorted(df["company"].dropna().unique().tolist())
        tiers = sorted(df["tier"].dropna().unique().tolist())

        selected_companies = st.multiselect("Company", companies, key="company_filter")
        selected_tiers = st.multiselect("Tier", tiers, key="tier_filter")

        min_date = df["date_matched"].min()
        max_date = df["date_matched"].max()
        if pd.isna(min_date) or pd.isna(max_date):
            date_range = (None, None)
        else:
            date_range = st.date_input(
                "Date matched",
                value=(min_date.date(), max_date.date()),
                min_value=min_date.date(),
                max_value=max_date.date(),
                key="date_filter",
            )

    filtered = apply_filters(df, selected_companies, selected_tiers, date_range)

    col1, col2, col3, col4 = st.columns(4)
    for col, (label, value) in zip(
        (col1, col2, col3, col4),
        (
            ("Companies tracked", df["company"].nunique()),
            ("Total postings", len(df)),
            ("Matches", int((df["tier"] == "match").sum())),
            ("Ambiguous", int((df["tier"] == "ambiguous").sum())),
        ),
    ):
        col.markdown(render_metric_card(label, value), unsafe_allow_html=True)

    st.subheader(f"Postings ({len(filtered)})")
    # Streamlit auto-escapes cell text by default, so this table renders
    # untrusted posting text safely without unsafe_allow_html anywhere in
    # this file (storage-time sanitization in utils/schema.py is the first
    # line of defense; this default escaping is intentional second).
    display_cols = ["company", "title", "date_matched", "location", "posted_days_ago", "link"]
    st.dataframe(
        filtered[display_cols],
        width="stretch",
        hide_index=True,
        column_config={
            "link": st.column_config.LinkColumn("Link", display_text="Open posting"),
            "date_matched": st.column_config.DatetimeColumn("Date matched", format="D MMM YYYY"),
            "posted_days_ago": st.column_config.NumberColumn("Age (days)", format="%d"),
        },
    )

    with st.container(key="taskbar"):
        with st.container(key="actionbar"):
            st.download_button(
                "Export CSV",
                data=filtered[display_cols].to_csv(index=False).encode("utf-8"),
                file_name="postings.csv",
                mime="text/csv",
                icon=":material/download:",
            )
            with st.popover("About", icon=":material/info:"):
                st.markdown(
                    "**Job Monitor Dashboard** v3.4\n\n"
                    "Scrapes and classifies job postings across ATS platforms, "
                    "filters by keyword and location, deduplicates results, and "
                    "sends email alerts for flagged companies.\n\n"
                    f"Source file: `{html.escape(path)}`\n\n"
                    "See README.md for setup and usage."
                )


def render_audit_tab(log_dir):
    # Audit / Security panel: Tier 3 hard-stops plus per-cycle classification
    # counts, both parsed from the JSON-lines audit log.
    audit_path = os.path.join(log_dir, "audit.log")
    if not os.path.exists(audit_path):
        st.markdown(
            render_badge("info", f"No audit log at {audit_path}. Run `python job_monitor.py` to generate one."),
            unsafe_allow_html=True,
        )
        return

    records = parse_audit_log(audit_path)
    if not records:
        st.info("Audit log is empty.")
        return

    st.subheader("Tier 3 hard-stops (bot detection)")
    hardstops = filter_hardstops(records)
    if hardstops:
        st.markdown(
            render_badge(
                "warn",
                f"{len(hardstops)} Tier 3 hard-stop event(s) recorded. These are deliberate stops, not bugs.",
            ),
            unsafe_allow_html=True,
        )
        hard_df = pd.DataFrame(hardstops)
        hard_df["timestamp"] = pd.to_datetime(hard_df["timestamp"], errors="coerce").dt.tz_localize(None)
        st.dataframe(
            hard_df[["timestamp", "company", "platform", "reason"]],
            width="stretch",
            hide_index=True,
            column_config={
                "timestamp": st.column_config.DatetimeColumn("Timestamp", format="D MMM YYYY HH:mm"),
                "reason": st.column_config.TextColumn("Reason", width="large"),
            },
        )
    else:
        st.info("No Tier 3 hard-stop events recorded.")

    st.subheader("Classification per run cycle")
    cycles = classify_cycles(records)
    if not cycles.empty:
        chart = cycles.set_index("cycle_label")[["match_count", "ambiguous_count", "new_count"]]
        st.bar_chart(chart)
        st.caption(
            "Classified postings per monitoring cycle (match / ambiguous / new). "
            "A new cycle starts when events are more than 30 minutes apart."
        )
        with st.expander("Classification details per cycle"):
            st.dataframe(cycles, width="stretch", hide_index=True)
    else:
        st.info("No CLASSIFY events recorded yet.")


def render_operations_tab(log_dir):
    # Operations panel: skip events (robots.txt, rate limits, bot-detection)
    # parsed from the operational log, with the inferred cycle and streak.
    op_path = os.path.join(log_dir, "operational.log")
    if not os.path.exists(op_path):
        st.markdown(
            render_badge(
                "info",
                f"No operational log at {op_path}. Run `python job_monitor.py` to generate one.",
            ),
            unsafe_allow_html=True,
        )
        return

    frame = parse_operational_log(op_path)
    if frame.empty:
        st.info("Operational log has no skip records yet.")
        return

    col1, col2, col3 = st.columns(3)
    max_streak = int(frame["streak"].max()) if frame["streak"].notna().any() else 0
    for col, (label, value) in zip(
        (col1, col2, col3),
        (
            ("Skip events", len(frame)),
            ("Companies skipped", frame["company"].nunique()),
            ("Max skip streak", max_streak),
        ),
    ):
        col.markdown(render_metric_card(label, value), unsafe_allow_html=True)

    st.subheader("Skip events (recent first)")
    display = frame.sort_values("timestamp", ascending=False).reset_index(drop=True)
    st.dataframe(
        display[["timestamp", "company", "reason", "streak"]],
        width="stretch",
        hide_index=True,
        column_config={
            "timestamp": st.column_config.DatetimeColumn("Timestamp", format="D MMM YYYY HH:mm"),
            "reason": st.column_config.TextColumn("Reason", width="large"),
            "streak": st.column_config.NumberColumn("Streak", format="%d"),
        },
    )


def main():
    if not _STREAMLIT_AVAILABLE:
        print("This dashboard needs Streamlit. Install it with: pip install streamlit pandas")
        return

    st.set_page_config(page_title="Job Monitor Dashboard", layout="wide")
    # Own static CSS, not ATS-sourced data, so unsafe_allow_html is safe here.
    st.markdown(DASHBOARD_CSS, unsafe_allow_html=True)
    st.markdown(render_title_bar(), unsafe_allow_html=True)

    tab_postings, tab_audit, tab_ops = st.tabs(["Postings", "Audit", "Operations"])

    with tab_postings:
        render_postings_tab(resolve_db_path())

    with tab_audit:
        render_audit_tab(resolve_log_dir())

    with tab_ops:
        render_operations_tab(resolve_log_dir())


if __name__ == "__main__":
    main()
