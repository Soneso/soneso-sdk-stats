#!/usr/bin/env python3
"""Build the Soneso Stellar SDK Stats dashboard.

Reads JSON data files from the 4 SDK folders and generates a single-page
dark-themed dashboard at docs/index.html using Apache ECharts.
"""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from string import Template

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "index.html"

SDKS = [
    {"key": "ios", "folder": "stellar-ios-mac-sdk", "label": "iOS", "color": "#FF6B4A", "first_release": "1.0.0 (2018-03-02)", "top_dependent": "LOBSTR Wallet"},
    {"key": "flutter", "folder": "stellar_flutter_sdk", "label": "Flutter", "color": "#54C5F8", "first_release": "0.7.8 (2020-06-24)", "top_dependent": "Beans App"},
    {"key": "php", "folder": "stellar-php-sdk", "label": "PHP", "color": "#6B93D6", "first_release": "0.0.1 (2021-12-30)", "top_dependent": "stellarchain.io"},
    {"key": "kmp", "folder": "kmp-stellar-sdk", "label": "KMP", "color": "#B07CFF", "card_label": "KMP (new)", "first_release": "v0.2.0 (2025-10-25)"},
]

# Which SDKs to show on the dashboard. Remove a key to hide it.
ENABLED_SDKS = {"ios", "flutter", "php"}

_valid_keys = {s["key"] for s in SDKS}
_invalid = ENABLED_SDKS - _valid_keys
if _invalid:
    raise ValueError(f"ENABLED_SDKS contains unknown keys: {_invalid}")

ACTIVE_SDKS = [s for s in SDKS if s["key"] in ENABLED_SDKS]

CUTOFF_DAYS = 365


def load_json(path):
    """Load a JSON file, returning None if missing or invalid."""
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def cutoff_date():
    return (datetime.now(timezone.utc) - timedelta(days=CUTOFF_DAYS)).strftime("%Y-%m-%d")


def trim_daily(entries, key="date"):
    """Keep only entries within the rolling window."""
    cut = cutoff_date()
    return [e for e in (entries or []) if e.get(key, "") >= cut]


# ── Data extraction ──────────────────────────────────────────────────

def extract_clones(sdk):
    data = load_json(ROOT / sdk["folder"] / "github-clones.json")
    if not data:
        return []
    return trim_daily(data.get("daily", []))


def extract_meta(sdk):
    data = load_json(ROOT / sdk["folder"] / "github-meta.json")
    if not data:
        return {"repo_daily": [], "views_daily": []}
    repo_daily = trim_daily((data.get("repo") or {}).get("daily", []))
    views_daily = trim_daily((data.get("views") or {}).get("daily", []))
    return {"repo_daily": repo_daily, "views_daily": views_daily}


def extract_activity(sdk):
    data = load_json(ROOT / sdk["folder"] / "github-activity.json")
    if not data:
        return {"weekly_commits": [], "releases": [], "release_summary": None}
    commits = data.get("commits", {})
    releases = data.get("releases", {})
    weekly = commits.get("weekly", [])
    all_releases = releases.get("all", [])
    cut = cutoff_date()
    all_releases = [
        r for r in all_releases
        if r.get("published_at", "")[:10] >= cut
    ]
    return {
        "weekly_commits": weekly,
        "releases": all_releases,
        "release_summary": releases.get("summary"),
    }


def extract_issues(sdk):
    data = load_json(ROOT / sdk["folder"] / "github-issues.json")
    if not data:
        return None
    return data.get("summary")


def extract_dependents(sdk):
    if sdk["key"] == "ios":
        return None
    data = load_json(ROOT / sdk["folder"] / "github-dependents.json")
    if not data:
        return None
    dl = data.get("dependents_list", {})
    repos = dl.get("repos", [])
    packages = dl.get("packages", [])
    daily = data.get("daily", [])
    latest_count = daily[0] if daily else {}
    return {
        "repos": repos[:20],
        "packages": packages[:10],
        "total_repos": latest_count.get("dependent_repos", 0),
        "total_packages": latest_count.get("dependent_packages", 0),
    }


def extract_packagist():
    data = load_json(ROOT / "stellar-php-sdk" / "packagist.json")
    if not data:
        return {"latest": {}, "daily": []}
    return {
        "latest": data.get("latest", {}),
        "daily": trim_daily(data.get("daily", [])),
    }


