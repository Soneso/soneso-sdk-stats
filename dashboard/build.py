#!/usr/bin/env python3
"""Build the Soneso Stellar SDK Stats dashboard.

Reads JSON data files from the 4 SDK folders plus the curated evidence
files and generates a single-page dark-themed dashboard at docs/index.html
using Apache ECharts.

Page order encodes the two-pillar strategy: maintenance evidence first,
usage (distribution + curated archive) second, reach/history demoted below,
data sources and definitions at the end.
"""

import html as html_mod
import json
import os
import statistics
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from string import Template
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "index.html"

SDKS = [
    {"key": "ios", "folder": "stellar-ios-mac-sdk", "label": "iOS", "color": "#FF6B4A", "first_release": "1.0.0 (2018-03-02)"},
    {"key": "flutter", "folder": "stellar_flutter_sdk", "label": "Flutter", "color": "#54C5F8", "first_release": "0.7.8 (2020-06-24)"},
    {"key": "php", "folder": "stellar-php-sdk", "label": "PHP", "color": "#6B93D6", "first_release": "0.0.1 (2021-12-30)"},
    {"key": "kmp", "folder": "kmp-stellar-sdk", "label": "KMP", "color": "#B07CFF", "first_release": "v0.2.0 (2025-10-25)"},
]

# Which SDKs to show on the dashboard. Remove a key to hide it.
ENABLED_SDKS = {"ios", "flutter", "php", "kmp"}

_valid_keys = {s["key"] for s in SDKS}
_invalid = ENABLED_SDKS - _valid_keys
if _invalid:
    raise ValueError(f"ENABLED_SDKS contains unknown keys: {_invalid}")

ACTIVE_SDKS = [s for s in SDKS if s["key"] in ENABLED_SDKS]

# Display window for operational time series (clones, views, heatmaps).
# Download histories are deliberately exempt: long history is their value.
CUTOFF_DAYS = 365

NOW = datetime.now(timezone.utc)
TODAY = NOW.date()


def load_json(path):
    """Load a JSON file, returning None if missing or invalid."""
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def cutoff_date():
    # Inclusive-of-today window: 364-day offset yields 365 calendar dates,
    # matching the release/cohort window arithmetic.
    return (TODAY - timedelta(days=CUTOFF_DAYS - 1)).isoformat()


def as_list(v):
    return v if isinstance(v, list) else []


def trim_daily(entries, key="date"):
    """Keep only entries within the rolling window."""
    cut = cutoff_date()
    return [
        e for e in as_list(entries)
        if isinstance(e, dict) and isinstance(e.get(key), str) and e[key] >= cut
    ]


def is_num(v):
    return not isinstance(v, bool) and isinstance(v, (int, float))


def parse_dt(s):
    if not isinstance(s, str) or not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# ── Data extraction ──────────────────────────────────────────────────

def extract_clones(sdk):
    data = load_json(ROOT / sdk["folder"] / "github-clones.json")
    if not isinstance(data, dict):
        return {"collected_at": None, "daily": [], "count_90d": None}
    daily = [
        e for e in as_list(data.get("daily"))
        if isinstance(e, dict) and isinstance(e.get("date"), str) and is_num(e.get("count"))
    ]
    cut_90 = (TODAY - timedelta(days=89)).isoformat()
    count_90d = sum(e["count"] for e in daily if e["date"] >= cut_90) if daily else None
    uniques_14d = None
    summary = data.get("summary")
    if isinstance(summary, dict) and isinstance(summary.get("last_14_days"), dict):
        u = summary["last_14_days"].get("uniques")
        if is_num(u):
            uniques_14d = u
    return {
        "collected_at": data.get("collected_at"),
        "daily": sorted(daily, key=lambda e: e["date"], reverse=True),
        "count_90d": count_90d,
        "uniques_14d": uniques_14d,
    }


def extract_meta(sdk):
    data = load_json(ROOT / sdk["folder"] / "github-meta.json")
    if not isinstance(data, dict):
        return {"repo_daily": [], "repo_collected_at": None}
    repo = data.get("repo")
    if not isinstance(repo, dict):
        repo = {}
    daily = repo.get("daily")
    return {
        "repo_daily": trim_daily(daily if isinstance(daily, list) else []),
        "repo_collected_at": repo.get("collected_at"),
    }


def extract_activity(sdk):
    data = load_json(ROOT / sdk["folder"] / "github-activity.json")
    if not isinstance(data, dict):
        return {"releases_available": False, "weekly_commits": [], "releases_all": [], "commits_collected_at": None, "releases_collected_at": None}
    commits = data.get("commits")
    if not isinstance(commits, dict):
        commits = {}
    releases = data.get("releases")
    if not isinstance(releases, dict):
        releases = {}
    releases_list = releases.get("all")
    raw_releases = as_list(releases_list)
    releases_all = [
        r for r in raw_releases
        if isinstance(r, dict) and parse_dt(r.get("published_at")) is not None
    ]
    # Any dropped record means the true count cannot be established: the
    # dataset reads unavailable, never a measured zero. A successfully
    # collected empty list stays a real zero.
    releases_available = (
        isinstance(releases_list, list) and len(releases_all) == len(raw_releases)
    )
    weekly = commits.get("weekly")
    return {
        "releases_available": releases_available,
        "weekly_commits": [w for w in as_list(weekly) if isinstance(w, dict)],
        "releases_all": releases_all,
        "commits_collected_at": commits.get("collected_at"),
        "releases_collected_at": releases.get("collected_at"),
    }


