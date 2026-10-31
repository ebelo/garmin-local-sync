# Changelog

## 0.2.2 — unreleased

- Resolve Sonar maintainability findings by separating decoding, validation,
  geographic path building, health-window retries and CLI/backfill dispatch.
- Preserve source observations, schema version 2, retry/checkpoint semantics and
  existing CLI/compatibility APIs; add focused retry and backfill regression tests.
- Improve test assertion diagnostics and isolate exception-under-test calls.
- Add a concise README, detailed usage guide, security-reporting policy and GitHub
  contribution templates; document portable optional SonarQube scanning.

## 0.2.1 — unreleased

- Add a shared local/CI Python quality gate with lint/format, package-wide type
  checking, security/secret scans, branch-aware coverage, strict package metadata,
  isolated installed-wheel checks and resolved-runtime vulnerability auditing.
- Harden GPX/TCX parsing with defusedxml; reject XML entity declarations as recoverable
  decode failures, and escape spatial-index metadata identifiers in validation SQL.
- Require Requests 2.33+ to exclude older releases with known security advisories.
- Add offline authentication-cache and bounded HTTP retry regression tests.
- Resolve type/lint findings while preserving the schema and geographic contract.

## 0.2.0 — unreleased

- Refactor into a lightweight hexagonal monolith with explicit source, repository,
  archive, decoder and progress ports and a `SyncService` application entry point.
- Introduce typed domain activities, observations, messages and date windows.
- Extract pure geography and health-normalization rules; GDAL encoding, SQL, HTTP,
  filesystem and FIT/XML dependencies now live in infrastructure adapters.
- Preserve CLI options, schema version 2 and legacy import facades.
- Add executable dependency rules and infrastructure-free application tests.

## 0.1.1 — unreleased

- Add `--all-data` coverage for all 26 documented GET operations, including daily
  summaries/details, individual weight measurements, women's-health endpoints,
  performed activity workouts and multisport child activities.
- Preserve multiple same-day weight measurements and independent workout checkpoints.
- Associate overnight detailed health responses with their requested day so refreshing
  one night cannot delete the following night's normalized records.
- Forward comprehensive mode to detached backfills and freeze their ending date.
- Add endpoint-reference coverage, complete-mode retry/idempotence tests and scope docs.

## 0.1.0 — unreleased

Initial alpha release candidate:

- Official Garmin Personal API authentication with private key/token files,
  automatic renewal, bounded retries, and rate-limit handling.
- Resumable activity/history import into a standards-conformant GeoPackage 1.3.
- FIT/GPX/TCX decoding, explicit sample segments, preserved original files and JSON.
- EPSG:4326 tracks/start points, spatial indexes, date-line splitting, and retained
  sensor elevations without fabricated geometry heights.
- Health-summary tables, optional daily detail datasets, and local SQL views.
- Read-only status/geography/integrity checks and a detached one-off backfill runner.
- Synthetic example generator and offline tests, including independent GDAL validation.

The internal application schema is version 2. Experimental prototype schema-1
databases migrate on sync write-open; historical geometry needs `--refresh` to be
regenerated. See [schema and migration notes](docs/schema.md).
