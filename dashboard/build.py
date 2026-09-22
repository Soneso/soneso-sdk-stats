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
import math
import os
import statistics
import subprocess
import tempfile
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

DEFINITION_VERSION = "dashboard-v1-response-v3"
REPOSITORY_URL = "https://github.com/Soneso/soneso-sdk-stats"
DASHBOARD_URL = "https://soneso.github.io/soneso-sdk-stats/"
PROFILE_PROPOSAL_URL = "https://github.com/SCF-Public-Goods-Maintenance/pg-atlas-backend/issues/80"


class Signals(dict):
    """Capture raw headlines at their rendering site, after the page's gates.

    Snapshots and profiles consume these records, never recalculate metrics
    from the source JSON. Observation time is independent of generation time.
    """

    @staticmethod
    def clean(value):
        """JSON-safe copy of a structured value, or (None, False) on any
        non-finite number, non-string key, or unsupported type. Exported
        records must serialize under allow_nan=False."""
        if value is None or isinstance(value, str):
            return value, True
        if isinstance(value, bool):
            return None, False
        if isinstance(value, int):
            return value, True
        if isinstance(value, float):
            return (value, True) if math.isfinite(value) else (None, False)
        if isinstance(value, dict):
            out = {}
            for k, v in value.items():
                cv, ok = Signals.clean(v)
                if not isinstance(k, str) or not ok:
                    return None, False
                out[k] = cv
            return out, True
        if isinstance(value, (list, tuple)):
            out = []
            for v in value:
                cv, ok = Signals.clean(v)
                if not ok:
                    return None, False
                out.append(cv)
            return out, True
        return None, False

    def add(self, key, value, window, observed_at=None, *, unit="count",
            sample_size=None, evidence_urls=(), reason=None, coverage=None,
            numeric=True, window_end=None):
        if numeric:
            if not is_num(value) or (isinstance(value, float) and not math.isfinite(value)):
                value = None
        else:
            value, _ = self.clean(value)
        if not (isinstance(sample_size, int) and not isinstance(sample_size, bool)
                and sample_size >= 0):
            sample_size = None
        if not isinstance(window, str):
            window = "unknown"
        if not isinstance(window_end, str):
            window_end = None
        if value is None:
            reason = reason or "Missing, malformed, or incomplete source data."
        observed = parse_dt(observed_at)
        self[key] = {
            "value": value, "unit": unit, "sample_size": sample_size,
            "window": window, "window_end": window_end,
            "coverage": coverage or ("complete" if value is not None else "incomplete"),
            "reason": reason, "observed_at": observed_at if observed else None,
            "freshness": ("not_applicable" if window in {"curated_snapshot", "mainnet_activation", "Q3 2026 proposal thread"} else
                          "unknown" if observed is None else
                          "stale" if observed < NOW - timedelta(hours=48) else "fresh"),
            "evidence_urls": list(dict.fromkeys(u for u in evidence_urls if valid_evidence_url(u))),
            "percentile": None, "percentile_reason": "No comparison pool is maintained.",
            "pool_size": None, "pool_size_reason": "No comparison pool is maintained.",
        }


def build_provenance():
    """Record the source checkout; never invent a commit for exported data."""
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parent.parent, text=True).strip()
    dirty = bool(subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=normal"],
        cwd=Path(__file__).resolve().parent.parent, text=True).strip())
    committed_page = subprocess.check_output(
        ["git", "show", f"{commit}:docs/index.html"],
        cwd=Path(__file__).resolve().parent.parent, text=True)
    definition_line = next((i for i, line in enumerate(committed_page.splitlines(), 1)
                            if ">Definitions</h3>" in line), 1)
    return {
        "definition_version": DEFINITION_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "as_of": NOW.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "build_commit": commit, "working_tree_dirty": dirty,
        "dashboard_url": DASHBOARD_URL + "#definitions",
        "definitions_at_commit": REPOSITORY_URL + f"/blob/{commit}/docs/index.html#L{definition_line}",
        "proposal_url": PROFILE_PROPOSAL_URL,
    }


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
    """Read closure definitions v2+; response claims require v3 evidence."""
    data = load_json(ROOT / sdk["folder"] / "github-issues.json")
    if not isinstance(data, dict):
        return {"summary": {}, "open_scan": None, "history": [], "response_cohort": [], "response_evidence_available": False}
    summary = data.get("summary")
    if (not isinstance(summary, dict) or not is_num(summary.get("definition_version"))
            or summary["definition_version"] < 2):
        summary = {}
    open_scan = data.get("open_scan")
    if not (isinstance(open_scan, dict) and is_num(open_scan.get("open_issues"))
            and is_num(open_scan.get("open_prs"))):
        open_scan = None
    history = [e for e in as_list(data.get("history")) if isinstance(e, dict)]
    observed = parse_dt(summary.get("collected_at"))
    raw_issues = data.get("issues")

    def evidence_item_ok(i):
        # The cohort filter tests set membership on these fields, so a
        # non-hashable value in a stored record must make the evidence
        # section unavailable, never abort the whole build.
        return (isinstance(i, dict)
                and isinstance(i.get("author_association"), (str, type(None)))
                and isinstance(i.get("author"), (str, type(None)))
                and isinstance(i.get("created_at"), (str, type(None))))

    evidence_available = (isinstance(raw_issues, list)
                          and all(evidence_item_ok(i) for i in raw_issues))
    cohort = []
    if summary.get("definition_version") == 3 and observed and evidence_available:
        cutoff = (observed.date() - timedelta(days=89)).isoformat()
        cohort = [i for i in raw_issues
                  if not i.get("removed") and i.get("author_association") not in {"OWNER", "MEMBER"}
                  and not str(i.get("author") or "").endswith("[bot]")
                  and isinstance(i.get("created_at"), str)
                  and cutoff <= i["created_at"][:10] <= observed.date().isoformat()]
    return {"summary": summary, "open_scan": open_scan, "history": history,
            "response_cohort": cohort, "response_evidence_available": evidence_available}


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


