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

## Workflows

| Workflow | Schedule (UTC) | Auth required |
|----------|---------------|---------------|
| Collect SDK Traffic Stats | 6:00 | Yes (`TRAFFIC_TOKEN`) |
| Collect Packagist Stats | 6:05 | No |
| Collect pub.dev Stats | 6:10 | No |

All workflows can also be triggered manually from the Actions tab.

## Setup

The GitHub clones workflow requires a repository secret named `TRAFFIC_TOKEN` containing a Fine-grained Personal Access Token with `administration:read` permission on all 4 SDK repositories. The Packagist and pub.dev workflows use public APIs and need no authentication.

## Future plans

CocoaPods stats for the iOS SDK and Maven Central stats for the KMP SDK could be added once those APIs are available.