def compute_release_stats(releases_all):
    """Release cadence from the full stored release list, prereleases
    excluded; the stored lifetime summary is deliberately not used.

    Gap rule: sort stable releases by publication date; a gap qualifies
    when its LATER release falls inside the trailing 365 days, and the
    earlier release of the pair is retained even when it lies outside
    the window.
    """
    stable = sorted(
        (r for r in releases_all if not r.get("prerelease")),
        key=lambda r: r["published_at"],
    )
    stats = {
        "latest_tag": None,
        "latest_date": None,
        "days_since_last": None,
        "count_90d": 0,
        "count_365d": 0,
        "median_gap_days_365d": None,
        "gap_count_365d": 0,
    }
    if not stable:
        return stats
    latest = stable[-1]
    latest_dt = parse_dt(latest["published_at"])
    stats["latest_tag"] = latest.get("tag") or latest.get("name")
    stats["latest_date"] = latest["published_at"][:10]
    if latest_dt:
        stats["days_since_last"] = round((NOW - latest_dt).total_seconds() / 86400, 1)
    cut_90 = (TODAY - timedelta(days=89)).isoformat()
    cut_365 = (TODAY - timedelta(days=364)).isoformat()
    stats["count_90d"] = sum(1 for r in stable if r["published_at"][:10] >= cut_90)
    stats["count_365d"] = sum(1 for r in stable if r["published_at"][:10] >= cut_365)
    gaps = []
    for prev, later in zip(stable, stable[1:]):
        if later["published_at"][:10] >= cut_365:
            a, b = parse_dt(prev["published_at"]), parse_dt(later["published_at"])
            if a and b:
                gaps.append(round((b - a).total_seconds() / 86400, 1))
    if gaps:
        stats["median_gap_days_365d"] = round(statistics.median(gaps), 1)
        stats["gap_count_365d"] = len(gaps)
    return stats


def extract_issues(sdk):
    """Read github-issues.json, tolerating both the old (v1) and new (v2)
    schemas; missing v2 fields render as unavailable, never as zero."""
    data = load_json(ROOT / sdk["folder"] / "github-issues.json")
    if not isinstance(data, dict):
        return {"summary": {}, "open_scan": None, "history": []}
    summary = data.get("summary")
    if not isinstance(summary, dict) or summary.get("definition_version") != 2:
        summary = {}
    open_scan = data.get("open_scan")
    if not (isinstance(open_scan, dict) and is_num(open_scan.get("open_issues"))
            and is_num(open_scan.get("open_prs"))):
        open_scan = None
    history = [e for e in as_list(data.get("history")) if isinstance(e, dict)]
    return {"summary": summary, "open_scan": open_scan, "history": history}


def extract_dependents(sdk):
    """Newest VALID observation only: a failed scrape stores null counts
    (parse_error), which must never render as zero. KMP's genuine 0/0 is
    a real observation and renders as such."""
    if sdk["key"] == "ios":
        return None
    data = load_json(ROOT / sdk["folder"] / "github-dependents.json")
    if not isinstance(data, dict):
        return None
    for entry in as_list(data.get("daily")):
        if (isinstance(entry, dict)
                and is_num(entry.get("dependent_repos"))
                and is_num(entry.get("dependent_packages"))):
            return {
                "total_repos": entry["dependent_repos"],
                "total_packages": entry["dependent_packages"],
                "as_of": entry.get("date"),
            }
    return None


def extract_packagist():
    data = load_json(ROOT / "stellar-php-sdk" / "packagist.json")
    if not isinstance(data, dict):
        return {"latest": {}, "monthly_history": None, "collected_at": None}
    latest = data.get("latest")
    if not isinstance(latest, dict):
        latest = {}
    mh = data.get("monthly_history")
    if isinstance(mh, dict) and isinstance(mh.get("months"), list):
        months = [
            m for m in mh["months"]
            if isinstance(m, dict) and isinstance(m.get("month"), str)
            and is_num(m.get("downloads"))
        ]
        mh = dict(mh, months=months) if months else None
    else:
        mh = None
    return {"latest": latest, "monthly_history": mh, "collected_at": data.get("collected_at")}


def extract_pubdev():
    """pub.dev v3 schema: rolling 7-day buckets with exact bounds. The
    chart uses the latest snapshot's buckets as authoritative and extends
    history only with archived buckets whose end precedes the earliest
    start in that snapshot (inclusive dates: equality is an overlap)."""
    data = load_json(ROOT / "stellar_flutter_sdk" / "pub-dev.json")
    if not isinstance(data, dict):
        return {"latest": {}, "series": [], "daily_30d": [], "collected_at": None, "snapshot_observed_at": None}
    latest = data.get("latest")
    if not isinstance(latest, dict):
        latest = {}
    buckets = data.get("buckets")
    snapshot = data.get("latest_snapshot")
    series = []
    if isinstance(buckets, dict) and isinstance(snapshot, dict):
        keys = [k for k in as_list(snapshot.get("bucket_keys")) if isinstance(k, str)]
        auth = [
            buckets[k] for k in keys
            if isinstance(buckets.get(k), dict) and is_num(buckets[k].get("downloads"))
            and isinstance(buckets[k].get("start"), str) and isinstance(buckets[k].get("end"), str)
        ]
        auth.sort(key=lambda b: b["start"])
        if auth:
            # Older archived buckets extend history only where they do not
            # overlap the authoritative span NOR each other: greedy
            # selection walking backward in time (inclusive dates, so
            # equality is an overlap). Anchor drift produces overlapping
            # rolling buckets that must never render side by side.
            min_start = auth[0]["start"]
            candidates = sorted(
                (
                    b for k, b in buckets.items()
                    if isinstance(b, dict) and is_num(b.get("downloads"))
                    and isinstance(b.get("start"), str) and isinstance(b.get("end"), str)
                    and b["end"] < min_start and k not in set(keys)
                ),
                key=lambda b: b["end"],
                reverse=True,
            )
            older = []
            limit = min_start
            for b in candidates:
                if b["end"] < limit:
                    older.append(b)
                    limit = b["start"]
            older.reverse()
            series = older + auth
    daily_30d = [
        e for e in as_list(data.get("daily"))
        if isinstance(e, dict) and isinstance(e.get("date"), str)
        and is_num(e.get("download_count_30d"))
    ]
    daily_30d.sort(key=lambda e: e["date"])
    return {
        "latest": latest,
        "series": [{"end": b["end"], "downloads": b["downloads"]} for b in series],
        "daily_30d": daily_30d,
        "collected_at": data.get("collected_at"),
        "snapshot_observed_at": snapshot.get("observed_at") if isinstance(snapshot, dict) else None,
    }


def extract_scarf():
    """Load scarf.json defensively: a shape-corrupt file degrades to empty
    Scarf data instead of aborting the whole dashboard build."""
    data = load_json(ROOT / "kmp-stellar-sdk" / "scarf.json")
    if not isinstance(data, dict):
        return {"latest": {}, "daily": []}
    latest = data.get("latest")
    if not (isinstance(latest, dict)
            and is_num(latest.get("downloads_90d"))
            and is_num(latest.get("unique_sources_90d"))):
        latest = {}
    daily = data.get("daily")
    if not isinstance(daily, list):
        daily = []
    daily = [
        e for e in daily
        if isinstance(e, dict) and isinstance(e.get("date"), str)
        and is_num(e.get("downloads"))
    ]
    return {"latest": latest, "daily": daily}


