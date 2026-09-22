# Dashboard exports, schema 1

`python3 dashboard/build.py` produces the page and `docs/profiles/` together.
`render_dashboard()` loads each used source once and returns HTML plus raw
headline records captured at the same rendering gates. Profiles and snapshots
serialize those records; neither implements another metric calculator.

The definition identifier is `dashboard-v1-response-v3`. It names the current
dashboard definitions, including collector response definition 3. Change it
when a published definition changes. `schema_version: 1` describes the export
structure independently of collector schemas.

## Profiles

`index.json` contains `schema_version`, a shared `methodology` object and an
ordered `sdks` array. Each entry has `key`, `folder`, `label`, `repository_url`
and a relative `profile` filename. The default build lists all four SDKs.
An `ENABLED_SDKS` subset lists only those SDKs and removes previously generated
files for disabled SDKs. An empty subset produces an empty index.

Each `<sdk-folder>.json` contains:

- `schema_version`: integer 1.
- `sdk`: `key`, `folder`, `label`, `repository_url`.
- `as_of`: UTC instant used for rolling dashboard calculations.
- `methodology`: `definition_version`, actual `generated_at`, calculation
  `as_of`, full `build_commit`, `working_tree_dirty`, `dashboard_url` pointing
  to the definitions anchor, `definitions_at_commit` pointing to the committed
  HTML's definitions line, and `proposal_url`.
- `signals`: a map from metric identifiers to the records below.
- `comparison_pool`: null, with `comparison_pool_reason`.

Every signal has these fields:

| Field | Meaning |
| --- | --- |
| `value` | Raw number, null, or release context. `release.latest_stable` is a tag/date object and `release.first` is the static first-release text. Every exported field is JSON-safe under strict serialization: non-finite numbers, malformed structured values, and non-string window boundaries become null (with a reason on the value), never Infinity/NaN output. |
| `unit` | `count`, `hours`, `days`, or `release`. Medians retain their source precision, before HTML display formatting. |
| `sample_size` | Median gap count, closed-item count, open backlog count, answered eligible count, or response denominator, when applicable and available; otherwise null. |
| `window` | `90d`, `365d`, `14d`, `30d`, `52w`, a calendar month, `lifetime`, `open_snapshot`, `snapshot`, `latest`, `first_release`, `since_last_release`, `curated_snapshot`, `mainnet_activation`, or the stated proposal thread. Unsupported windows use `not_configured` or `since_last_push`. |
| `window_end` | Explicit evaluation boundary for locally calculated rolling releases/clones and collected issue cohorts; otherwise null and the source observation anchors the stated source window. |
| `coverage` | `complete`, `incomplete`, or `not_applicable`. Missing/malformed/incomplete data is null, never a synthetic zero. |
| `reason` | Required nonempty text for null values; optional explanatory text for present values. |
| `observed_at` | The section's successful source timestamp, or a curated verification date; null when no timestamp exists. Never inferred from generation time. |
| `freshness` | `fresh`, `stale` after 48 hours, or `unknown` without a timestamp. Curated historical verification uses `not_applicable`, not a daily collection deadline. |
| `evidence_urls` | Only valid public URLs actually linked by the page for this signal. Empty where the page has no evidence link. |
| `source_files` | Repository-relative JSON inputs supporting the signal. Resolve against the source checkout, or a snapshot's `inputs/` directory. Unsupported signals and static first-release text have no source file. |
| `percentile`, `pool_size` | Always null, each with its own reason field. No ranking or combined score is generated. |

Response A and N are independent scalar records:
`issues.answered_within_48h_90d` and `issues.response_eligible_90d`, and the
corresponding `prs.*` fields. A has `sample_size: N`. N=0 preserves A=0 and
N=0, while the median is null with `coverage: not_applicable`. Invalid A/N
pairs null both fields and the median. Incomplete response observations do
not invalidate independently covered closure or backlog values.

The map also contains release statistics, open issue/PR counts and ages,
365-day community opened/closed counts and close medians, maintainer PR
context, response exclusions, PR disposition, per-channel usage headlines,
reach/history, and published curated counts. It exports headline signals;
chart series and quotation archives stay in their existing source files.

