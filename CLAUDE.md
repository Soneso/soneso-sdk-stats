# CLAUDE.md

## Project overview

This repo collects daily statistics for Soneso Stellar SDKs via GitHub Actions workflows. Each workflow fetches data from an API, merges it with existing JSON files, and commits the result.

## Repository structure

- `.github/workflows/collect-github.yml` — GitHub clone stats for all 4 SDKs (10:00 UTC)
- `.github/workflows/collect-packagist.yml` — Packagist download stats for stellar-php-sdk (10:05 UTC)
- `.github/workflows/collect-pubdev.yml` — pub.dev download stats for stellar_flutter_sdk (10:10 UTC)
- `.github/workflows/collect-scarf.yml` — Maven Central download stats for kmp-stellar-sdk via the Scarf v3 insights API (10:15 UTC)
- `.github/workflows/collect-github-meta.yml` — GitHub repo stats and page views (10:20 UTC)
- `.github/workflows/collect-github-activity.yml` — Commit frequency and release history (10:25 UTC)
- `.github/workflows/collect-github-issues.yml` — Issue/PR response times and closure stats (10:30 UTC)
- `.github/workflows/collect-github-dependents.yml` — GitHub "Used by" dependents list and counts via HTML scraping (10:35 UTC, excludes iOS SDK)
- `.github/workflows/build-dashboard.yml` — Generates `docs/index.html` dashboard from JSON data (11:00 UTC)
- `dashboard/build.py` — Python build script (stdlib only) that reads all JSON data files and generates the dashboard HTML
- `docs/index.html` — Generated dashboard page, not hand-edited (Apache ECharts from CDN)
- `<sdk-folder>/github-clones.json` — accumulated GitHub clone data
- `<sdk-folder>/packagist.json` — accumulated Packagist download snapshots
- `<sdk-folder>/pub-dev.json` — pub.dev stats: `latest` (30d count, 4w/12w totals), `weekly` (52-week history with ISO week labels), `daily` (daily snapshots)
- `<sdk-folder>/github-meta.json` — accumulated GitHub metadata (stars, forks, issues, views)
- `<sdk-folder>/github-activity.json` — weekly commit counts (52w+) and full release history with summary
- `<sdk-folder>/github-issues.json` — issue/PR list with first response times, closure times, and summary stats
- `<sdk-folder>/github-dependents.json` — dependent repos/packages with metadata (stars, forks) and daily count history (schema v2, not collected for iOS SDK)
- `kmp-stellar-sdk/scarf.json` — Maven Central downloads via Scarf: `latest` (90d downloads + unique sources), `packages_90d` (per-artifact), `daily` (per-day totals, KMP only)

## Key patterns

- Collection scripts are inline in workflow YAML using single-quoted heredocs (`<<'PYTHON'`); `dashboard/build.py` is a standalone file
- Data files use `schema_version` for future migrations (github-clones/packagist: v1, pub-dev: v2)
- Daily entries are sorted descending (most recent first)
- Deduplication is by date string as dictionary key (latest value wins)
- Atomic writes: write to `.tmp` file then `os.replace()`
- Per-repo/package try/except so one failure doesn't block others
- github-meta: per-API-call try/except within each repo (partial data written on partial failure)
- github-meta schema: v1, combines repo metadata + views in one file per SDK
- github-activity: commit_activity API may return 202 (computing), retry up to 3 times with 5s delay
- github-activity: release summary edge cases: 0 releases = null fields, 1 release = null avg_days
- github-activity: draft releases filtered out (they have null published_at)
- github-issues: first_response_at is cached per issue to avoid re-fetching comments
- github-issues: only fetches issues updated in last 365 days, capped at 500 per repo
- github-issues: 0.5s sleep between comments API calls (secondary rate limit protection)
- github-issues: self-filed issues (OWNER/MEMBER) and bot-authored items (author ending in `[bot]`) excluded from response time metrics, included in counts
- github-issues: closed_issues_365d filters on closed_at date, not updated_at
- github-issues: stale open reconciliation — issues marked open in cache but absent from API response are auto-closed with `removed: true` flag (handles deleted/transferred issues); removed issues are excluded from all metrics
- github-dependents: HTML scraping (no API available), fragile regex, stores null on parse failure
- github-dependents: paginates all dependent pages (27 per page), capped at 500 entries
- github-dependents: dependents_list sorted by stars descending, then owner/repo name
- github-dependents: iOS SDK excluded (GitHub does not track SPM dependencies)
- github-dependents: schema v2 — adds dependents_list (repos + packages with metadata) before daily
- github-dependents: staleness check emits error if no successful scrape in 7 days
- dashboard: `build.py` uses stdlib only (no pip dependencies), atomic write to `docs/index.html`
- dashboard: ECharts pinned to 6.0.0 from jsDelivr CDN with SRI integrity hash
- dashboard: time-series data capped at rolling 365-day window to limit file size
- dashboard: weekly commit data expanded into per-day entries for calendar heatmap
- Push retries: 3 attempts with `git pull --rebase` and 5s backoff
- Packagist, pub.dev, and scarf workflows use `User-Agent: soneso-sdk-stats/1.0`
- scarf: uses the v3 insights API (`/v3/insights/{owner}/aggregations/export`, NDJSON; the v2 packages aggregates endpoints return no rows for this org). Maven Central data reaches Scarf with ~1 week of ingest lag, so each run re-fetches a 45-day trailing window and rewrites those days
- scarf: org-wide unique sources = distinct `origin_id` across a `breakdown=by-origin` export; summing per-artifact `unique_origins` overcounts (one consumer pulls common + platform artifacts)
- scarf: on fetch failure the job keeps the old file and exits green, but errors if the stored file has no usable statistics (numeric summary values plus at least one well-formed history entry) or `latest.as_of` is missing, unparseable, or older than 7 days (staleness guard, like github-dependents)
- scarf: an empty or schema-invalid export on HTTP 200 is treated as a fetch failure and nothing is written; ANY invalid row rejects its whole export (daily, artifact, and origin exports alike) since silently filtering bad rows would undercount; days past the ingest-lag horizon are zero-filled so a no-download day stays on the chart axis, while the lag tail stays absent until data arrives
- scarf: window boundaries derive from the exclusive `end_date`, so the 90d summary and 45d daily windows cover exactly that many calendar days (today included); malformed daily history entries are dropped at load with a warning
- dashboard: `extract_scarf` validates the file shape and falls back to empty Scarf data, so a corrupt scarf.json cannot abort the dashboard build

## Conventions

- Workflow names: `Collect <Source> Stats`
- Concurrency groups: one per workflow, `cancel-in-progress: false`
- Cron schedules staggered by 5 minutes to avoid push conflicts
- Top-level `permissions: {}`, job-level `contents: write`
- `actions/checkout` pinned to full SHA
- Commit messages: `Update <source> data YYYY-MM-DD`
- Committer: `github-actions[bot]`

## Secrets

- `TRAFFIC_TOKEN` — Fine-grained PAT with `administration:read` on the 4 SDK repos (used by all `gh api` workflows: collect-github.yml, collect-github-meta.yml, collect-github-activity.yml, collect-github-issues.yml). Required for correct `author_association` on issues (org membership is not visible with the default `GITHUB_TOKEN`).
- The dependents workflow uses unauthenticated `curl` (HTML scraping, not GitHub API)
- `SCARF_API_TOKEN` — Scarf API bearer token (user token from app.scarf.sh account settings, org `Soneso`), used by collect-scarf.yml