def valid_evidence_url(url):
    if not isinstance(url, str):
        return False
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.netloc)


def valid_verified_date(v):
    if not isinstance(v, str):
        return False
    try:
        date.fromisoformat(v)
    except ValueError:
        return False
    return True


def load_curated(filename, list_key):
    """Load a curated evidence file; only entries with a well-formed
    http(s) evidence url AND a maintainer-set verification DATE are
    rendered. Truthy non-date values do not pass the gate."""
    data = load_json(ROOT / "curated" / filename)
    if not isinstance(data, dict):
        return []
    entries = data.get(list_key)
    if not isinstance(entries, list):
        return []
    return [
        e for e in entries
        if isinstance(e, dict)
        and valid_evidence_url(e.get("url"))
        and valid_verified_date(e.get("verified"))
    ]


# ── Commit heatmap expansion ────────────────────────────────────────

def expand_commits_to_days(weekly_commits):
    """Expand weekly commit data into [date, count] pairs for calendar heatmap."""
    result = []
    cut = cutoff_date()
    for week in weekly_commits:
        week_start = week.get("week_start", "")
        days = week.get("days")
        if not isinstance(week_start, str) or not week_start or not isinstance(days, list):
            continue
        days = [d if is_num(d) else 0 for d in days]
        try:
            base = datetime.strptime(week_start, "%Y-%m-%d")
        except ValueError:
            continue
        for i, count in enumerate(days):
            d = base + timedelta(days=i)
            ds = d.strftime("%Y-%m-%d")
            if ds >= cut:
                result.append([ds, count])
    return result


# ── Build dashboard data ────────────────────────────────────────────

def build_data():
    all_data = []
    for sdk in ACTIVE_SDKS:
        sdk_data = {
            "sdk": sdk,
            "clones": extract_clones(sdk),
            "meta": extract_meta(sdk),
            "activity": extract_activity(sdk),
            "issues": extract_issues(sdk),
            "dependents": extract_dependents(sdk),
        }
        sdk_data["release_stats"] = compute_release_stats(sdk_data["activity"]["releases_all"])
        sdk_data["packagist"] = extract_packagist() if sdk["key"] == "php" else {"latest": {}, "monthly_history": None, "collected_at": None}
        sdk_data["pubdev"] = extract_pubdev() if sdk["key"] == "flutter" else {"latest": {}, "series": [], "daily_30d": [], "collected_at": None, "snapshot_observed_at": None}
        sdk_data["scarf"] = extract_scarf() if sdk["key"] == "kmp" else {"latest": {}, "daily": []}
        all_data.append(sdk_data)

    chart_data = {
        "sdks": [s["label"] for s in ACTIVE_SDKS],
        "colors": [s["color"] for s in ACTIVE_SDKS],
        "commit_heatmaps": {},
        "releases": {},
        "packagist_monthly": [],
        "pubdev_buckets": [],
        "pubdev_daily_30d": [],
        "scarf_daily": [],
        "ios_clones_daily": [],
    }

    for i, sdk in enumerate(ACTIVE_SDKS):
        sd = all_data[i]
        chart_data["commit_heatmaps"][sdk["label"]] = expand_commits_to_days(
            sd["activity"]["weekly_commits"]
        )
        cut = cutoff_date()
        def display_text(v):
            # Rendered-copy rule: no em dashes in anything the page shows,
            # including tooltip text; the stored source data is untouched.
            return str(v if v is not None else "").replace("\u2014", "-")

        chart_data["releases"][sdk["label"]] = [
            {"date": r["published_at"][:10], "tag": display_text(r.get("tag", "")), "name": display_text(r.get("name", ""))}
            for r in sd["activity"]["releases_all"]
            if r.get("published_at", "")[:10] >= cut
        ]

    chart_data["release_order"] = [s["label"] for s in reversed(ACTIVE_SDKS)]

    php_data = next((sd for sd in all_data if sd["sdk"]["key"] == "php"), None)
    if php_data and php_data["packagist"]["monthly_history"]:
        chart_data["packagist_monthly"] = php_data["packagist"]["monthly_history"]["months"]

    flutter_data = next((sd for sd in all_data if sd["sdk"]["key"] == "flutter"), None)
    if flutter_data:
        chart_data["pubdev_buckets"] = flutter_data["pubdev"]["series"]
        chart_data["pubdev_daily_30d"] = [
            {"date": e["date"], "downloads": e["download_count_30d"]}
            for e in flutter_data["pubdev"]["daily_30d"]
        ]

    kmp_data = next((sd for sd in all_data if sd["sdk"]["key"] == "kmp"), None)
    if kmp_data:
        chart_data["scarf_daily"] = [
            {"date": e["date"], "downloads": e["downloads"]}
            for e in kmp_data["scarf"]["daily"]
        ]

    ios_data = next((sd for sd in all_data if sd["sdk"]["key"] == "ios"), None)
    if ios_data:
        chart_data["ios_clones_daily"] = [
            {"date": e["date"], "count": e["count"]}
            for e in sorted(ios_data["clones"]["daily"], key=lambda e: e["date"])
        ]

    return chart_data, all_data


# ── Generate HTML ────────────────────────────────────────────────────

def format_number(n):
    """Format a number with comma separators; non-numeric values render
    n/a so one malformed field can never abort the whole build."""
    if not is_num(n):
        return "n/a"
    return f"{n:,}"


def format_hours(h):
    """Format hours as a human-readable string."""
    if not is_num(h):
        return "n/a"
    if h < 1:
        return f"{int(h * 60)}m"
    if h < 24:
        return f"{h:.1f}h"
    return f"{h / 24:.1f}d"


def format_days(d):
    if not is_num(d):
        return "n/a"
    if d == int(d):
        return f"{int(d)}"
    return f"{d}"


def esc(v):
    """Escape a value for HTML text/attribute contexts."""
    return html_mod.escape(str(v), quote=True)


