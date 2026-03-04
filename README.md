# soneso-sdk-stats

Automated daily statistics for Soneso Stellar SDKs.

## Data collected

### [stellar-ios-mac-sdk](https://github.com/Soneso/stellar-ios-mac-sdk)

| Data | Description | File |
|------|-------------|------|
| GitHub clones | Daily clone counts and unique cloners (14-day rolling window) | [github-clones.json](stellar-ios-mac-sdk/github-clones.json) |
| GitHub meta | Stars, forks, watchers, page views, and top referrers | [github-meta.json](stellar-ios-mac-sdk/github-meta.json) |
| GitHub activity | 52-week commit history and full release list with summary | [github-activity.json](stellar-ios-mac-sdk/github-activity.json) |
| GitHub issues | Issue/PR response times, closure stats, and maintainer metrics | [github-issues.json](stellar-ios-mac-sdk/github-issues.json) |

### [stellar_flutter_sdk](https://github.com/Soneso/stellar_flutter_sdk)

| Data | Description | File |
|------|-------------|------|
| GitHub clones | Daily clone counts and unique cloners (14-day rolling window) | [github-clones.json](stellar_flutter_sdk/github-clones.json) |
| pub.dev downloads | 30-day, 4/12/52-week download counts and weekly breakdown | [pub-dev.json](stellar_flutter_sdk/pub-dev.json) |
| GitHub meta | Stars, forks, watchers, page views, and top referrers | [github-meta.json](stellar_flutter_sdk/github-meta.json) |
| GitHub activity | 52-week commit history and full release list with summary | [github-activity.json](stellar_flutter_sdk/github-activity.json) |
| GitHub issues | Issue/PR response times, closure stats, and maintainer metrics | [github-issues.json](stellar_flutter_sdk/github-issues.json) |
| GitHub dependents | Dependent repos and packages with stars, forks, and daily count history | [github-dependents.json](stellar_flutter_sdk/github-dependents.json) |

### [stellar-php-sdk](https://github.com/Soneso/stellar-php-sdk)

| Data | Description | File |
|------|-------------|------|
| GitHub clones | Daily clone counts and unique cloners (14-day rolling window) | [github-clones.json](stellar-php-sdk/github-clones.json) |
| Packagist downloads | Total, monthly, and daily download counts plus favers | [packagist.json](stellar-php-sdk/packagist.json) |
| GitHub meta | Stars, forks, watchers, page views, and top referrers | [github-meta.json](stellar-php-sdk/github-meta.json) |
| GitHub activity | 52-week commit history and full release list with summary | [github-activity.json](stellar-php-sdk/github-activity.json) |
| GitHub issues | Issue/PR response times, closure stats, and maintainer metrics | [github-issues.json](stellar-php-sdk/github-issues.json) |
| GitHub dependents | Dependent repos and packages with stars, forks, and daily count history | [github-dependents.json](stellar-php-sdk/github-dependents.json) |

### [kmp-stellar-sdk](https://github.com/Soneso/kmp-stellar-sdk)

| Data | Description | File |
|------|-------------|------|
| GitHub clones | Daily clone counts and unique cloners (14-day rolling window) | [github-clones.json](kmp-stellar-sdk/github-clones.json) |
| GitHub meta | Stars, forks, watchers, page views, and top referrers | [github-meta.json](kmp-stellar-sdk/github-meta.json) |
| GitHub activity | 52-week commit history and full release list with summary | [github-activity.json](kmp-stellar-sdk/github-activity.json) |
| GitHub issues | Issue/PR response times, closure stats, and maintainer metrics | [github-issues.json](kmp-stellar-sdk/github-issues.json) |
| GitHub dependents | Dependent repos and packages with stars, forks, and daily count history | [github-dependents.json](kmp-stellar-sdk/github-dependents.json) |

## Dashboard

A dark-themed stats dashboard is auto-generated daily at [`docs/index.html`](docs/index.html) using Apache ECharts. Once GitHub Pages is enabled, it will be available at `https://soneso.github.io/soneso-sdk-stats/`.

## Workflows

| Workflow | Schedule (UTC) |
|----------|---------------|
| Collect SDK Traffic Stats | 10:00 |
| Collect Packagist Stats | 10:05 |
| Collect pub.dev Stats | 10:10 |
| Collect GitHub Meta Stats | 10:20 |
| Collect GitHub Activity Stats | 10:25 |
| Collect GitHub Issues Stats | 10:30 |
| Collect GitHub Dependents Stats | 10:35 |
| Build Dashboard | 11:00 |