def response_rows(summary, signals=None, evidence=None):
    """Only complete v3 response observations can support A of N claims."""
    rows = []
    signals = signals if signals is not None else Signals()
    evidence = evidence or {}
    available = (summary.get("definition_version") == 3
                 and summary.get("coverage", "complete") == "complete"
                 and summary.get("response_coverage") == "complete")
    for kind, label, inline in (("issues", "Community issues", "community issues"),
                                ("prs", "Community PRs", "community PRs")):
        prefix = f"community_{kind}_"
        get = lambda key: summary.get(prefix + key) if available else None
        keys = ("response_eligible_90d", "answered_within_48h_90d", "median_first_response_hours",
                "unanswered_90d", "pending_90d", "unknown_attribution_90d", "unknown_clock_90d")
        def record(key, value, **kwargs):
            signals.add(f"{kind}.{key}", value, "90d", summary.get("response_collected_at"),
                        unit="hours" if key == "median_first_response_hours" else "count",
                        evidence_urls=evidence.get(kind, []),
                        window_end=summary.get("collected_at"), **kwargs)
        for key in keys:
            record(key, None)
        n, a = get("response_eligible_90d"), get("answered_within_48h_90d")
        counts_ok = all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in (n, a))
        if not counts_ok or a > n:
            rows.append(stat_row(f"{label} answered within 48h (90d)", "n/a", muted=True))
            rows.append(stat_row(f"Median first response ({inline}, 90d)", "n/a", muted=True))
            continue
        rows.append(stat_row(f"{label} answered within 48h (90d)",
                             f"{a} of {n}" if n else "no eligible items", muted=n == 0))
        record("response_eligible_90d", n, sample_size=n)
        record("answered_within_48h_90d", a, sample_size=n)
        median = get("median_first_response_hours")
        unanswered = get("unanswered_90d")
        median_text = ("not applicable" if n == 0 or unanswered == n else format_hours(median))
        no_answers = n == 0 or unanswered == n
        answered_sample = n - unanswered if isinstance(unanswered, int) and not isinstance(unanswered, bool) and 0 <= unanswered <= n else None
        record("median_first_response_hours", None if no_answers else median,
               sample_size=answered_sample,
               coverage="not_applicable" if no_answers else None,
               reason="No answered eligible items." if no_answers else None)
        rows.append(stat_row(f"Median first response ({inline}, 90d)", median_text, muted=not is_num(median)))
        context = []
        for key, text in (("unanswered_90d", "unanswered eligible"), ("pending_90d", "pending"),
                          ("unknown_attribution_90d", "unknown attribution"), ("unknown_clock_90d", "unknown clock")):
            count = get(key)
            record(key, count)
            if is_num(count) and count > 0:
                context.append(f"{format_number(count)} {text}")
        if context:
            rows.append(stat_row(f"{label} response context (90d)", esc(", ".join(context)), muted=True))
    disposition = [summary.get(f"community_prs_{key}_90d") if available else None
                   for key in ("merged", "closed_without_merge", "still_open")]
    disposition_ok = all(is_num(n) for n in disposition)
    for key, value in zip(("merged", "closed_without_merge", "still_open"), disposition):
        signals.add(f"prs.{key}_90d", value if disposition_ok else None, "90d",
                    summary.get("response_collected_at"), evidence_urls=evidence.get("prs", []),
                    window_end=summary.get("collected_at"))
    text = (f"{format_number(disposition[0])} merged, {format_number(disposition[1])} closed without merge, "
            f"{format_number(disposition[2])} open" if all(is_num(n) for n in disposition) else "n/a")
    rows.append(stat_row("Community PR disposition (90d)", esc(text), muted=True))
    return rows