def stat_row(label, value, muted=False):
    style = ' style="color:#8b949e"' if muted else ""
    return (f'<div class="sdk-stat"><span class="sdk-stat-label">{label}</span>'
            f'<span class="sdk-stat-value"{style}>{value}</span></div>')


def build_maintenance_cards(all_data):
    """Per-SDK maintenance evidence. Small cohorts show A/N counts, never
    percentages; zero open items reads as a real zero with age not
    applicable; missing collection reads n/a, never zero."""
    cards = []
    for sd in all_data:
        sdk = sd["sdk"]
        rs = sd["release_stats"]
        summary = sd["issues"]["summary"]
        open_scan = sd["issues"]["open_scan"]
        rows = []

        if not sd["activity"]["releases_available"]:
            rows.append(stat_row("Release cadence", "n/a (no release data collected)", muted=True))
        else:
            gap = rs["median_gap_days_365d"]
            if gap is not None:
                rows.append(stat_row("Median release gap (365d)", f"{format_days(gap)} days"))
            else:
                rows.append(stat_row("Median release gap (365d)", "insufficient release history", muted=True))
            rows.append(stat_row("Days since last stable release", format_days(rs["days_since_last"])))
            rows.append(stat_row("Stable releases (90d / 365d)", f"{rs['count_90d']} / {rs['count_365d']}"))

        if open_scan:
            def age_value(count, age):
                # A missing or malformed age beside a nonzero count is a
                # data gap (n/a); "not applicable" is reserved for a real
                # zero backlog.
                if is_num(age):
                    return f"{format_days(age)} days", False
                return ("not applicable", True) if count == 0 else ("n/a", True)

            rows.append(stat_row("Open issues", format_number(open_scan["open_issues"])))
            v, m = age_value(open_scan["open_issues"], open_scan.get("issue_median_age_days"))
            rows.append(stat_row("Open issue median age", v, muted=m))
            rows.append(stat_row("Open PRs", format_number(open_scan["open_prs"])))
            v, m = age_value(open_scan["open_prs"], open_scan.get("pr_median_age_days"))
            rows.append(stat_row("Open PR median age", v, muted=m))
        else:
            rows.append(stat_row("Open issues / PRs", "n/a (no completed scan)", muted=True))

        if summary and summary.get("coverage", "complete") != "complete":
            rows.append(stat_row("Community cohort (365d)", "n/a (incomplete collection)", muted=True))
        elif summary:
            def cohort_rows(kind_label, created, closed, median):
                if not is_num(created) or not is_num(closed):
                    rows.append(stat_row(f"Community {kind_label} (365d)", "n/a", muted=True))
                elif created == 0:
                    rows.append(stat_row(f"Community {kind_label} (365d)", "no eligible items", muted=True))
                    rows.append(stat_row(f"Median time to close ({kind_label})", "not applicable", muted=True))
                else:
                    rows.append(stat_row(f"Community {kind_label} (365d)", f"{created} opened, {closed} closed"))
                    rows.append(stat_row(f"Median time to close ({kind_label})", format_hours(median)))
            cohort_rows("issues",
                        summary.get("community_issues_created_365d"),
                        summary.get("community_issues_closed_365d"),
                        summary.get("community_issue_median_close_hours"))
            cohort_rows("PRs",
                        summary.get("community_prs_created_365d"),
                        summary.get("community_prs_closed_365d"),
                        summary.get("community_pr_median_close_hours"))
            m_created = summary.get("maintainer_prs_created_365d")
            m_closed = summary.get("maintainer_prs_closed_365d")
            if is_num(m_created) and is_num(m_closed):
                rows.append(stat_row("Maintainer PRs (365d)", f"{m_created} opened, {m_closed} closed"))
        else:
            rows.append(stat_row("Community cohort (365d)", "n/a", muted=True))

        rows_html = "\n    ".join(rows)
        cards.append(f'''<div class="sdk-card">
    <h3 style="color:{sdk["color"]}">{sdk["label"]}</h3>
    {rows_html}
  </div>''')
    return "\n  ".join(cards)


def usage_card(title, headline, chart_id):
    return (
        '<div class="card">\n'
        f'    <h2>{title}</h2>\n'
        f'    <div class="usage-headline">{headline}</div>\n'
        f'    <div id="{chart_id}" class="chart"></div>\n'
        '  </div>'
    )


def build_usage_cards(all_data):
    """The 2x2 usage-by-distribution-channel grid. The iOS card shows git
    clone traffic (the SPM/CocoaPods install path) and never uses the
    word downloads for it."""
    by_key = {sd["sdk"]["key"]: sd for sd in all_data}
    cards = []
    if "flutter" in ENABLED_SDKS:
        latest = by_key["flutter"]["pubdev"]["latest"]
        headline = (
            f'Downloads (last 30 days): {format_number(latest.get("download_count_30d"))}'
            f' &middot; downloads (last 52 weeks): {format_number(latest.get("download_count_52w"))}'
        )
        card = (
            '<div class="card">\n'
            '    <h2>pub.dev 7-Day Totals (Flutter)</h2>\n'
            f'    <div class="usage-headline">{headline}</div>\n'
            '    <div id="chart-pubdev" class="chart"></div>\n'
            '    <div class="usage-headline">Rolling 30-day downloads as reported daily by pub.dev</div>\n'
            '    <div id="chart-pubdev-30d" class="chart-small"></div>\n'
            '  </div>'
        )
        cards.append(card)
    if "php" in ENABLED_SDKS:
        pk = by_key["php"]["packagist"]
        lifetime = pk["latest"].get("total")
        mh = pk["monthly_history"]
        last_full = None
        if mh and len(mh["months"]) >= 2:
            last_full = mh["months"][-2]
        headline = (
            f'Downloads (lifetime): {format_number(lifetime)}'
            + (f' &middot; last full month ({last_full["month"]}): {format_number(last_full["downloads"])}' if last_full else "")
        )
        cards.append(usage_card("Packagist Monthly Downloads (PHP)", headline, "chart-packagist"))
    if "kmp" in ENABLED_SDKS:
        latest = by_key["kmp"]["scarf"]["latest"]
        headline = (
            f'Downloads (last 90 days): {format_number(latest.get("downloads_90d"))}'
            f' &middot; unique sources (last 90 days): {format_number(latest.get("unique_sources_90d"))}'
        )
        cards.append(usage_card("Maven Central Daily Downloads (KMP, via Scarf)", headline, "chart-scarf"))
    if "ios" in ENABLED_SDKS:
        cl = by_key["ios"]["clones"]
        headline = (
            f'Clones (last 90 days): {format_number(cl.get("count_90d"))}'
            f' &middot; unique cloners (last 14 days): {format_number(cl.get("uniques_14d"))}'
        )
        cards.append(usage_card("Git Clones (iOS, SPM/CocoaPods install path)", headline, "chart-ios-clones"))
    if len(cards) > 1:
        inner = "\n  ".join(cards)
        return f'<div class="two-col">\n  {inner}\n</div>'
    if cards:
        return cards[0]
    return ""


