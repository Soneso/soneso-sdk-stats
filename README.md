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

## Workflows

| Workflow | Schedule (UTC) |
|----------|---------------|
| Collect SDK Traffic Stats | 10:00 |
| Collect Packagist Stats | 10:05 |
| Collect pub.dev Stats | 10:10 |
| Collect GitHub Meta Stats | 10:20 |