`protocol.<source-index>.lag_days` is emitted for each verified protocol row.
Its additional `protocol` object supplies `name`, `mainnet_activation_date`,
`release_tag` and `release_date`; missing release evidence yields a null lag.
The numeric index is the entry's position in the source array, not an external
protocol identifier. With no verified rows, `protocol.lag_days` is explicitly
null with a reason. Evidence includes the page's activation, CAP and valid
supporting-release links. `production_user.<filtered-index>.stars` preserves
the verified users table's stars or null where it displays a dash. Indices
are local to the rendered collection, not stable project identifiers.
`feedback.verified_comments` includes individual `verified_dates`; it has no
fabricated shared observation timestamp.

## Relationship to the maintenance-profile proposal

This is a local serialization of the concepts in
[pg-atlas-backend issue 80](https://github.com/SCF-Public-Goods-Maintenance/pg-atlas-backend/issues/80),
not a claim of compatibility with the example fork's API schema.

- Cadence retains stable GitHub releases, the dashboard's 365-day gap rule,
  and publication timestamps; it does not deduplicate package shipping dates
  or select between registry and GitHub sources.
- Responses retain 48 hours, A/N counts, PR discussion/review comments,
  draft readiness, and the existing actor rules. Seven-day signals are null.
- Backlogs use the existing complete REST scan. Separate maintainer context
  is the published 365-day PR cohort, not new 90-day counts.
- Push recency and bot-filtered non-merge commits are null. Daily GitHub
  heatmap counts cannot stand in for either.
- Protocol lag retains verified API-support releases and mainnet dates.
- Coverage uses the dashboard vocabulary and separate freshness; stale
  retained values remain visible. No ranking pool is available.
- No declared-maintainer override or host/external-tracker exemption is
  configured. Contributor-scoped host activity is explicitly null.

## Immutable quarterly captures

The manual-only `snapshot.yml` accepts `^20[0-9]{2}-Q[1-4]$` and creates
`snapshots/<quarter>/`. The quarter is a capture label, not a calendar-quarter
aggregation. Default rolling windows are evaluated at dispatch time.

`inputs/` preserves every `<sdk>/*.json` and `curated/*.json` byte for byte,
including unverified drafts and archived text. `summary.json` contains
`schema_version`, `definition_version`, `quarter`, `created_at`, `build_commit`,
`methodology`, `inputs_sha256` (relative paths to SHA-256 hashes), and `sdks`
(key/folder/label/signals). Computation reads the frozen copies exclusively.
The two-paragraph README links the methodology at the producing commit.

`summary.csv` has one unique SDK-folder/metric row with `window`, `value`,
`coverage`, `observed_at`, `unit`, `sample_size`, `window_end`, `freshness`,
`reason`, and optional JSON `protocol` context. `value` cells are JSON
encoded: numbers are bare, missing is literal `null`, strings are quoted,
and the latest release retains its tag/date object. Other absent cells are
empty. Thus CSV values round-trip to exactly the JSON headline values.

An existing path, even an empty directory or dangling symlink, is refused
before mutation. Exclusive directory creation prevents competing local
writers. A failed new capture removes only its own partial directory. Push
retries fetch and check for the quarter on `origin/main` before rebasing;
concurrent creation cannot overwrite an upstream capture. There is no force
input. Workflow jobs publish only on `main`.

For local checks use `python3 -m dashboard.snapshot 2099-Q4 --output-root
/absolute/scratch/path`. Never run workflow commit/push steps locally.
`create_snapshot(..., now=<aware UTC datetime>)` and the builder's `NOW`/`TODAY`
allow a reproducible calculation instant in tests. Generation/creation time
remains the actual current time; `as_of` records the calculation instant.
Local dirty outputs report the current base commit plus
`working_tree_dirty: true`; they do not pretend uncommitted code exists at
that commit. A clean workflow checkout records the exact producing commit.

Each generated file is staged before output replacement. Pages receives the
page and all profiles in one successful workflow commit. The files are not a
transactional live directory API during local replacement. Export JSON/CSV
and rendered text contain no em dashes or emojis; copied source inputs keep
their original bytes, including any archived punctuation or quotes.
