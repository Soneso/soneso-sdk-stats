# soneso-sdk-stats

Automated daily traffic statistics for Soneso Stellar SDKs.

## What is collected

GitHub clone counts (total and unique) for:

- [stellar-ios-mac-sdk](https://github.com/Soneso/stellar-ios-mac-sdk)
- [stellar_flutter_sdk](https://github.com/Soneso/stellar_flutter_sdk)
- [stellar-php-sdk](https://github.com/Soneso/stellar-php-sdk)
- [kmp-stellar-sdk](https://github.com/Soneso/kmp-stellar-sdk)

Data is stored as JSON in each SDK's folder (e.g. `stellar-ios-mac-sdk/github-clones.json`).

## Schedule

Runs daily at 6:00 AM UTC via GitHub Actions.

## Manual run

Go to **Actions > Collect SDK Traffic Stats > Run workflow** to trigger a run manually.

## Setup

This workflow requires a repository secret named `TRAFFIC_TOKEN` containing a Fine-grained Personal Access Token with `administration:read` permission on all 4 SDK repositories.

## Future plans

Package manager stats (pub.dev, Packagist, CocoaPods, Maven Central) may be added later.
