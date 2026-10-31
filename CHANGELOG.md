# Changelog

## Unreleased

- Remove the offline `rebuild-3d` command, automatic schema-1/2 upgrades and separate
  3D processing checkpoints. Keep this early alpha focused on fresh imports and
  incremental sync, which generates 2D/3D geometry under one activity checkpoint.
- Reject noncurrent schema versions without changing the export. Preserve all
  elevation, geometry, metadata and source-fidelity checks for the current schema.

## 0.5.0 — 2026-10-31

- Add `activity_tracks_3d` as a derived MultiLineString Z layer while retaining
  complete 2D tracks and authoritative timestamped observations. Preserve recorded
  heights, vertical movement, missing-Z gaps, original segments and date-line splits.
- Store standard layer-scoped elevation metadata: metres, descriptive EPSG:5714 MSL
  height (assumed, not source-certified), source provenance and no transformation.
- Introduce schema 3 and resumable offline `rebuild-3d`, with per-activity derived
  checkpoints. Schema-1/2 reads stay nonmutating; write migration leaves historical
  source rows and 2D features intact. Existing source-import checkpoints are retained.
- Validate 3D dimensions, metadata, XYZ structure, spatial indexes and equality to
  stored samples; add migration/retry, vertical-line and source-fidelity regressions.

## 0.4.0 — 2026-10-31

First tagged alpha release, bringing the development milestones below together.

- Keep typed `Sample` and `Message` values from FIT/GPX/TCX decoding through
  application workflows and geographic processing. Convert to positional SQL
  rows and encode raw JSON only at the persistence boundary.
- **Breaking low-level Python helpers:** replace `sample_row` with
  `sample_from_values`; parser helpers no longer accept an activity ID, and
  `parse_archive` returns `DecodedActivity`. See the
  [import and helper migration guide](docs/architecture.md#entry-points-and-python-imports).
- Preserve the CLI, schema version 2, original archives, timestamp/coordinate/segment
  rules and persisted source payloads. Add native-payload and multi-FIT regressions.
- Include SHA-pinned Node 24 CI, weekly action updates, duplicate-run prevention,
  single-source version metadata and removal of the old root import shims.

## Development milestones — not separate tagged releases

These versions describe development snapshots, not previously published tagged
releases. They are retained as history rather than assigned invented release dates.

### 0.3.0

- Modernize CI to SHA-pinned Node 24 actions with Dependabot updates, skip duplicate
  branch/PR runs and cancel superseded runs; keep main and version-tag checks.
- Single-source distribution/runtime version metadata and remove the remaining
  `Store` naming alias in the CLI.

- **Breaking Python imports:** remove the seven root-level `api`, `datasets`,
  `formats`, `geography`, `storage`, `sync` and `validation` compatibility modules.
  Use explicit `domain`, `application` and `adapters` imports; see the
  [migration guide](docs/architecture.md#entry-points-and-python-imports).
- Migrate the synthetic example and integration tests to canonical adapters and
  application workflows; wire `GarminSource` explicitly for HTTP-backed workflows.
- Keep CLI commands/options, schema version 2, archives and geographic/data
  semantics unchanged.

### 0.2.2

- Resolve Sonar maintainability findings by separating decoding, validation,
  geographic path building, health-window retries and CLI/backfill dispatch.
- Preserve source observations, schema version 2, retry/checkpoint semantics and
  existing CLI/compatibility APIs; add focused retry and backfill regression tests.
- Improve test assertion diagnostics and isolate exception-under-test calls.
- Add a concise README, detailed usage guide, security-reporting policy and GitHub
  contribution templates; document portable optional SonarQube scanning.

### 0.2.1

- Add a shared local/CI Python quality gate with lint/format, package-wide type
  checking, security/secret scans, branch-aware coverage, strict package metadata,
  isolated installed-wheel checks and resolved-runtime vulnerability auditing.
- Harden GPX/TCX parsing with defusedxml; reject XML entity declarations as recoverable
  decode failures, and escape spatial-index metadata identifiers in validation SQL.
- Require Requests 2.33+ to exclude older releases with known security advisories.
- Add offline authentication-cache and bounded HTTP retry regression tests.
- Resolve type/lint findings while preserving the schema and geographic contract.

### 0.2.0

- Refactor into a lightweight hexagonal monolith with explicit source, repository,
  archive, decoder and progress ports and a `SyncService` application entry point.
- Introduce typed domain activities, observations, messages and date windows.
- Extract pure geography and health-normalization rules; GDAL encoding, SQL, HTTP,
  filesystem and FIT/XML dependencies now live in infrastructure adapters.
- Preserve CLI options, schema version 2 and legacy import facades.
- Add executable dependency rules and infrastructure-free application tests.

### 0.1.1

- Add `--all-data` coverage for all 26 documented GET operations, including daily
  summaries/details, individual weight measurements, women's-health endpoints,
  performed activity workouts and multisport child activities.
- Preserve multiple same-day weight measurements and independent workout checkpoints.
- Associate overnight detailed health responses with their requested day so refreshing
  one night cannot delete the following night's normalized records.
- Forward comprehensive mode to detached backfills and freeze their ending date.
- Add endpoint-reference coverage, complete-mode retry/idempotence tests and scope docs.

### 0.1.0

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

The current application schema is version 3. Only the current schema is supported
on the development branch; published releases retain their documented behaviour.
See the [schema policy](docs/schema.md#schema-policy).
