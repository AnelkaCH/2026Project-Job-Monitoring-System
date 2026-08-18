# Streamlit dashboard for the job monitoring system (v3.1.1).
#
# Reads the tracked postings from the SQLite database written by
# job_monitor.py and renders a filterable table of every matched / ambiguous
# posting: company, title, ATS platform, and the date it was first matched.
#
# The database path is configurable so the tool stays reusable for anyone
# who forks the repo and runs their own monitor. Resolution order:
#     DB_PATH env var  overrides  repo default
#     --db-path CLI flag  overrides  DB_PATH env var
#
# Usage:
#     streamlit run dashboard.py
#     DB_PATH=/path/to/jobmonitor.db streamlit run dashboard.py
#     streamlit run dashboard.py -- --db-path /path/to/jobmonitor.db

import argparse
import html
import os
import sqlite3

from db import repository

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


def main():
    if not _STREAMLIT_AVAILABLE:
        print("This dashboard needs Streamlit. Install it with: pip install streamlit pandas")
        return

    st.set_page_config(page_title="Job Monitor Dashboard", layout="wide")
    # Own static CSS, not ATS-sourced data, so unsafe_allow_html is safe here.
    st.markdown(DASHBOARD_CSS, unsafe_allow_html=True)
    st.markdown(render_title_bar(), unsafe_allow_html=True)

    path = resolve_db_path()

    try:
        db_rows = repository.list_jobs(db_path=path)
    except sqlite3.Error as exc:
        st.markdown(render_badge("error", f"Could not read database {path}: {exc}"), unsafe_allow_html=True)
        st.stop()

    rows = flatten_rows(db_rows)
    if not rows:
        st.info("No job records yet. Run `python job_monitor.py` to start tracking postings.")
        st.stop()

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
                    "**Job Monitor Dashboard** v3.1.1\n\n"
                    "Scrapes and classifies job postings across ATS platforms, "
                    "filters by keyword and location, deduplicates results, and "
                    "sends email alerts for flagged companies.\n\n"
                    f"Source file: `{html.escape(path)}`\n\n"
                    "See README.md for setup and usage."
                )


if __name__ == "__main__":
    main()
