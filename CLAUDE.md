# CLAUDE.md

## Project overview

This repo collects daily statistics for Soneso Stellar SDKs via GitHub Actions workflows. Each workflow fetches data from an API, merges it with existing JSON files, and commits the result.

## Repository structure

- `.github/workflows/collect-github.yml` — GitHub clone stats for all 4 SDKs (10:00 UTC)
- `.github/workflows/collect-packagist.yml` — Packagist download stats for stellar-php-sdk (10:05 UTC)
- `.github/workflows/collect-pubdev.yml` — pub.dev download stats for stellar_flutter_sdk (10:10 UTC)
- `<sdk-folder>/github-clones.json` — accumulated GitHub clone data
- `<sdk-folder>/packagist.json` — accumulated Packagist download snapshots
- `<sdk-folder>/pub-dev.json` — accumulated pub.dev download snapshots

## Key patterns

- All Python scripts are inline in workflow YAML using single-quoted heredocs (`<<'PYTHON'`)
- Data files use `schema_version: 1` for future migrations
- Daily entries are sorted descending (most recent first)
- Deduplication is by date string as dictionary key (latest value wins)
- Atomic writes: write to `.tmp` file then `os.replace()`
- Per-repo/package try/except so one failure doesn't block others
- Push retries: 3 attempts with `git pull --rebase` and 5s backoff
- Packagist and pub.dev workflows use `User-Agent: soneso-sdk-stats/1.0`

## Conventions

- Workflow names: `Collect <Source> Stats`
- Concurrency groups: one per workflow, `cancel-in-progress: false`
- Cron schedules staggered by 5 minutes to avoid push conflicts
- Top-level `permissions: {}`, job-level `contents: write`
- `actions/checkout` pinned to full SHA
- Commit messages: `Update <source> data YYYY-MM-DD`
- Committer: `github-actions[bot]`

## Secrets

- `TRAFFIC_TOKEN` — Fine-grained PAT with `administration:read` on the 4 SDK repos (used only by collect-github.yml)