def build_usage_archive():
    """Curated usage evidence: verified production users and linked user
    statements. Entries render only after maintainer verification."""
    label_by_key = {s["key"]: s["label"] for s in SDKS}
    users = [u for u in load_curated("verified-users.json", "users") if u.get("sdk") in ENABLED_SDKS]
    statements = [s for s in load_curated("user-statements.json", "statements") if s.get("sdk") in ENABLED_SDKS]

    if users:
        def evidence_cell(u):
            links = f'<a href="{esc(u["url"])}">evidence</a>'
            if valid_evidence_url(u.get("affiliation_url")):
                links += f', <a href="{esc(u["affiliation_url"])}">affiliation</a>'
            return links

        user_rows = "\n      ".join(
            f'<tr><td>{esc(label_by_key.get(u["sdk"], u["sdk"]))}</td><td>{esc(u.get("name", ""))}</td>'
            f'<td>{esc(u.get("evidence_kind", ""))}</td>'
            f'<td>{evidence_cell(u)}</td><td>{esc(u.get("verified", ""))}</td></tr>'
            for u in users
        )
        users_html = f'''<table class="evidence-table">
      <tr><th>SDK</th><th>Project</th><th>Evidence kind</th><th>Evidence</th><th>Verified</th></tr>
      {user_rows}
    </table>'''
    else:
        users_html = '<div class="empty-state">Entries appear after maintainer verification.</div>'

    return f'''<div class="card">
    <h2>Production Users (examples with public evidence)</h2>
    {users_html}
  </div>'''


def build_community_feedback():
    """Compact feedback section: one line per SDK linking to the public
    PG Award proposal thread that holds the community comments, with the
    verified comment count. The full quotes stay in the curated JSON for
    submission use; they are deliberately not rendered here."""
    label_by_key = {s["key"]: s["label"] for s in SDKS}
    color_by_key = {s["key"]: s["color"] for s in SDKS}
    statements = [s for s in load_curated("user-statements.json", "statements") if s.get("sdk") in ENABLED_SDKS]
    by_sdk = {}
    for st in statements:
        by_sdk.setdefault(st["sdk"], []).append(st)
    lines = []
    for sdk in ACTIVE_SDKS:
        entries = by_sdk.get(sdk["key"])
        if not entries:
            continue
        thread_url = entries[0]["url"].split("#")[0]
        n = len(entries)
        lines.append(
            f'<div class="sdk-stat"><span class="sdk-stat-label" style="color:{color_by_key[sdk["key"]]}">'
            f'{esc(label_by_key[sdk["key"]])}</span><span class="sdk-stat-value">'
            f'{n} public comments &middot; <a href="{esc(thread_url)}">Q3 2026 proposal thread</a></span></div>'
        )
    if not lines:
        body = '<div class="empty-state">Entries appear after maintainer verification.</div>'
    else:
        body = "\n  ".join(lines)
    return f'''<div class="card">
  <h2>Community Feedback (PG Award proposal threads)</h2>
  {body}
</div>'''


def build_reach_cards(all_data):
    """Reach and history context, deliberately below the evidence sections."""
    cards = []
    for sd in all_data:
        sdk = sd["sdk"]
        repo_daily = sd["meta"]["repo_daily"]
        stars = repo_daily[0].get("stars") if repo_daily else None
        forks = repo_daily[0].get("forks") if repo_daily else None
        releases_total = (
            len(sd["activity"]["releases_all"])
            if sd["activity"]["releases_available"] else None
        )
        rs = sd["release_stats"]
        dep = sd["dependents"]

        rows = [
            stat_row("Stars", format_number(stars)),
            stat_row("Forks", format_number(forks)),
            stat_row("Releases (all time)", format_number(releases_total)),
        ]
        if rs["latest_tag"]:
            rows.append(stat_row("Latest stable release", esc(f"{rs['latest_tag']} ({rs['latest_date']})")))
        rows.append(stat_row("First release", sdk["first_release"]))
        if dep is not None:
            total_dep = (dep.get("total_repos") or 0) + (dep.get("total_packages") or 0)
            rows.append(stat_row("Dependents (GitHub graph count)", format_number(total_dep)))
        elif sdk["key"] == "ios":
            rows.append(stat_row("Dependents (GitHub graph count)", "not tracked for SPM", muted=True))

        rows_html = "\n    ".join(rows)
        cards.append(f'''<div class="sdk-card">
    <h3 style="color:{sdk["color"]}">{sdk["label"]}</h3>
    {rows_html}
  </div>''')
    return "\n  ".join(cards)


