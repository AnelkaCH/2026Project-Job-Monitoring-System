# Streamlit dashboard for the job monitoring system (v3.0).
#
# Reads the enriched seen_jobs.json written by job_monitor.py and renders a
# filterable table of every matched / ambiguous posting: company, title, ATS
# platform, and the date it was first matched.
#
# The path to seen_jobs.json is configurable so the tool stays reusable for
# anyone who forks the repo and runs their own monitor. Resolution order:
#     SEEN_JOBS_PATH env var  overrides  repo default
#     --seen-jobs CLI flag    overrides  SEEN_JOBS_PATH env var
#
# Usage:
#     streamlit run dashboard.py
#     SEEN_JOBS_PATH=/path/to/seen_jobs.json streamlit run dashboard.py
#     streamlit run dashboard.py -- --seen-jobs /path/to/seen_jobs.json

import argparse
import json
import os

try:
    import pandas as pd
    import streamlit as st
    _STREAMLIT_AVAILABLE = True
except ImportError:
    pd = None
    st = None
    _STREAMLIT_AVAILABLE = False

DEFAULT_SEEN_JOBS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seen_jobs.json")


def resolve_seen_jobs_path():
    # Env var first, then CLI flag, then the repo default. argparse's default
    # reads the env var so a flag still wins when both are set.
    parser = argparse.ArgumentParser(description="Job Monitor Dashboard")
    parser.add_argument(
        "--seen-jobs",
        default=os.environ.get("SEEN_JOBS_PATH") or DEFAULT_SEEN_JOBS_FILE,
        help="Path to seen_jobs.json (default: SEEN_JOBS_PATH env var or repo file)",
    )
    args, _ = parser.parse_known_args()
    return args.seen_jobs


def load_seen_jobs(path):
    # Returns the parsed dict, or None if the file does not exist yet.
    # Malformed JSON raises JSONDecodeError for the caller to surface.
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def flatten_seen_jobs(seen_jobs):
    # Flattens seen_jobs.json into one row per posting.
    #
    # Since v3.0 the file stores a "details" map per company (title, link,
    # ats, first_seen) alongside the dedup id lists. Pre-v3.0 files have no
    # details, so the job id stands in for the title and date/ats are blank
    # rather than crashing the view.
    rows = []
    for company, record in seen_jobs.items():
        record = record or {}
        details = record.get("details", {})
        matched = set(record.get("matched_ids", []))
        ambiguous = set(record.get("ambiguous_ids", []))

        all_ids = list(details.keys()) or list(matched) + list(ambiguous)
        for job_id in all_ids:
            detail = details.get(job_id, {})
            if job_id in matched:
                tier = "match"
            elif job_id in ambiguous:
                tier = "ambiguous"
            else:
                tier = "match"
            rows.append({
                "company": company,
                "title": detail.get("title", job_id),
                "location": detail.get("location", ""),
                "link": detail.get("link", ""),
                "ats": detail.get("ats", ""),
                "tier": tier,
                "date_matched": detail.get("first_seen", ""),
                "posted_days_ago": detail.get("posted_days_ago"),
                "id": job_id,
            })
    return rows


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
    path = resolve_seen_jobs_path()

    st.title("Job Monitor Dashboard")
    st.caption(f"Data source: `{path}`")

    try:
        seen_jobs = load_seen_jobs(path)
    except (json.JSONDecodeError, OSError) as exc:
        st.error(f"Could not read {path}: {exc}")
        st.stop()

    if seen_jobs is None:
        st.warning("No seen_jobs.json found yet. Run `python job_monitor.py` at least once to populate it.")
        st.stop()

    rows = flatten_seen_jobs(seen_jobs)
    if not rows:
        st.info("No job records yet. Run `python job_monitor.py` to start tracking postings.")
        st.stop()

    df = build_dataframe(rows)

    with st.sidebar:
        st.header("Filters")
        companies = sorted(df["company"].dropna().unique().tolist())
        tiers = sorted(df["tier"].dropna().unique().tolist())

        selected_companies = st.multiselect("Company", companies)
        selected_tiers = st.multiselect("Tier", tiers)

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
            )

    filtered = apply_filters(df, selected_companies, selected_tiers, date_range)

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Companies tracked", df["company"].nunique())
    col2.metric("Total postings", len(df))
    col3.metric("Matches", int((df["tier"] == "match").sum()))
    col4.metric("Ambiguous", int((df["tier"] == "ambiguous").sum()))

    st.subheader(f"Postings ({len(filtered)})")
    # Streamlit auto-escapes cell text by default, so this table renders
    # untrusted posting text safely without unsafe_allow_html anywhere in
    # this file (storage-time sanitization in utils/schema.py is the first
    # line of defense; this default escaping is intentional second).
    st.dataframe(
        filtered[["company", "title", "date_matched", "location", "posted_days_ago", "link"]],
        width="stretch",
        hide_index=True,
        column_config={
            "link": st.column_config.LinkColumn("Link", display_text="Open posting"),
            "date_matched": st.column_config.DatetimeColumn("Date matched", format="D MMM YYYY"),
            "posted_days_ago": st.column_config.NumberColumn("Age (days)", format="%d"),
        },
    )


if __name__ == "__main__":
    main()
