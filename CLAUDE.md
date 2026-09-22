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
- `<sdk-folder>/packagist.json` — Packagist v2: daily snapshots plus `monthly_history` (calendar-month TOTALS summed from the daily stats endpoint; the average=monthly endpoint returns rounded per-day averages and is never used)
- `<sdk-folder>/pub-dev.json` — pub.dev stats v3: `latest` (30d count, 4w/12w/52w sums), `buckets` (rolling 7-day buckets keyed by exact start..end dates, accumulating), `latest_snapshot` (authoritative bucket keys), `daily` (30d-rolling snapshots). ISO-week labels are retired: pub.dev buckets are anchored to newestDate, not calendar weeks
- `<sdk-folder>/github-meta.json` — accumulated GitHub metadata (stars, forks, issues, views)
- `<sdk-folder>/github-activity.json` — weekly commit counts (52w+) and full release history with summary
- `<sdk-folder>/github-issues.json` - issue/PR list with response/closure timestamps, v3 summary (365d closure context plus 90d response cohorts, definition_version), `open_scan` (complete backlog observation), and `history` (per-day summary snapshots, never trimmed)
- `<sdk-folder>/github-dependents.json` — dependent repos/packages with metadata (stars, forks) and daily count history (schema v2, not collected for iOS SDK)
- `kmp-stellar-sdk/scarf.json` — Maven Central downloads via Scarf: `latest` (90d downloads + unique sources), `packages_90d` (per-artifact), `daily` (per-day totals, KMP only)

## Key patterns