def build_freshness_section(all_data):
    """Per-source last-successful-collection timestamps. A source without
    a stored timestamp (not yet migrated) shows n/a rather than a guess:
    collection time is never inferred from data recency."""
    stale_before = NOW - timedelta(hours=48)

    def short_ts(ts):
        # Sources collect daily; a timestamp older than 48 hours means
        # collection has been failing and the shown values are retained,
        # not fresh. The stale label is the explicit signal item 7 requires.
        dt = parse_dt(ts)
        if dt is None:
            return "n/a"
        label = ts[:16].replace("T", " ")
        if dt < stale_before:
            label += " (STALE)"
        return label

    rows = []
    for sd in all_data:
        sdk = sd["sdk"]
        sources = [
            ("Clone traffic", sd["clones"]["collected_at"]),
            ("Repo metadata", sd["meta"]["repo_collected_at"]),
            ("Commit activity", sd["activity"]["commits_collected_at"]),
            ("Releases", sd["activity"]["releases_collected_at"]),
            ("Issues/PRs (windowed)", sd["issues"]["summary"].get("collected_at")),
            ("Open backlog scan", (sd["issues"]["open_scan"] or {}).get("observed_at")),
        ]
        if sdk["key"] == "flutter":
            sources.append(("pub.dev", sd["pubdev"]["collected_at"]))
            sources.append(("pub.dev bucket snapshot", sd["pubdev"].get("snapshot_observed_at")))
        if sdk["key"] == "php":
            sources.append(("Packagist", sd["packagist"]["collected_at"]))
            mh = sd["packagist"]["monthly_history"]
            sources.append(("Packagist monthly history", mh.get("collected_at") if mh else None))
        if sdk["key"] == "kmp":
            sources.append(("Scarf (Maven Central)", sd["scarf"]["latest"].get("as_of")))
        if sd["dependents"] is not None:
            sources.append(("Dependents scrape", sd["dependents"].get("as_of")))
        rows.append(
            f'<tr><th style="color:{sdk["color"]}">{sdk["label"]}</th>'
            + "".join(f"<td>{name}<br><span class=\"muted\">{short_ts(ts)}</span></td>" for name, ts in sources)
            + "</tr>"
        )
    table = "\n    ".join(rows)
    return f'''<div class="card">
  <h2>Data Sources and Freshness</h2>
  <div style="overflow-x:auto">
    <table class="evidence-table">
    {table}
    </table>
  </div>
  <div class="definitions">
    <h3>Definitions</h3>
    <ul>
      <li>All times are UTC. Each source shows its own last successful collection time; a green build never implies every source is fresh.</li>
      <li>Release cadence: median gap in days between stable (non-prerelease) GitHub releases, over gaps whose later release falls in the trailing 365 days. GitHub publication is the shipping proxy; registry artifacts may lag briefly.</li>
      <li>Open issues and PRs: complete scan of all open items at the stated observation time (no window). Zero shows a real zero; median age at zero is not applicable.</li>
      <li>Community cohort: issues and PRs created in the trailing 365 days, excluding self-filed (owner/member) and bot-authored items. Counts are shown as opened and closed; percentages are avoided for small cohorts.</li>
      <li>Median time to close covers closed community-cohort items, from creation to closure.</li>
      <li>Maintainer PRs count owner/member-authored pull requests created in the same trailing 365 days, as workload context; bot-authored PRs (dependabot and similar) belong to neither cohort and are not shown.</li>
      <li>Downloads: pub.dev values are pub.dev-reported rolling 7-day totals as observed at collection (not calendar weeks); Packagist values are calendar-month sums of Packagist's daily download counts (current month partial); KMP values are Maven Central artifact downloads reported via Scarf over the stated windows (about one week of ingest lag); iOS shows git clone traffic, the retrieval path SPM and CocoaPods installs use, with the same CI/bot noise as any registry download count.</li>
      <li>Production users and community feedback are curated examples with public evidence links, verified on the stated date; never a census. Community feedback links to the public PG Award proposal threads where users and community members posted their comments; the counts cover maintainer-verified comments. The dependents number is GitHub's dependents-graph count.</li>
    </ul>
  </div>
</div>'''


def build_heatmap_divs():
    divs = []
    for i, sdk in enumerate(ACTIVE_SDKS):
        divs.append(f'<div id="chart-heatmap-{i}" class="chart-heatmap"></div>')
    return "\n  ".join(divs)


def build_heatmap_legend():
    items = []
    for sdk in ACTIVE_SDKS:
        items.append(f'<span class="heatmap-legend-item"><span class="heatmap-legend-dot" style="background:{sdk["color"]}"></span>{sdk["label"]}</span>')
    return "".join(items)


def build_subtitle():
    labels = [s["label"] for s in ACTIVE_SDKS]
    if not labels:
        return "Soneso Stellar SDK Stats"
    if len(labels) == 1:
        return f"Daily statistics for the {labels[0]} Stellar SDK"
    subtitle_text = ", ".join(labels[:-1]) + " and " + labels[-1]
    return f"Daily statistics for {subtitle_text} Stellar SDKs"


# ── HTML Template ────────────────────────────────────────────────────