def response_evidence(issues, evidence=None):
    summary = issues["summary"]
    complete = (summary.get("definition_version") == 3
                and summary.get("coverage", "complete") == "complete"
                and summary.get("response_coverage") == "complete")
    links = []
    status_labels = {"answered": "answered", "unanswered": "unanswered", "pending": "pending eligibility",
                     "unknown_attribution": "unknown attribution", "unknown_clock": "unknown clock"}
    for item in issues["response_cohort"]:
        status = status_labels.get(str(item.get("response_status")), "n/a") if complete else "n/a (incomplete collection)"
        label = f"#{item.get('number', '?')} {item.get('type', 'item')}: {status}"
        url = item.get("url")
        urls = evidence.setdefault("prs" if item.get("type") == "pr" else "issues", []) if evidence is not None else []
        if valid_evidence_url(url):
            urls.append(url)
        link = f'<a href="{esc(url)}">{esc(label)}</a>' if valid_evidence_url(url) else esc(label)
        response = item.get("response")
        if complete and isinstance(response, dict) and response.get("definition_version") == 3:
            evidence_url = response.get("url")
            if valid_evidence_url(evidence_url):
                urls.append(evidence_url)
                detail = f"{response.get('kind', 'response')} by {response.get('actor', '?')} ({format_hours(response.get('hours'))})"
                link += f' <a href="{esc(evidence_url)}">{esc(detail)}</a>'
        links.append(f"<li>{link}</li>")
    content = ("<ul>" + "".join(links) + "</ul>" if links else
               "<p>No community items in the 90-day cohort.</p>" if complete and issues.get("response_evidence_available")
               else "<p>Response evidence unavailable.</p>")
    return '<details class="response-evidence"><summary>Response evidence (90d)</summary>' + content + '</details>'


def build_protocol_delivery(signals=None):
    """Publish only maintainer-verified upgrades with complete evidence."""
    data = load_json(ROOT / "curated" / "protocol-delivery.json")
    entries = as_list(data.get("upgrades")) if isinstance(data, dict) else []
    rows = []
    for entry_index, entry in enumerate(entries):
        if not isinstance(entry, dict) or not valid_verified_date(entry.get("verified")):
            continue
        activation = entry.get("mainnet_activation_date")
        if not valid_verified_date(activation) or not valid_evidence_url(entry.get("activation_url")):
            continue
        releases = entry.get("releases")
        if not isinstance(releases, dict):
            continue
        caps = [c for c in as_list(entry.get("caps")) if isinstance(c, dict) and valid_evidence_url(c.get("url"))]
        if not caps or not isinstance(entry.get("name"), str):
            continue
        cap_links = ", ".join(f'<a href="{esc(c["url"])}">{esc(c.get("name", "CAP"))}</a>' for c in caps)
        upgrade = f'{esc(entry["name"])}<br>{cap_links}'
        activation_link = f'<a href="{esc(entry["activation_url"])}">{esc(activation)}</a>'
        for sdk in ACTIVE_SDKS:
            release = releases.get(sdk["key"])
            value = "n/a"
            delta, shipped = None, None
            evidence = [entry["activation_url"]] + [c["url"] for c in caps]
            if isinstance(release, dict) and valid_evidence_url(release.get("url")) and isinstance(release.get("tag"), str):
                published = parse_dt(release.get("published_at"))
                if published:
                    shipped = published.astimezone(timezone.utc).date()
                    delta = (shipped - date.fromisoformat(activation)).days
                    unit = "day" if abs(delta) == 1 else "days"
                    lag = (f"shipped {abs(delta)} {unit} before activation" if delta < 0 else
                           f"shipped {delta} {unit} after activation" if delta > 0 else "shipped on activation day")
                    value = f'<a href="{esc(release["url"])}">{esc(release["tag"])}</a> ({esc(shipped.isoformat())})<br>{esc(lag)}'
                    evidence.append(release["url"])
            if signals is not None:
                key = f"protocol.{entry_index}.lag_days"
                signals[sdk["key"]].add(key, delta, "mainnet_activation", entry["verified"],
                                        unit="days", evidence_urls=evidence)
                signals[sdk["key"]][key]["protocol"] = {
                    "name": entry["name"], "mainnet_activation_date": activation,
                    "release_tag": release["tag"] if shipped else None,
                    "release_date": shipped.isoformat() if shipped else None,
                }
            rows.append(f'<tr><td>{upgrade}</td><td>{activation_link}</td><td>{esc(sdk["label"])}</td>'
                        f'<td>{value}</td><td>{esc(entry["verified"])}</td></tr>')
    content = ('<div style="overflow-x:auto"><table class="evidence-table"><thead><tr>'
               '<th>Upgrade</th><th>Mainnet activation</th><th>SDK</th><th>First supporting stable release</th><th>Verified</th>'
               '</tr></thead><tbody>' + "".join(rows) + '</tbody></table></div>' if rows else
               '<p class="muted">Protocol delivery entries are awaiting maintainer verification.</p>')
    return '<div class="card"><h2>Protocol Delivery</h2>' + content + '</div>'


