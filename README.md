# soneso-sdk-stats

Automated daily statistics for Soneso Stellar SDKs.

## Data collected

| SDK | Data | File |
|-----|------|------|
| [stellar-ios-mac-sdk](https://github.com/Soneso/stellar-ios-mac-sdk) | GitHub clones | `stellar-ios-mac-sdk/github-clones.json` |
| [stellar_flutter_sdk](https://github.com/Soneso/stellar_flutter_sdk) | GitHub clones | `stellar_flutter_sdk/github-clones.json` |
| [stellar_flutter_sdk](https://github.com/Soneso/stellar_flutter_sdk) | pub.dev downloads | `stellar_flutter_sdk/pub-dev.json` |
| [stellar-php-sdk](https://github.com/Soneso/stellar-php-sdk) | GitHub clones | `stellar-php-sdk/github-clones.json` |
| [stellar-php-sdk](https://github.com/Soneso/stellar-php-sdk) | Packagist downloads | `stellar-php-sdk/packagist.json` |
| [kmp-stellar-sdk](https://github.com/Soneso/kmp-stellar-sdk) | GitHub clones | `kmp-stellar-sdk/github-clones.json` |
| [stellar-ios-mac-sdk](https://github.com/Soneso/stellar-ios-mac-sdk) | GitHub meta (stars, forks, views, referrers) | `stellar-ios-mac-sdk/github-meta.json` |
| [stellar_flutter_sdk](https://github.com/Soneso/stellar_flutter_sdk) | GitHub meta (stars, forks, views, referrers) | `stellar_flutter_sdk/github-meta.json` |
| [stellar-php-sdk](https://github.com/Soneso/stellar-php-sdk) | GitHub meta (stars, forks, views, referrers) | `stellar-php-sdk/github-meta.json` |
| [kmp-stellar-sdk](https://github.com/Soneso/kmp-stellar-sdk) | GitHub meta (stars, forks, views, referrers) | `kmp-stellar-sdk/github-meta.json` |
| [stellar-ios-mac-sdk](https://github.com/Soneso/stellar-ios-mac-sdk) | GitHub activity (commits, releases) | `stellar-ios-mac-sdk/github-activity.json` |
| [stellar_flutter_sdk](https://github.com/Soneso/stellar_flutter_sdk) | GitHub activity (commits, releases) | `stellar_flutter_sdk/github-activity.json` |
| [stellar-php-sdk](https://github.com/Soneso/stellar-php-sdk) | GitHub activity (commits, releases) | `stellar-php-sdk/github-activity.json` |
| [kmp-stellar-sdk](https://github.com/Soneso/kmp-stellar-sdk) | GitHub activity (commits, releases) | `kmp-stellar-sdk/github-activity.json` |
| [stellar-ios-mac-sdk](https://github.com/Soneso/stellar-ios-mac-sdk) | GitHub issues (response times) | `stellar-ios-mac-sdk/github-issues.json` |
| [stellar_flutter_sdk](https://github.com/Soneso/stellar_flutter_sdk) | GitHub issues (response times) | `stellar_flutter_sdk/github-issues.json` |
| [stellar-php-sdk](https://github.com/Soneso/stellar-php-sdk) | GitHub issues (response times) | `stellar-php-sdk/github-issues.json` |
| [kmp-stellar-sdk](https://github.com/Soneso/kmp-stellar-sdk) | GitHub issues (response times) | `kmp-stellar-sdk/github-issues.json` |
| [stellar-ios-mac-sdk](https://github.com/Soneso/stellar-ios-mac-sdk) | GitHub dependents (Used by count) | `stellar-ios-mac-sdk/github-dependents.json` |
| [stellar_flutter_sdk](https://github.com/Soneso/stellar_flutter_sdk) | GitHub dependents (Used by count) | `stellar_flutter_sdk/github-dependents.json` |
| [stellar-php-sdk](https://github.com/Soneso/stellar-php-sdk) | GitHub dependents (Used by count) | `stellar-php-sdk/github-dependents.json` |
| [kmp-stellar-sdk](https://github.com/Soneso/kmp-stellar-sdk) | GitHub dependents (Used by count) | `kmp-stellar-sdk/github-dependents.json` |

## Workflows

| Workflow | Schedule (UTC) |
|----------|---------------|
| Collect SDK Traffic Stats | 10:00 |
| Collect Packagist Stats | 10:05 |
| Collect pub.dev Stats | 10:10 |
| Collect GitHub Meta Stats | 10:20 |
| Collect GitHub Activity Stats | 10:30 |
| Collect GitHub Issues Stats | 10:40 |
| Collect GitHub Dependents Stats | 10:50 |