HTML_TEMPLATE = Template(r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Soneso Stellar SDK Stats</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@6.0.0/dist/echarts.min.js"
        integrity="sha384-F07Cpw5v8spSU0H113F33m2NQQ/o6GqPTnTjf45ssG4Q6q58ZwhxBiQtIaqvnSpR"
        crossorigin="anonymous"></script>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0d1117;color:#e6edf3;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;padding:24px;max-width:1400px;margin:0 auto}
h1{font-size:1.6rem;font-weight:600}
h2{font-size:1.15rem;font-weight:600;color:#e6edf3;margin-bottom:12px}
h3{font-size:0.95rem}
.subtitle{color:#8b949e;font-size:0.85rem;margin-top:4px}
.header{display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;margin-bottom:24px;padding-bottom:16px;border-bottom:1px solid #30363d}
.section-title{margin:24px 0 12px 0}
.sdk-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px;margin-bottom:24px}
.sdk-card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px}
.sdk-card h3{font-size:1.05rem;font-weight:600;margin-bottom:10px}
.sdk-stat{display:flex;justify-content:space-between;padding:3px 0;font-size:0.85rem;gap:8px}
.sdk-stat-label{color:#8b949e}
.sdk-stat-value{font-weight:600;text-align:right}
.card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px;margin-bottom:16px}
.chart{width:100%;height:350px}
.chart-small{width:100%;height:180px}
.chart-tall{width:100%;height:420px}
.chart-heatmap{width:100%;height:150px}
.two-col{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.footer{text-align:center;color:#8b949e;font-size:0.8rem;margin-top:32px;padding-top:16px;border-top:1px solid #30363d}
.footer a{color:#6B93D6;text-decoration:none}
.heatmap-legend{display:flex;gap:16px;align-items:center;font-size:0.8rem;color:#8b949e}
.heatmap-legend-item{display:flex;align-items:center;gap:5px}
.heatmap-legend-dot{width:12px;height:12px;border-radius:3px;display:inline-block}
.empty-state{color:#8b949e;font-size:0.85rem;text-align:center;padding:40px 0}
.usage-headline{color:#8b949e;font-size:0.85rem;margin:-6px 0 8px 0}
.evidence-table{width:100%;border-collapse:collapse;font-size:0.82rem}
.evidence-table th{color:#8b949e;text-align:left;padding:6px 8px;border-bottom:1px solid #30363d;font-weight:600}
.evidence-table td{padding:6px 8px;border-bottom:1px solid #21262d;vertical-align:top}
.evidence-table a{color:#6B93D6;text-decoration:none}
.muted{color:#8b949e}
.sdk-stat-value a{color:#6B93D6;text-decoration:none}
.definitions{margin-top:16px;font-size:0.82rem;color:#8b949e}
.definitions h3{color:#e6edf3;margin-bottom:8px}
.definitions ul{margin-left:18px}
.definitions li{margin-bottom:6px}
@media(max-width:768px){
  .two-col{grid-template-columns:1fr}
  body{padding:12px}
}
</style>
</head>
<body>
<div class="header">
  <div>
    <h1>Soneso Stellar SDK Stats</h1>
    <div class="subtitle">$subtitle</div>
  </div>
  <div class="subtitle">Page built: $last_updated (UTC). Per-source collection times and definitions are listed at the end of the page.</div>
</div>

<!-- Maintenance -->
<h2 class="section-title">Maintenance</h2>
<div class="sdk-grid">
  $maintenance_cards
</div>

<!-- Usage by distribution channel -->
<h2 class="section-title">Usage by Distribution Channel</h2>
$usage_cards

<!-- Release Timeline -->
<div class="card">
  <h2>Release Timeline (365d)</h2>
  <div id="chart-releases" class="chart-tall"></div>
</div>

<!-- Reach & history -->
<h2 class="section-title">Reach and History</h2>
<div class="sdk-grid">
  $reach_cards
</div>

<!-- Commit Heatmaps -->
<div class="card">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
    <h2 style="margin-bottom:0">Commit Activity</h2>
    <div class="heatmap-legend">$heatmap_legend</div>
  </div>
  <div style="height:18px"></div>
  $heatmap_divs
</div>

<!-- Usage archive -->
<h2 class="section-title">Who Uses These SDKs</h2>
$usage_archive

<!-- Community feedback -->
$community_feedback

<!-- Data sources, freshness, definitions -->
$freshness_section

<!-- Footer -->
<div class="footer">
  <a href="https://github.com/Soneso/soneso-sdk-stats">Soneso/soneso-sdk-stats</a> &middot; Data collected daily via GitHub Actions
</div>

<script>
const DATA = $chart_data_json;
const COLORS = DATA.colors;
const SDK_NAMES = DATA.sdks;

function initChart(id, option) {
  var el = document.getElementById(id);
  if (!el) return null;
  var c = echarts.init(el, 'dark');
  c.setOption(option);
  return c;
}

function shortDate(d) { return d.length >= 10 ? d.slice(2) : d; }
function escapeHtml(v) {
  return String(v).replace(/[&<>"']/g, function(c) {
    return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
  });
}
var allCharts = [];

// ── pub.dev 7-day buckets ──
(function() {
  var buckets = DATA.pubdev_buckets || [];
  allCharts.push(initChart('chart-pubdev', {
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis' },
    legend: { data: ['Flutter'], right: 0, top: -5, textStyle: { color: '#8b949e' } },
    grid: { left: 50, right: 20, bottom: 60, top: 20 },
    xAxis: {
      type: 'category',
      data: buckets.map(function(e) { return e.end; }),
      axisLabel: { color: '#8b949e', rotate: 45, interval: Math.max(1, Math.floor(buckets.length / 12)), formatter: shortDate }
    },
    yAxis: { type: 'value', axisLabel: { color: '#8b949e' }, splitLine: { lineStyle: { color: '#21262d' } } },
    series: [{
      name: 'Flutter',
      type: 'bar',
      data: buckets.map(function(e) { return e.downloads; }),
      itemStyle: { color: '#54C5F8' }
    }]
  }));
})();

// ── pub.dev rolling 30-day trend ──
(function() {
  var daily = DATA.pubdev_daily_30d || [];
  allCharts.push(initChart('chart-pubdev-30d', {
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis' },
    grid: { left: 50, right: 20, bottom: 40, top: 10 },
    xAxis: {
      type: 'category',
      data: daily.map(function(e) { return e.date; }),
      axisLabel: { color: '#8b949e', rotate: 45, interval: Math.max(1, Math.floor(daily.length / 10)), formatter: shortDate }
    },
    yAxis: { type: 'value', axisLabel: { color: '#8b949e' }, splitLine: { lineStyle: { color: '#21262d' } } },
    series: [{
      name: 'Flutter',
      type: 'line',
      data: daily.map(function(e) { return e.downloads; }),
      lineStyle: { color: '#54C5F8' },
      itemStyle: { color: '#54C5F8' },
      smooth: true,
      symbol: 'none'
    }]
  }));
})();

// ── Packagist monthly totals ──
(function() {
  var months = DATA.packagist_monthly || [];
  allCharts.push(initChart('chart-packagist', {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      formatter: function(params) {
        var p = params[0];
        var m = months[p.dataIndex];
        p = { name: escapeHtml(p.name), value: escapeHtml(p.value), dataIndex: p.dataIndex };
        var note = '';
        if (m && m.month && m.last_day) {
          var y = +m.month.slice(0, 4), mo = +m.month.slice(5, 7);
          var lastDom = new Date(Date.UTC(y, mo, 0)).getUTCDate();
          if (+m.last_day.slice(8) !== lastDom) note = ' (partial: through ' + m.last_day + ')';
        }
        return p.name + ': ' + p.value + ' downloads' + note;
      }
    },
    legend: { data: ['PHP'], right: 0, top: -5, textStyle: { color: '#8b949e' } },
    grid: { left: 50, right: 20, bottom: 60, top: 20 },
    xAxis: {
      type: 'category',
      data: months.map(function(e) { return e.month; }),
      axisLabel: { color: '#8b949e', rotate: 45, interval: Math.max(1, Math.floor(months.length / 14)) }
    },
    yAxis: { type: 'value', axisLabel: { color: '#8b949e' }, splitLine: { lineStyle: { color: '#21262d' } } },
    series: [{
      name: 'PHP',
      type: 'bar',
      data: months.map(function(e) { return e.downloads; }),
      itemStyle: { color: '#6B93D6' }
    }]
  }));
})();

// ── Scarf Daily (Maven Central) ──
(function() {
  var daily = (DATA.scarf_daily || []).slice().reverse();
  allCharts.push(initChart('chart-scarf', {
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis' },
    legend: { data: ['KMP'], right: 0, top: -5, textStyle: { color: '#8b949e' } },
    grid: { left: 50, right: 20, bottom: 60, top: 20 },
    xAxis: {
      type: 'category',
      data: daily.map(function(e) { return e.date; }),
      axisLabel: { color: '#8b949e', rotate: 45, interval: Math.max(1, Math.floor(daily.length / 12)), formatter: shortDate }
    },
    yAxis: { type: 'value', axisLabel: { color: '#8b949e' }, splitLine: { lineStyle: { color: '#21262d' } } },
    series: [{
      name: 'KMP',
      type: 'line',
      data: daily.map(function(e) { return e.downloads; }),
      lineStyle: { color: '#B07CFF' },
      itemStyle: { color: '#B07CFF' },
      areaStyle: { color: 'rgba(176,124,255,0.15)' },
      smooth: true,
      symbol: 'none'
    }]
  }));
})();

// ── iOS git clones ──
(function() {
  var daily = DATA.ios_clones_daily || [];
  allCharts.push(initChart('chart-ios-clones', {
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis' },
    legend: { data: ['iOS'], right: 0, top: -5, textStyle: { color: '#8b949e' } },
    grid: { left: 50, right: 20, bottom: 60, top: 20 },
    xAxis: {
      type: 'category',
      data: daily.map(function(e) { return e.date; }),
      axisLabel: { color: '#8b949e', rotate: 45, interval: Math.max(1, Math.floor(daily.length / 12)), formatter: shortDate }
    },
    yAxis: { type: 'value', axisLabel: { color: '#8b949e' }, splitLine: { lineStyle: { color: '#21262d' } } },
    series: [{
      name: 'iOS',
      type: 'line',
      data: daily.map(function(e) { return e.count; }),
      lineStyle: { color: '#FF6B4A' },
      itemStyle: { color: '#FF6B4A' },
      areaStyle: { color: 'rgba(255,107,74,0.15)' },
      smooth: true,
      symbol: 'none'
    }]
  }));
})();

// ── Commit Heatmaps ──
(function() {
  SDK_NAMES.forEach(function(name, i) {
    var elId = 'chart-heatmap-' + i;
    var heatData = DATA.commit_heatmaps[name] || [];
    if (heatData.length === 0) {
      var el = document.getElementById(elId);
      if (el) el.innerHTML = '<div class="empty-state">No commit data</div>';
      return;
    }

    // Find date range
    var allDates = heatData.map(function(d) { return d[0]; }).sort();
    var rangeStart = allDates[0];
    var rangeEnd = allDates[allDates.length - 1];

    // Max value for color scale
    var maxVal = Math.max.apply(null, heatData.map(function(d) { return d[1]; }).concat([1]));

    allCharts.push(initChart(elId, {
      backgroundColor: 'transparent',
      tooltip: {
        formatter: function(p) { return p.value[0] + ': ' + p.value[1] + ' commits'; }
      },
      visualMap: {
        min: 0, max: maxVal, show: false,
        inRange: { color: ['#161b22', COLORS[i]] }
      },
      calendar: {
        range: [rangeStart, rangeEnd],
        cellSize: [13, 13],
        top: 20,
        left: 60,
        right: 20,
        orient: 'horizontal',
        itemStyle: { borderWidth: 2, borderColor: '#0d1117' },
        splitLine: { show: false },
        dayLabel: { color: '#8b949e', fontSize: 10 },
        monthLabel: { color: '#8b949e', fontSize: 10 },
        yearLabel: { show: false }
      },
      series: [{
        name: name,
        type: 'heatmap',
        coordinateSystem: 'calendar',
        data: heatData,
        itemStyle: { color: COLORS[i] }
      }]
    }));
  });
})();

// ── Release Timeline ──
(function() {
  var series = [];
  var releaseOrder = DATA.release_order;
  releaseOrder.forEach(function(name) {
    var i = SDK_NAMES.indexOf(name);
    var releases = DATA.releases[name] || [];
    if (releases.length === 0) return;
    series.push({
      name: name,
      type: 'scatter',
      data: releases.map(function(r) {
        return { value: [r.date, name], tag: r.tag, releaseName: r.name };
      }),
      symbolSize: 12,
      itemStyle: { color: COLORS[i] },
      tooltip: {
        formatter: function(p) {
          return '<b>' + escapeHtml(name) + '</b><br/>' + escapeHtml(p.data.tag) + '<br/>' + escapeHtml(p.data.releaseName) + '<br/>' + escapeHtml(p.value[0]);
        }
      }
    });
  });

  allCharts.push(initChart('chart-releases', {
    backgroundColor: 'transparent',
    tooltip: { trigger: 'item' },
    legend: { show: false },
    grid: { left: 80, right: 20, bottom: 40, top: 20 },
    xAxis: {
      type: 'time',
      axisLabel: { color: '#8b949e' },
      splitLine: { lineStyle: { color: '#21262d' } }
    },
    yAxis: {
      type: 'category',
      data: releaseOrder,
      axisLabel: { color: '#8b949e' },
      splitLine: { show: false }
    },
    series: series
  }));
})();

// ── Resize handler ──
window.addEventListener('resize', function() {
  allCharts.forEach(function(c) { if (c) c.resize(); });
});
</script>
</body>
</html>""")


def generate():
    chart_data, all_data = build_data()

    today = NOW.strftime("%Y-%m-%d")

    html = HTML_TEMPLATE.substitute(
        last_updated=today,
        subtitle=build_subtitle(),
        maintenance_cards=build_maintenance_cards(all_data),
        usage_cards=build_usage_cards(all_data),
        usage_archive=build_usage_archive(),
        community_feedback=build_community_feedback(),
        reach_cards=build_reach_cards(all_data),
        heatmap_divs=build_heatmap_divs(),
        heatmap_legend=build_heatmap_legend(),
        freshness_section=build_freshness_section(all_data),
        chart_data_json=json.dumps(chart_data, separators=(",", ":")).replace("</", "<\\/"),
    )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    with open(tmp, "w") as f:
        f.write(html)
    os.replace(tmp, OUT)
    print(f"Dashboard written to {OUT}")


if __name__ == "__main__":
    generate()