def extract_pubdev():
    data = load_json(ROOT / "stellar_flutter_sdk" / "pub-dev.json")
    if not data:
        return {"latest": {}, "weekly": []}
    return {
        "latest": data.get("latest", {}),
        "weekly": data.get("weekly", [])[:52],
    }


# ── Commit heatmap expansion ────────────────────────────────────────

def expand_commits_to_days(weekly_commits):
    """Expand weekly commit data into [date, count] pairs for calendar heatmap."""
    result = []
    cut = cutoff_date()
    for week in weekly_commits:
        week_start = week.get("week_start", "")
        days = week.get("days", [0] * 7)
        if not week_start:
            continue
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


# ── KPI computation ──────────────────────────────────────────────────

def compute_sdk_summary(sdk_data):
    """Compute per-SDK summary stats for the summary card."""
    summary = {}

    # Stars & forks
    repo_daily = sdk_data["meta"]["repo_daily"]
    if repo_daily:
        summary["stars"] = repo_daily[0].get("stars", 0)
        summary["forks"] = repo_daily[0].get("forks", 0)
    else:
        summary["stars"] = 0
        summary["forks"] = 0

    # Releases
    rel_summary = sdk_data["activity"]["release_summary"]
    if rel_summary:
        summary["releases"] = rel_summary.get("total_releases", 0)
        summary["latest_release"] = rel_summary.get("latest_release")
        summary["latest_release_date"] = rel_summary.get("latest_release_date")
    else:
        summary["releases"] = 0
        summary["latest_release"] = None
        summary["latest_release_date"] = None

    # Issues
    issues = sdk_data.get("issues")
    if issues:
        summary["open_issues"] = (issues.get("open_issues") or 0) + (issues.get("open_prs") or 0)
        summary["median_response_hours"] = issues.get("median_first_response_hours")
    else:
        summary["open_issues"] = 0
        summary["median_response_hours"] = None

    # Dependents
    dep = sdk_data.get("dependents")
    if dep:
        summary["dependents"] = (dep.get("total_repos") or 0) + (dep.get("total_packages") or 0)
    else:
        summary["dependents"] = None

    # Packagist (PHP only)
    packagist_latest = sdk_data.get("packagist", {}).get("latest", {})
    if packagist_latest:
        summary["packagist_total"] = packagist_latest.get("total")
        summary["packagist_monthly"] = packagist_latest.get("monthly")
    else:
        summary["packagist_total"] = None
        summary["packagist_monthly"] = None

    # pub.dev (Flutter only)
    pubdev_latest = sdk_data.get("pubdev", {}).get("latest", {})
    if pubdev_latest:
        summary["pubdev_52w"] = pubdev_latest.get("download_count_52w")
        summary["pubdev_30d"] = pubdev_latest.get("download_count_30d")
    else:
        summary["pubdev_52w"] = None
        summary["pubdev_30d"] = None

    return summary


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
        # Only PHP has packagist
        if sdk["key"] == "php":
            sdk_data["packagist"] = extract_packagist()
        else:
            sdk_data["packagist"] = {"latest": {}, "daily": []}
        # Only Flutter has pub.dev
        if sdk["key"] == "flutter":
            sdk_data["pubdev"] = extract_pubdev()
        else:
            sdk_data["pubdev"] = {"latest": {}, "weekly": []}
        all_data.append(sdk_data)

    # Prepare chart data
    chart_data = {
        "sdks": [s["label"] for s in ACTIVE_SDKS],
        "colors": [s["color"] for s in ACTIVE_SDKS],
        "clones": {},
        "packagist_daily": [],
        "pubdev_weekly": [],
        "commit_heatmaps": {},
        "releases": {},
        "issues": {},
        "dependents": {},
    }

    for i, sdk in enumerate(ACTIVE_SDKS):
        sd = all_data[i]
        chart_data["clones"][sdk["label"]] = sd["clones"]
        # Commit heatmap
        chart_data["commit_heatmaps"][sdk["label"]] = expand_commits_to_days(
            sd["activity"]["weekly_commits"]
        )

        # Releases
        chart_data["releases"][sdk["label"]] = [
            {"date": r["published_at"][:10], "tag": r.get("tag", ""), "name": r.get("name", "")}
            for r in sd["activity"]["releases"]
        ]

        # Issues summary
        chart_data["issues"][sdk["label"]] = sd["issues"]

        # Dependents
        if sd["dependents"]:
            chart_data["dependents"][sdk["label"]] = sd["dependents"]

    chart_data["release_order"] = [s["label"] for s in reversed(ACTIVE_SDKS)]

    # Packagist daily (monthly field trend) — find PHP SDK by key
    php_data = next((sd for sd in all_data if sd["sdk"]["key"] == "php"), None)
    packagist = php_data["packagist"] if php_data else {"daily": []}
    chart_data["packagist_daily"] = [
        {"date": e["date"], "monthly": e.get("monthly", 0)}
        for e in packagist.get("daily", [])
    ]

    # pub.dev weekly (reversed to chronological) — find Flutter SDK by key
    flutter_data = next((sd for sd in all_data if sd["sdk"]["key"] == "flutter"), None)
    pubdev = flutter_data["pubdev"] if flutter_data else {"weekly": []}
    weekly = list(reversed(pubdev.get("weekly", [])))
    pubdev_weekly = []
    for e in weekly:
        # Convert "10 (2026)" to Monday date of that ISO week
        w = e.get("week", "")
        try:
            parts = w.split(" (")
            week_num = int(parts[0])
            year = int(parts[1].rstrip(")"))
            monday = datetime.strptime(f"{year}-W{week_num:02d}-1", "%G-W%V-%u")
            date_label = monday.strftime("%Y-%m-%d")
        except (ValueError, IndexError):
            date_label = w
        pubdev_weekly.append({"week": date_label, "downloads": e.get("download_count", 0)})
    chart_data["pubdev_weekly"] = pubdev_weekly

    return chart_data, all_data


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
.subtitle{color:#8b949e;font-size:0.85rem;margin-top:4px}
.header{display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;margin-bottom:24px;padding-bottom:16px;border-bottom:1px solid #30363d}
.sdk-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px;margin-bottom:24px}
.sdk-card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px}
.sdk-card h3{font-size:1.05rem;font-weight:600;margin-bottom:10px}
.sdk-stat{display:flex;justify-content:space-between;padding:3px 0;font-size:0.85rem}
.sdk-stat-label{color:#8b949e}
.sdk-stat-value{font-weight:600}
.card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px;margin-bottom:16px}
.chart{width:100%;height:350px}
.chart-tall{width:100%;height:420px}
.chart-heatmap{width:100%;height:150px}
.two-col{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.four-col{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px;margin-bottom:16px}
.issue-card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px}
.issue-card h3{font-size:0.95rem;margin-bottom:10px}
.issue-stat{display:flex;justify-content:space-between;padding:4px 0;font-size:0.85rem}
.issue-stat-label{color:#8b949e}
.footer{text-align:center;color:#8b949e;font-size:0.8rem;margin-top:32px;padding-top:16px;border-top:1px solid #30363d}
.footer a{color:#6B93D6;text-decoration:none}
.heatmap-legend{display:flex;gap:16px;align-items:center;font-size:0.8rem;color:#8b949e}
.heatmap-legend-item{display:flex;align-items:center;gap:5px}
.heatmap-legend-dot{width:12px;height:12px;border-radius:3px;display:inline-block}
.empty-state{color:#8b949e;font-size:0.85rem;text-align:center;padding:40px 0}
@media(max-width:768px){
  .two-col{grid-template-columns:1fr}
  .four-col{grid-template-columns:1fr 1fr}
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
  <div class="subtitle">Last updated: $last_updated</div>
</div>

<!-- SDK Summary Cards -->
<div class="sdk-grid">
  $sdk_summary_cards
</div>

<!-- Clone Traffic -->
<div class="card">
  <h2>GitHub Clones</h2>
  <div id="chart-clones" class="chart"></div>
</div>

<!-- Package Downloads -->
$package_downloads

<!-- Commit Heatmaps -->
<div class="card">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
    <h2 style="margin-bottom:0">Commit Activity</h2>
    <div class="heatmap-legend">$heatmap_legend</div>
  </div>
  <div style="height:18px"></div>
  $heatmap_divs
</div>

<!-- Release Timeline -->
<div class="card">
  <h2>Release Timeline</h2>
  <div id="chart-releases" class="chart-tall"></div>
</div>

<!-- Issue & PR Health -->
<h2 style="margin-bottom:12px">Issue &amp; PR Health</h2>
<div class="four-col">
  $issue_cards
</div>

<!-- Top Dependents -->
<div class="card">
  <h2>Top 3 GitHub Dependents by Stars</h2>
  <div id="chart-dependents" style="width:100%;height:180px"></div>
</div>

<!-- Footer -->
<div class="footer">
  <a href="https://github.com/Soneso/soneso-sdk-stats">Soneso/soneso-sdk-stats</a> &middot; Data collected daily via GitHub Actions 💙
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
var allCharts = [];

// ── Clone Traffic ──
(function() {
  // Collect all dates across SDKs
  var dateSet = {};
  SDK_NAMES.forEach(function(name) {
    (DATA.clones[name] || []).forEach(function(e) { dateSet[e.date] = true; });
  });
  var dates = Object.keys(dateSet).sort();

  var series = SDK_NAMES.map(function(name, i) {
    var byDate = {};
    (DATA.clones[name] || []).forEach(function(e) { byDate[e.date] = e; });
    return {
      name: name,
      type: 'bar',
      data: dates.map(function(d) { return (byDate[d] || {}).count || 0; }),
      itemStyle: { color: COLORS[i] },
      emphasis: { focus: 'series' }
    };
  });

  allCharts.push(initChart('chart-clones', {
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis' },
    legend: { data: SDK_NAMES, right: 0, top: 0, textStyle: { color: '#8b949e' } },
    grid: { left: 50, right: 20, bottom: 40, top: 40 },
    xAxis: { type: 'category', data: dates, axisLabel: { color: '#8b949e', rotate: 45, formatter: shortDate } },
    yAxis: { type: 'value', axisLabel: { color: '#8b949e' }, splitLine: { lineStyle: { color: '#21262d' } } },
    series: series
  }));
})();

// ── pub.dev Weekly ──
(function() {
  var weekly = DATA.pubdev_weekly || [];
  allCharts.push(initChart('chart-pubdev', {
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis' },
    legend: { data: ['Flutter'], right: 0, top: -5, textStyle: { color: '#8b949e' } },
    grid: { left: 50, right: 20, bottom: 60, top: 20 },
    xAxis: {
      type: 'category',
      data: weekly.map(function(e) { return e.week; }),
      axisLabel: { color: '#8b949e', rotate: 45, interval: 3, formatter: shortDate }
    },
    yAxis: { type: 'value', axisLabel: { color: '#8b949e' }, splitLine: { lineStyle: { color: '#21262d' } } },
    series: [{
      name: 'Flutter',
      type: 'bar',
      data: weekly.map(function(e) { return e.downloads; }),
      itemStyle: { color: '#54C5F8' }
    }]
  }));
})();

// ── Packagist Monthly ──
(function() {
  var daily = (DATA.packagist_daily || []).slice().reverse();
  allCharts.push(initChart('chart-packagist', {
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis' },
    legend: { data: ['PHP'], right: 0, top: -5, textStyle: { color: '#8b949e' } },
    grid: { left: 50, right: 20, bottom: 60, top: 20 },
    xAxis: {
      type: 'category',
      data: daily.map(function(e) { return e.date; }),
      axisLabel: { color: '#8b949e', rotate: 45, interval: Math.max(1, Math.floor(daily.length / 12)), formatter: shortDate }
    },
    yAxis: { type: 'value', axisLabel: { color: '#8b949e' }, splitLine: { lineStyle: { color: '#21262d' } } },
    series: [{
      name: 'PHP',
      type: 'line',
      data: daily.map(function(e) { return e.monthly; }),
      lineStyle: { color: '#6B93D6' },
      itemStyle: { color: '#6B93D6' },
      areaStyle: { color: 'rgba(107,147,214,0.15)' },
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
    if (heatData.length === 0) return;

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
          return '<b>' + name + '</b><br/>' + p.data.tag + '<br/>' + p.data.releaseName + '<br/>' + p.value[0];
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

// ── Top Dependents ──
(function() {
  var items = [];
  var depData = DATA.dependents || {};
  Object.keys(depData).forEach(function(sdkName) {
    var sdk = depData[sdkName];
    var color = COLORS[SDK_NAMES.indexOf(sdkName)] || '#8b949e';
    (sdk.repos || []).concat(sdk.packages || []).forEach(function(d) {
      items.push({ name: d.owner + '/' + d.repo, stars: d.stars || 0, color: color, sdk: sdkName });
    });
  });

  // Sort by stars desc, take top 3
  items.sort(function(a, b) { return b.stars - a.stars; });
  items = items.slice(0, 3).reverse(); // reverse for horizontal bar (bottom to top)

  if (items.length === 0) {
    var el = document.getElementById('chart-dependents');
    if (el) el.innerHTML = '<div class="empty-state">No dependent data available</div>';
    return;
  }

  allCharts.push(initChart('chart-dependents', {
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: 200, right: 40, bottom: 20, top: 10 },
    xAxis: {
      type: 'value',
      axisLabel: { color: '#8b949e' },
      splitLine: { lineStyle: { color: '#21262d' } }
    },
    yAxis: {
      type: 'category',
      data: items.map(function(d) { return d.name; }),
      axisLabel: { color: '#8b949e', fontSize: 11 }
    },
    series: [{
      type: 'bar',
      data: items.map(function(d) {
        return { value: d.stars, itemStyle: { color: d.color } };
      }),
      label: { show: true, position: 'right', color: '#8b949e', fontSize: 11, formatter: function(p) { return p.value + ' ★'; } }
    }]
  }));
})();

// ── Resize handler ──
window.addEventListener('resize', function() {
  allCharts.forEach(function(c) { if (c) c.resize(); });
});
</script>
</body>
</html>""")


# ── Generate HTML ────────────────────────────────────────────────────

def format_number(n):
    """Format a number with comma separators."""
    if n is None:
        return "—"
    return f"{n:,}"


def format_hours(h):
    """Format hours as a human-readable string."""
    if h is None:
        return "—"
    if h < 1:
        return f"{int(h * 60)}m"
    if h < 24:
        return f"{h:.1f}h"
    return f"{h / 24:.1f}d"


def build_issue_cards(chart_data):
    cards = []
    for i, sdk in enumerate(ACTIVE_SDKS):
        name = sdk["label"]
        color = sdk["color"]
        issues = chart_data["issues"].get(name)

        if not issues:
            cards.append(f'''<div class="issue-card">
  <h3 style="color:{color}">{name}</h3>
  <div class="empty-state">No issue data available</div>
</div>''')
            continue

        open_count = (issues.get("open_issues") or 0) + (issues.get("open_prs") or 0)
        median_resp = format_hours(issues.get("median_first_response_hours"))
        median_close = format_hours(issues.get("median_time_to_close_hours"))
        resp_pct = issues.get("issues_with_response_pct")
        resp_str = f"{resp_pct:.0f}%" if resp_pct is not None else "—"

        cards.append(f'''<div class="issue-card">
  <h3 style="color:{color}">{name}</h3>
  <div class="issue-stat"><span class="issue-stat-label">Open issues/PRs</span><span>{open_count}</span></div>
  <div class="issue-stat"><span class="issue-stat-label">Median response</span><span>{median_resp}</span></div>
  <div class="issue-stat"><span class="issue-stat-label">Median close time</span><span>{median_close}</span></div>
  <div class="issue-stat"><span class="issue-stat-label">Response rate</span><span>{resp_str}</span></div>
</div>''')
    return "\n".join(cards)


def build_sdk_summary_cards(all_data):
    cards = []
    for sdk_data in all_data:
        sdk = sdk_data["sdk"]
        s = compute_sdk_summary(sdk_data)
        color = sdk["color"]
        label = sdk.get("card_label", sdk["label"])

        rows = []
        rows.append(f'<div class="sdk-stat"><span class="sdk-stat-label">Stars</span><span class="sdk-stat-value">{format_number(s["stars"])}</span></div>')
        rows.append(f'<div class="sdk-stat"><span class="sdk-stat-label">Forks</span><span class="sdk-stat-value">{format_number(s["forks"])}</span></div>')
        rows.append(f'<div class="sdk-stat"><span class="sdk-stat-label">Releases</span><span class="sdk-stat-value">{format_number(s["releases"])}</span></div>')
        if s["latest_release"]:
            rows.append(f'<div class="sdk-stat"><span class="sdk-stat-label">Latest release</span><span class="sdk-stat-value">{s["latest_release"]} ({s["latest_release_date"]})</span></div>')
        rows.append(f'<div class="sdk-stat"><span class="sdk-stat-label">First release</span><span class="sdk-stat-value">{sdk["first_release"]}</span></div>')
        rows.append(f'<div class="sdk-stat"><span class="sdk-stat-label">Open issues/PRs</span><span class="sdk-stat-value">{s["open_issues"]}</span></div>')
        rows.append(f'<div class="sdk-stat"><span class="sdk-stat-label">Median response</span><span class="sdk-stat-value">{format_hours(s["median_response_hours"])}</span></div>')
        if sdk.get("top_dependent"):
            rows.append(f'<div class="sdk-stat"><span class="sdk-stat-label">Top ecosystem dependent</span><span class="sdk-stat-value">{sdk["top_dependent"]}</span></div>')
        if s["dependents"] is not None and sdk["key"] != "kmp":
            rows.append(f'<div class="sdk-stat"><span class="sdk-stat-label">Dependents (GitHub)</span><span class="sdk-stat-value">{format_number(s["dependents"])}</span></div>')
        elif sdk["key"] == "ios":
            rows.append('<div class="sdk-stat"><span class="sdk-stat-label">Dependents (GitHub)</span><span class="sdk-stat-value" style="color:#8b949e">N/A for SPM</span></div>')
        if s["packagist_total"]:
            rows.append(f'<div class="sdk-stat"><span class="sdk-stat-label">Packagist total</span><span class="sdk-stat-value">{format_number(s["packagist_total"])}</span></div>')
            rows.append(f'<div class="sdk-stat"><span class="sdk-stat-label">Packagist monthly</span><span class="sdk-stat-value">{format_number(s["packagist_monthly"])}</span></div>')
        if s["pubdev_52w"]:
            rows.append(f'<div class="sdk-stat"><span class="sdk-stat-label">pub.dev downloads (52w)</span><span class="sdk-stat-value">{format_number(s["pubdev_52w"])}</span></div>')
            rows.append(f'<div class="sdk-stat"><span class="sdk-stat-label">pub.dev downloads (30d)</span><span class="sdk-stat-value">{format_number(s["pubdev_30d"])}</span></div>')

        rows_html = "\n    ".join(rows)
        cards.append(f'''<div class="sdk-card">
    <h3 style="color:{color}">{label}</h3>
    {rows_html}
  </div>''')
    return "\n  ".join(cards)


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


def build_package_downloads_section():
    """Build the package downloads HTML section based on which SDKs are active."""
    has_pubdev = "flutter" in ENABLED_SDKS
    has_packagist = "php" in ENABLED_SDKS

    pubdev_card = (
        '<div class="card">\n'
        '    <h2>pub.dev Weekly Downloads</h2>\n'
        '    <div id="chart-pubdev" class="chart"></div>\n'
        '  </div>'
    )
    packagist_card = (
        '<div class="card">\n'
        '    <h2>Packagist Monthly Downloads</h2>\n'
        '    <div id="chart-packagist" class="chart"></div>\n'
        '  </div>'
    )

    if has_pubdev and has_packagist:
        return (
            '<div class="two-col">\n'
            f'  {pubdev_card}\n'
            f'  {packagist_card}\n'
            '</div>'
        )
    if has_pubdev:
        return pubdev_card
    if has_packagist:
        return packagist_card
    return ""


def build_subtitle():
    """Build the dashboard subtitle from ACTIVE_SDKS."""
    labels = [s["label"] for s in ACTIVE_SDKS]
    if not labels:
        return "Soneso Stellar SDK Stats"
    if len(labels) == 1:
        return f"Daily statistics for the {labels[0]} Stellar SDK"
    subtitle_text = ", ".join(labels[:-1]) + " and " + labels[-1]
    return f"Daily statistics for {subtitle_text} Stellar SDKs"


def generate():
    chart_data, all_data = build_data()

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    html = HTML_TEMPLATE.substitute(
        last_updated=today,
        subtitle=build_subtitle(),
        sdk_summary_cards=build_sdk_summary_cards(all_data),
        package_downloads=build_package_downloads_section(),
        heatmap_divs=build_heatmap_divs(),
        heatmap_legend=build_heatmap_legend(),
        issue_cards=build_issue_cards(chart_data),
        chart_data_json=json.dumps(chart_data, separators=(",", ":")),
    )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    with open(tmp, "w") as f:
        f.write(html)
    os.replace(tmp, OUT)
    print(f"Dashboard written to {OUT}")


if __name__ == "__main__":
    generate()