def build_maintenance_cards(all_data, signals=None):
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
        metrics = signals[sdk["key"]] if signals is not None else Signals()
        evidence = {}
        evidence_html = response_evidence(sd["issues"], evidence)
        for key, window, unit, empty_reason in (
                ("median_gap_days_365d", "365d", "days", "No qualifying stable-release gaps in the 365-day window."),
                ("days_since_last", "since_last_release", "days", "No stable releases."),
                ("count_90d", "90d", "count", None), ("count_365d", "365d", "count", None)):
            available = sd["activity"]["releases_available"]
            # A successfully collected but empty release history is a real
            # empty state (not applicable), not a collection gap.
            empty = available and rs[key] is None and empty_reason is not None
            metrics.add("release." + key, rs[key] if available else None,
                        window, sd["activity"]["releases_collected_at"], unit=unit,
                        sample_size=rs["gap_count_365d"] if key == "median_gap_days_365d" and available else None,
                        coverage="not_applicable" if empty else None,
                        reason=empty_reason if empty else None,
                        window_end=TODAY.isoformat())

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

        for kind, singular in (("issues", "issue"), ("prs", "pr")):
            scan = open_scan or {}
            count, age = scan.get("open_" + kind), scan.get(singular + "_median_age_days")
            metrics.add(f"{kind}.open_count", count, "open_snapshot", scan.get("observed_at"))
            no_items = count == 0 and not is_num(age)
            metrics.add(f"{kind}.open_median_age_days", age, "open_snapshot", scan.get("observed_at"),
                        unit="days", sample_size=count,
                        coverage="not_applicable" if no_items else None,
                        reason="No open items." if no_items else None)
            for field in ("created_365d", "closed_365d", "median_close_hours"):
                metrics.add(f"{kind}.{field}", None, "365d", summary.get("collected_at"),
                            unit="hours" if field == "median_close_hours" else "count")
        for field in ("created_365d", "closed_365d"):
            metrics.add("maintainer_prs." + field, None, "365d", summary.get("collected_at"))

        if summary and summary.get("coverage", "complete") != "complete":
            rows.append(stat_row("Community cohort (365d)", "n/a (incomplete collection)", muted=True))
        elif summary:
            def cohort_rows(kind_label, created, closed, median):
                kind = "issues" if kind_label == "issues" else "prs"
                if is_num(created) and is_num(closed):
                    for field, value in (("created_365d", created), ("closed_365d", closed)):
                        metrics.add(f"{kind}.{field}", value, "365d", summary.get("collected_at"),
                                    window_end=summary.get("collected_at"))
                    metrics.add(f"{kind}.median_close_hours", median if created != 0 else None,
                                "365d", summary.get("collected_at"), unit="hours", sample_size=closed,
                                coverage="not_applicable" if created == 0 else None,
                                reason="No eligible items." if created == 0 else None,
                                window_end=summary.get("collected_at"))
                if not is_num(created) or not is_num(closed):
                    rows.append(stat_row(f"Community {kind_label} (365d)", "n/a", muted=True))
                elif created == 0:
                    rows.append(stat_row(f"Community {kind_label} (365d)", "no eligible items", muted=True))
                    rows.append(stat_row(f"Median time to close (community {kind_label}, 365d)", "not applicable", muted=True))
                else:
                    rows.append(stat_row(f"Community {kind_label} (365d)", f"{created} opened, {closed} closed"))
                    rows.append(stat_row(f"Median time to close (community {kind_label}, 365d)", format_hours(median)))
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
                metrics.add("maintainer_prs.created_365d", m_created, "365d", summary.get("collected_at"), window_end=summary.get("collected_at"))
                metrics.add("maintainer_prs.closed_365d", m_closed, "365d", summary.get("collected_at"), window_end=summary.get("collected_at"))
                rows.append(stat_row("Maintainer PRs (365d)", f"{m_created} opened, {m_closed} closed"))
        else:
            rows.append(stat_row("Community cohort (365d)", "n/a", muted=True))

        rows.extend(response_rows(summary, metrics, evidence))
        rows_html = "\n    ".join(rows)
        cards.append(f'''<div class="sdk-card">
    <h3 style="color:{sdk["color"]}">{sdk["label"]}</h3>
    {rows_html}
    {evidence_html}
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


def build_usage_cards(all_data, signals=None):
    """The 2x2 usage-by-distribution-channel grid. The iOS card shows git
    clone traffic (the SPM/CocoaPods install path) and never uses the
    word downloads for it."""
    by_key = {sd["sdk"]["key"]: sd for sd in all_data}
    cards = []
    signals = signals if signals is not None else {key: Signals() for key in by_key}
    if "ios" in ENABLED_SDKS:
        cl = by_key["ios"]["clones"]
        for key, window in (("count_90d", "90d"), ("uniques_14d", "14d")):
            signals["ios"].add("clones." + key, cl.get(key), window, cl.get("collected_at"),
                               window_end=TODAY.isoformat() if key == "count_90d" else cl.get("collected_at"))
        headline = (
            f'Clones (last 90 days): {format_number(cl.get("count_90d"))}'
            f' &middot; unique cloners (last 14 days): {format_number(cl.get("uniques_14d"))}'
        )
        cards.append(usage_card("Git Clones (iOS, SPM/CocoaPods install path)", headline, "chart-ios-clones"))
    if "flutter" in ENABLED_SDKS:
        latest = by_key["flutter"]["pubdev"]["latest"]
        for key, window in (("download_count_52w", "52w"), ("download_count_30d", "30d")):
            signals["flutter"].add("pubdev." + key, latest.get(key), window, by_key["flutter"]["pubdev"]["collected_at"])
        headline = (
            f'Downloads (last 52 weeks): {format_number(latest.get("download_count_52w"))}'
            f' &middot; downloads (last 30 days): {format_number(latest.get("download_count_30d"))}'
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
        signals["php"].add("packagist.lifetime_downloads", lifetime, "lifetime", pk["collected_at"])
        signals["php"].add("packagist.last_full_month_downloads", last_full["downloads"] if last_full else None,
                           last_full["month"] if last_full else "last_full_month", mh.get("collected_at") if mh else None)
        headline = (
            f'Downloads (lifetime): {format_number(lifetime)}'
            + (f' &middot; last full month ({last_full["month"]}): {format_number(last_full["downloads"])}' if last_full else "")
        )
        cards.append(usage_card("Packagist Monthly Downloads (PHP)", headline, "chart-packagist"))
    if "kmp" in ENABLED_SDKS:
        latest = by_key["kmp"]["scarf"]["latest"]
        for key in ("downloads_90d", "unique_sources_90d"):
            signals["kmp"].add("scarf." + key, latest.get(key), "90d", latest.get("as_of"))
        headline = (
            f'Downloads (last 90 days): {format_number(latest.get("downloads_90d"))}'
            f' &middot; unique sources (last 90 days): {format_number(latest.get("unique_sources_90d"))}'
        )
        cards.append(usage_card("Maven Central Daily Downloads (KMP, via Scarf)", headline, "chart-scarf"))
    if len(cards) > 1:
        inner = "\n  ".join(cards)
        return f'<div class="two-col">\n  {inner}\n</div>'
    if cards:
        return cards[0]
    return ""


def build_usage_archive(signals=None):
    """Curated usage evidence: verified production users and linked user
    statements. Entries render only after maintainer verification."""
    label_by_key = {s["key"]: s["label"] for s in SDKS}
    users = [u for u in load_curated("verified-users.json", "users") if u.get("sdk") in ENABLED_SDKS]

    if users:
        def evidence_cell(u):
            links = f'<a href="{esc(u["url"])}">evidence</a>'
            if valid_evidence_url(u.get("affiliation_url")):
                links += f', <a href="{esc(u["affiliation_url"])}">affiliation</a>'
            return links

        def stars_cell(row_index, u):
            # A maintainer-set snapshot for open-source user projects;
            # closed-source or off-GitHub projects have no star count.
            stars = u.get("stars")
            if signals is not None:
                signals[u["sdk"]].add(f"production_user.{row_index}.stars", None,
                                      "curated_snapshot", reason="No verified public repository star snapshot.")
            if (isinstance(stars, int) and not isinstance(stars, bool) and stars >= 0
                    and valid_evidence_url(u.get("repo_url"))
                    and valid_verified_date(u.get("stars_as_of"))):
                if signals is not None:
                    signals[u["sdk"]].add(f"production_user.{row_index}.stars", stars,
                                          "curated_snapshot", u["stars_as_of"], evidence_urls=[u["repo_url"]])
                return f'<a href="{esc(u["repo_url"])}">{format_number(stars)}</a>'
            return '<span class="muted">-</span>'

        user_rows = "\n      ".join(
            f'<tr><td>{esc(label_by_key.get(u["sdk"], u["sdk"]))}</td><td>{esc(u.get("name", ""))}</td>'
            f'<td>{esc(u.get("evidence_kind", ""))}</td>'
            f'<td>{evidence_cell(u)}</td><td>{stars_cell(i, u)}</td><td>{esc(u.get("verified", ""))}</td></tr>'
            for i, u in enumerate(users)
        )
        users_html = f'''<table class="evidence-table">
      <tr><th>SDK</th><th>Project</th><th>Evidence kind</th><th>Evidence</th><th>GitHub stars</th><th>Verified</th></tr>
      {user_rows}
    </table>'''
    else:
        users_html = '<div class="empty-state">Entries appear after maintainer verification.</div>'

    return f'''<div class="card">
    <h2>Production Users (examples with public evidence)</h2>
    {users_html}
  </div>'''


def build_community_feedback(signals=None):
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
        if signals is not None:
            signals[sdk["key"]].add("feedback.verified_comments", None, "Q3 2026 proposal thread",
                                     reason="No verified public comments are published.")
        entries = by_sdk.get(sdk["key"])
        if not entries:
            continue
        thread_url = entries[0]["url"].split("#")[0]
        n = len(entries)
        if signals is not None:
            # Each entry has its own verified date; do not invent a single
            # collection timestamp for this manually curated collection.
            signals[sdk["key"]].add("feedback.verified_comments", n, "Q3 2026 proposal thread",
                                     evidence_urls=[thread_url],
                                     reason="No shared collection timestamp; individual verified_dates are supplied.")
            signals[sdk["key"]]["feedback.verified_comments"]["verified_dates"] = [e["verified"] for e in entries]
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


def build_reach_cards(all_data, signals=None):
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
        metrics = signals[sdk["key"]] if signals is not None else Signals()
        for key, value in (("stars", stars), ("forks", forks)):
            metrics.add("reach." + key, value, "snapshot", sd["meta"]["repo_collected_at"])
        metrics.add("release.all_time_count", releases_total, "lifetime", sd["activity"]["releases_collected_at"])
        metrics.add("release.latest_stable", {"tag": rs["latest_tag"], "date": rs["latest_date"]} if rs["latest_tag"] else None,
                    "latest", sd["activity"]["releases_collected_at"], numeric=False, unit="release")
        metrics.add("release.first", sdk["first_release"], "first_release", numeric=False, unit="release",
                    reason="Static repository history; observation time is not collected.")
        metrics.add("reach.dependents", None, "snapshot", (dep or {}).get("as_of"))

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
            # The graph does not map Maven/Gradle coordinates to source
            # repos (a mature control, java-stellar-sdk, also reads 0), so
            # a KMP zero is a blind spot, not a measured absence. A nonzero
            # count would mean the mapping started working and is shown.
            if sdk["key"] == "kmp" and total_dep == 0:
                metrics.add("reach.dependents", None, "snapshot", dep.get("as_of"),
                            coverage="not_applicable", reason="Not tracked for Gradle/Maven.")
                rows.append(stat_row("Dependents (GitHub graph count)", "not tracked for Gradle/Maven", muted=True))
            else:
                metrics.add("reach.dependents", total_dep, "snapshot", dep.get("as_of"))
                rows.append(stat_row("Dependents (GitHub graph count)", format_number(total_dep)))
        elif sdk["key"] == "ios":
            metrics.add("reach.dependents", None, "snapshot", coverage="not_applicable", reason="Not tracked for SPM.")
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
            ("Response events", sd["issues"]["summary"].get("response_collected_at")),
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
  <p>Profiles: <a href="profiles/index.json">download JSON index</a>.</p>
  <div style="overflow-x:auto">
    <table class="evidence-table">
    {table}
    </table>
  </div>
  <div class="definitions">
    <h3>Definitions</h3>
    <ul>
      <li id="definitions">Profiles export this dashboard's raw signals, samples, coverage, freshness, and evidence following the <a href="https://github.com/SCF-Public-Goods-Maintenance/pg-atlas-backend/issues/80">maintenance-profile proposal</a>; uncollected signals and percentiles are null with reasons. There is no comparison pool or combined score.</li>
      <li>All times are UTC. Each source shows its own last successful collection time; a green build never implies every source is fresh.</li>
      <li>Release cadence (shown on the maintenance cards as median release gap): median gap in days between stable (non-prerelease) GitHub releases, over gaps whose later release falls in the trailing 365 days. GitHub publication is the shipping proxy; registry artifacts may lag briefly.</li>
      <li>Commit activity: GitHub's per-day commit counts for the default branch, shown for the trailing 365 days.</li>
      <li>Open issues and PRs: complete scan of all open items at the stated observation time (no window). Zero shows a real zero; median age at zero is not applicable.</li>
      <li>Community cohort: issues and PRs created in the trailing 365 days, excluding self-filed (owner/member), bot-authored, and removed items. Counts are shown as opened and closed, never percentages. Median time to close covers the closed items of this cohort, from creation to closure.</li>
      <li>Maintainer PRs count owner/member-authored pull requests created in the same trailing 365 days, as workload context; bot-authored PRs (dependabot and similar) belong to neither cohort and are not shown.</li>
      <li>Responsiveness (definition v3): community issues and PRs created in the trailing 90 UTC calendar days, including today (89-day offset), with the same exclusions as the community cohort. A of N counts eligible items whose first qualifying response arrived within 48 elapsed hours, including exactly 48h. No percentage or combined score is shown. An empty denominator reads no eligible items; incomplete or pre-v3 data reads n/a.</li>
      <li>Eligibility requires a full 48 hours of observation, even when already answered. The clock starts at creation; for a PR created as a draft, it starts at its first ready-for-review event. Later draft conversions do not restart the clock. A response before readiness has zero elapsed response time. If draft readiness cannot be established, creation is retained as a fallback timestamp but the unknown clock is counted and excluded from N.</li>
      <li>Qualifying issue comments, submitted PR reviews, and PR review comments must have OWNER, MEMBER, or COLLABORATOR association, come from a login not ending [bot], and not be by the item author. Pending reviews do not count. A review comment counts from when it became visible: the later of its creation and its parent review submission; comments in unsubmitted reviews do not count. For PRs, a human non-author merge or close also counts. For issues, only a close linked to a commit or merged PR counts; a manual click-close never counts. Full pagination is required. A cross-reference alone does not prove closure: GitHub must identify the closing commit or merged PR.</li>
      <li>If an unresolved actor could change the first-response result, attribution is unknown and the item is excluded from N and the median. Unknown attribution and clock counts cover the full cohort and may overlap pending items and each other. Unanswered eligible items stay in N. Median first response uses answered eligible items in the same 90-day cohort, including slow responses; the separate close medians retain their 365-day creation cohort. PR disposition covers all community PRs in the 90-day cohort, including pending and unknown items.</li>
      <li>Response evidence expands below each SDK card. Closed, answered v3 records are cached while their source update time is unchanged; open, unanswered, unknown, changed, or legacy records are collected again. A response-fetch failure makes the response section incomplete and preserves its last successful timestamp; the closure and backlog metrics remain independently covered.</li>
      <li>Differences from the <a href="https://github.com/SCF-Public-Goods-Maintenance/pg-atlas-backend/issues/80">maintenance-signals proposal</a>: this dashboard uses 48h instead of the proposed seven days, counts qualifying PR discussion and review comments as well as submitted reviews, and explicitly handles draft readiness as above. It uses counts, without percentile ranks or a comparison pool. Author exclusions remain OWNER/MEMBER and bots; there is no declared-maintainer login override. Coverage is complete/incomplete, missing data is n/a, and retained values older than 48h are marked STALE in the freshness table; host-repository and external-tracker exemptions are not configured for these four repositories.</li>
      <li>Protocol Delivery: maintainer-verified entries only. A supporting release is the first stable GitHub release whose notes explicitly announce the usable protocol API, not preliminary XDR adoption. Shipping uses the GitHub publication date as a proxy, not registry publication or testnet activation. Lag is publication minus mainnet activation in UTC calendar days; early, same-day, and later delivery are descriptive, with no judgment coloring. Each row links the release, CAP, and activation evidence.</li>
      <li>Downloads: pub.dev values are pub.dev-reported rolling 7-day totals as observed at collection (not calendar weeks); Packagist values are calendar-month sums of Packagist's daily download counts (current month partial); KMP values are Maven Central artifact downloads reported via Scarf over the stated windows (about one week of ingest lag); iOS shows git clone traffic, the retrieval path SPM and CocoaPods installs use, with the same CI/bot noise as any registry download count.</li>
      <li>Production users and community feedback are curated examples with public evidence links, verified on the stated date; never a census. GitHub star counts in the users table are maintainer-set snapshots for open-source user projects, linked to the project repository and dated in the curated file; closed-source or off-GitHub projects show a dash. Community feedback links to the public PG Award proposal threads where users and community members posted their comments; the counts cover maintainer-verified comments. The dependents number is GitHub's dependents-graph count; it is shown only where the graph can attribute dependents to the repository (pub.dev and Composer manifests). SPM manifests are not parsed by the graph, and Maven/Gradle coordinates are not mapped back to source repositories, so iOS and KMP read not tracked instead of a false zero.</li>
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
.response-evidence{font-size:0.8rem;color:#8b949e;margin-top:12px}
.response-evidence summary{cursor:pointer}
.response-evidence ul{margin:8px 0 0 18px}
.response-evidence li{margin-bottom:6px}
.response-evidence a{color:#6B93D6;overflow-wrap:anywhere}
.sdk-stat{gap:10px}
.definitions{margin-top:16px;font-size:0.82rem;color:#8b949e}
.definitions h3{color:#e6edf3;margin-bottom:8px}
.definitions ul{margin-left:18px}
.definitions li{margin-bottom:6px}
.maintenance-grid{grid-template-columns:1fr 1fr}
@media(max-width:768px){
  .two-col{grid-template-columns:1fr}
  .maintenance-grid{grid-template-columns:1fr}
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
<div class="sdk-grid maintenance-grid">
  $maintenance_cards
</div>

$protocol_delivery

<!-- Release Timeline -->
<div class="card">
  <h2>Release Timeline (365d)</h2>
  <div id="chart-releases" class="chart-tall"></div>
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

<!-- Usage by distribution channel -->
<h2 class="section-title">Usage by Distribution Channel</h2>
$usage_cards

<!-- Usage archive -->
<h2 class="section-title">Who Uses These SDKs</h2>
$usage_archive

<!-- Community feedback -->
$community_feedback

<!-- Reach & history -->
<h2 class="section-title">Reach and History</h2>
<div class="sdk-grid">
  $reach_cards
</div>

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


def render_dashboard():
    """One rendering pass supplies both HTML and its captured raw headlines."""
    chart_data, all_data = build_data()
    signals = {sdk["key"]: Signals() for sdk in ACTIVE_SDKS}

    today = NOW.strftime("%Y-%m-%d")

    html = HTML_TEMPLATE.substitute(
        last_updated=today,
        subtitle=build_subtitle(),
        maintenance_cards=build_maintenance_cards(all_data, signals),
        usage_cards=build_usage_cards(all_data, signals),
        protocol_delivery=build_protocol_delivery(signals),
        usage_archive=build_usage_archive(signals),
        community_feedback=build_community_feedback(signals),
        reach_cards=build_reach_cards(all_data, signals),
        heatmap_divs=build_heatmap_divs(),
        heatmap_legend=build_heatmap_legend(),
        freshness_section=build_freshness_section(all_data),
        chart_data_json=json.dumps(chart_data, separators=(",", ":")).replace("</", "<\\/"),
    )
    for sdk in ACTIVE_SDKS:
        metrics = signals[sdk["key"]]
        missing = {
            "activity.days_since_last_push": ("since_last_push", "Pure push timestamps are not collected; commit dates are not push dates."),
            "activity.non_merge_commits": ("not_configured", "Bot-filtered non-merge git-log counts are not collected; the heatmap uses GitHub daily counts."),
            "issues.answered_within_7d": ("90d", "The dashboard publishes 48-hour A/N counts, not a seven-day response signal."),
            "prs.answered_within_7d": ("90d", "The dashboard publishes 48-hour A/N counts, not a seven-day response signal."),
            "maintainer_issues.created_90d": ("90d", "Separate maintainer issue counts are not published."),
            "maintainer_prs.created_90d": ("90d", "Only the separate 365-day maintainer PR cohort is published."),
            "host.contributor_activity": ("not_configured", "Contributor-scoped host-repository activity is not collected."),
        }
        if not any(key.startswith("protocol.") for key in metrics):
            missing["protocol.lag_days"] = ("mainnet_activation", "No maintainer-verified protocol delivery rows are available.")
        for key, (window, reason) in missing.items():
            metrics.add(key, None, window, reason=reason,
                        unit="days" if key.endswith(("days", "push")) else "count")
        for key, signal in metrics.items():
            prefix = key.split(".", 1)[0]
            filename = {"release": "github-activity.json", "issues": "github-issues.json",
                        "prs": "github-issues.json", "maintainer_prs": "github-issues.json",
                        "clones": "github-clones.json", "pubdev": "pub-dev.json",
                        "packagist": "packagist.json", "scarf": "scarf.json",
                        "reach": "github-dependents.json" if key == "reach.dependents" else "github-meta.json"}.get(prefix)
            curated = {"protocol": "protocol-delivery.json", "production_user": "verified-users.json",
                       "feedback": "user-statements.json"}.get(prefix)
            signal["source_files"] = ([f"curated/{curated}"] if curated else
                                      [sdk["folder"] + "/" + filename] if filename else [])
            if key in missing or key == "release.first" or (key == "reach.dependents" and sdk["key"] == "ios"):
                signal["source_files"] = []
    return html, signals


def profile_documents(signals, methodology):
    """Serialize the captured page values using the documented local schema."""
    documents = {}
    index = {"schema_version": 1, "methodology": methodology, "sdks": []}
    for sdk in ACTIVE_SDKS:
        filename = sdk["folder"] + ".json"
        identity = {key: sdk[key] for key in ("key", "folder", "label")}
        identity["repository_url"] = "https://github.com/Soneso/" + sdk["folder"]
        documents[filename] = {
            "schema_version": 1, "sdk": identity, "as_of": methodology["as_of"],
            "methodology": methodology, "signals": signals[sdk["key"]],
            "comparison_pool": None, "comparison_pool_reason": "No comparison pool is maintained.",
        }
        index["sdks"].append({**identity, "profile": filename})
    documents["index.json"] = index
    return {name: json.dumps(value, indent=2, allow_nan=False) + "\n" for name, value in documents.items()}


def generate():
    html, signals = render_dashboard()
    documents = profile_documents(signals, build_provenance())
    OUT.parent.mkdir(parents=True, exist_ok=True)
    profiles = OUT.parent / "profiles"
    # Stage every artifact before replacing outputs. The workflow publishes
    # page and profiles in one commit only after this entire step succeeds.
    with tempfile.TemporaryDirectory(prefix=".dashboard-", dir=OUT.parent) as staging:
        staging = Path(staging)
        (staging / "page.html").write_text(html, encoding="utf-8")
        for name, text in documents.items():
            (staging / name).write_text(text, encoding="utf-8")
        profiles.mkdir(exist_ok=True)
        for name in documents:
            os.replace(staging / name, profiles / name)
        for sdk in SDKS:
            name = sdk["folder"] + ".json"
            if name not in documents:
                (profiles / name).unlink(missing_ok=True)
        os.replace(staging / "page.html", OUT)
    print(f"Dashboard written to {OUT}")


if __name__ == "__main__":
    generate()