- Collection scripts are inline in workflow YAML using single-quoted heredocs (`<<'PYTHON'`); `dashboard/build.py` is a standalone file
- Data files use `schema_version` for migrations (github-clones: v1, packagist: v2, pub-dev: v3, github-issues: v3, scarf: v1)
- Daily entries are sorted descending (most recent first)
- Deduplication is by date string as dictionary key (latest value wins)
- Atomic writes: write to `.tmp` file then `os.replace()`
- Per-repo/package try/except so one failure doesn't block others
- github-meta: per-API-call try/except within each repo (partial data written on partial failure)
- github-meta schema: v1, combines repo metadata + views in one file per SDK
- github-activity: commit_activity API may return 202 (computing), retry up to 3 times with 20s delay
- github-activity: release summary edge cases: 0 releases = null fields, 1 release = null avg_days
- github-activity: draft releases filtered out (they have null published_at)
- github-issues: v3 response objects store the earliest qualifying event, actor, kind, exact elapsed hours, and evidence URL; legacy first_response_at/first_response_hours are archival only and never seed v3. A PR review comment's response time is the later of its creation and its parent review's submission (drafted comments are invisible until submission); comments in unsubmitted reviews never count, and a comment without a resolvable parent review degrades the item to incomplete. Closed, answered records with known clocks and attribution are cached while updated_at is unchanged; open, unanswered, unknown, changed, and pre-v3 cohort items are re-fetched. Evidence remains archived when items age out of the cohort.
- github-issues: windowed activity fetch (updated in last 365 days, capped at 500) PLUS a complete state=open scan (no since filter, all pages) that alone produces backlog observations; a failed/truncated scan yields no observation for the day and never a fresh zero, and a failed same-day rerun never erases an earlier successful observation from history. A capped or partially processed fetch sets summary.coverage=incomplete, nulls the cohort fields (the page shows n/a, not a fabricated small cohort), and suppresses removed-flag reconciliation (absence in truncated coverage is not evidence of removal)
- github-issues: 0.5s sleep before every per-item API request, including pagination and GraphQL (secondary rate limit protection). REST pages follow Link headers; a failure on any page rejects the entire endpoint. Logs count actual requests, failures, timeouts, and elapsed time per repo. A 24-minute total request budget fits within the 30-minute workflow timeout; budget exhaustion produces incomplete or retained data, never a partial successful response cohort.
- github-issues: Phase 1 closure metrics keep their v2 definitions: community items created in trailing 365d, excluding OWNER/MEMBER authors, bot authors, and removed items. Summary/history definition_version is now 3. New response metrics use a separate 90-calendar-day creation cohort (inclusive today, 89-day offset), with distinct issue and PR A/N counts within 48 elapsed hours, answered-eligible first-response medians (slow responses included), unanswered eligible counts, pending eligibility, unknown attribution/clock counts, and full-cohort PR disposition (merged/closed_without_merge/still_open).
- github-issues: full response pagination covers issue comments, PR submitted reviews and review comments, and REST timeline events. Comments/reviews require OWNER/MEMBER/COLLABORATOR association and a human non-author login; `[bot]` suffix identifies bots. Pending reviews never count. Human non-author PR merges/closes count. Issue manual closes never count: require a REST closed event with commit_id or an explicit GitHub GraphQL ClosedEvent.closer of type Commit or merged PullRequest. GraphQL resolves ambiguous REST closes, paginates all CLOSED_EVENT nodes, and rejects HTTP-200 errors; a cross-reference alone is insufficient.
- github-issues: PR draft clocks use the first ready_for_review when the first draft transition is readiness; first convert_to_draft means the PR began ready and keeps its creation clock. A draft without known readiness keeps creation as a flagged unknown fallback and is excluded from N. Eligibility requires 48 hours since both creation and the established clock; early responses do not bypass maturity. Responses before readiness have zero elapsed hours. Threshold comparisons use timestamps, not rounded display hours. Unknown earlier actors exclude the item from N and the median; an unknown later event cannot invalidate an established first response. Unknown counts cover the full cohort and may overlap pending items.
- github-issues: response_coverage and response_collected_at are independent of Phase 1 coverage/collected_at. A response endpoint failure nulls ALL new response/disposition summary and history fields, preserving the prior successful response timestamp, while successful closure/backlog observations remain valid. Capped or partially processed activity makes both cohorts incomplete. Item response_collection/response_status make incomplete evidence explicit; unknown attribution is an observed exclusion, not a fetch failure. schema_version 3 is additive and older history definitions remain unchanged.
- github-issues: stale open reconciliation — issues marked open in cache but absent from API response are auto-closed with `removed: true` flag (handles deleted/transferred issues); removed issues are excluded from all metrics. CAVEAT: an open issue dormant beyond the 365d `since` window ages out and gets a SYNTHETIC closed_at in the cache; displayed metrics are safe (open counts come solely from the open scan, the creation cohort cannot contain such items), but later phases must not trust cached state/closed_at for items flagged removed
- github-dependents: HTML scraping (no API available), fragile regex, stores null on parse failure
- github-dependents: paginates all dependent pages (27 per page), capped at 500 entries
- github-dependents: dependents_list sorted by stars descending, then owner/repo name
- github-dependents: iOS SDK excluded (GitHub does not track SPM dependencies)
- github-dependents: schema v2 — adds dependents_list (repos + packages with metadata) before daily
- github-dependents: staleness check emits error if no successful scrape in 7 days
- dashboard: `build.py` uses stdlib only (no pip dependencies), atomic write to `docs/index.html`
- dashboard: ECharts pinned to 6.0.0 from jsDelivr CDN with SRI integrity hash
- dashboard: operational time series (clones, heatmaps) capped at a rolling 365-day window; download histories are exempt (long history is their value: Packagist since 2021-12, pub.dev buckets accumulating, Scarf accumulating, iOS clones full archive)
- dashboard: weekly commit data expanded into per-day entries for calendar heatmap
- Per-section freshness: the dashboard's freshness table marks any source timestamp older than 48 hours as STALE (collection runs daily, so an old timestamp means retained values, not fresh ones); every collector writes `collected_at` (github-meta: per repo/views section; github-activity: per commits/releases section; clones/packagist/pub-dev/issues: per file), advanced only on that section's successful fetch; collection time is never inferred from data recency
- dashboard: page order keeps the two pillars unmixed: the maintenance block (Maintenance cards 2 per row, Protocol Delivery, Release Timeline, Commit Activity), then the usage block (Usage by Distribution Channel in maintenance-card SDK order iOS/Flutter/PHP/KMP: iOS git clones labeled as the SPM/CocoaPods install path and never "downloads", pub.dev buckets + rolling 30-day trend, Packagist monthly totals, Scarf daily; curated usage archive; community feedback; reach/history), then data sources + definitions; tooltip formatters escape data-derived strings (ECharts assigns them to innerHTML) and display copies of release names have em dashes replaced
- dashboard: release cadence is recomputed from releases.all with prereleases excluded (median gap over gaps whose later release is inside 365d); the stored lifetime release summary is not used
- dashboard: small cohorts render as opened/closed counts, never percentages; zero open items is a real zero with age not applicable; missing collection renders n/a, never zero; no em dashes or emojis in rendered text
- dashboard: the four-SDK clones comparison chart and the stars-ranked dependents chart are removed; clone collection continues (the iOS usage card consumes it); the dependents-graph count row stays, labeled, never presented as a census. iOS renders "not tracked for SPM"; KMP renders "not tracked for Gradle/Maven" while its measured count is zero (the graph does not map Maven coordinates to repos: the mature java-stellar-sdk control also reads 0), and a nonzero KMP count renders because it means the mapping works. KMP collection continues so that flip is observable
- curated/: verified-users.json and user-statements.json are maintainer-curated evidence; an entry renders only with a well-formed http(s) evidence url (scheme + host) AND a verified value that parses as an ISO date; truthy non-date values do not pass the gate. Optional per-user repo_url + stars + stars_as_of render a GitHub-stars snapshot column (int stars, valid repo url, ISO date, all required; otherwise a muted dash); the snapshot is maintainer-set, not collected
- dashboard: extract_issues accepts definition_version >= 2 for closure context; only complete v3 summaries publish response claims. A of N counts are always used, N=0 is no eligible items, and unavailable/incomplete/pre-v3 values are n/a. Each maintenance card links its 90d response cohort and event evidence. Response freshness has its own row. The page lists definition differences from pg-atlas-backend#80 (48h, PR comments, draft clocks, coverage vocabulary, no rankings or login overrides).
- curated/protocol-delivery.json: schema v1 `upgrades` entries have name, caps (name/url), mainnet_activation_date, activation_url, verified, notes, and releases keyed by SDK (tag/published_at/url/note). Only a valid ISO verified date plus valid activation date/evidence/CAP URLs opens the publication gate. Missing SDK release data renders n/a. Drafts for Protocol 27 and 28 have verified:null until the maintainer's evidence pass. Qualification is the first stable release announcing the usable protocol API, not preliminary XDR adoption; GitHub publication is the shipping proxy. Lag uses UTC calendar days against mainnet activation, without judgment colors.
- tests: `python3 -m unittest discover -s tests -v` runs offline response-event, coverage/cache, boundary, protocol-gate, and all-SDK-subset rendering regressions against the actual inline collector and build.py. Live collector testing runs only the extracted Python in a scratch directory; never execute workflow commit/push steps locally.
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
