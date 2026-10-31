# Architecture

The tool is a lightweight **hexagonal monolith**: one Python distribution, with
inward dependencies and replaceable infrastructure. It is not a collection of
services, a plugin framework, or a dependency-injection container.

## Dependency direction

```mermaid
flowchart TB
    CLI["CLI / composition root"] --> APP["Application: SyncService"]
    CLI --> ADAPTERS["Infrastructure adapters"]
    BACKFILL["Backfill driving adapter"] --> CLI
    APP --> PORTS["Application-owned ports"]
    APP --> DOMAIN["Domain values and rules"]
    ADAPTERS -. implement .-> PORTS
    ADAPTERS --> DOMAIN
```

Arrows describe dependencies, not the direction of downloaded bytes. The
application calls ports; concrete implementations are supplied by the composition
root. Neither the domain nor the application imports adapter implementations.

## Inner layers

`domain/` contains standard-library-only code:

- `models.py`: typed activities, import state, observations, messages, date windows,
  geographic values, normalized health records and sync options. Original payloads
  remain attached so normalization does not discard fields.
- `geography.py`: segment/gap/time rules, repeated-point handling and date-line
  splitting. Results are coordinate values, not OGR objects.
- `health.py`: daily record identity and scalar numeric normalization, including
  requested-day identity for overnight data and multiple same-day weight entries.
- `datasets.py`: dataset semantics, history discovery and request-window policy;
  it contains no service URLs or HTTP parameter mappings.
- `errors.py`: source failure categories and decoding errors, independent of
  HTTP status codes and third-party exception types.

`application/sync.py` supplies the inbound `SyncService.run(SyncOptions)` use case,
plus reusable workflows for indexing, activity imports, workouts and health data.
It owns refresh decisions, resumption, interval coverage, range splitting and
honest failure/completion policy. It does not access SQL connections, local paths,
transport methods, FIT libraries or GDAL objects. Time-sensitive policies accept an
injected date; progress is an injected callable.

## Outbound ports and adapters

Contracts are structural Python `Protocol` interfaces in `application/ports.py`.

| Port | Adapter | Responsibility |
| --- | --- | --- |
| `ActivitySource` | `adapters.garmin.GarminSource` | Typed summaries/details, originals, workouts, health windows and account context |
| `SyncRepository` | `adapters.geopackage.GeoPackageRepository` | Atomic summaries/responses, import checkpoints, query state and reports |
| `ArchiveStore` | `adapters.filesystem.FileArchiveStore` | Private original-archive reads and atomic writes |
| `ActivityDecoder` | `adapters.decoding.OriginalActivityDecoder` | FIT/GPX/TCX decoding into domain samples/messages |
| `Progress` | CLI callback or an in-memory collector | Human-readable progress without coupling workflows to a terminal |

Supporting adapters:

- `http.py` handles credentials, token renewal, HTTPS, throttling and retries.
- `garmin_routes.py` holds vendor paths and parameters. `GarminSource` translates
  transport errors into domain failure categories and maps source summaries.
- `mapping.py` converts vendor and legacy storage representations at boundaries.
- `geometry.py` encodes pure geographic values as OGR geometry. GDAL remains
  responsible for GeoPackage geometry blobs and spatial indexes.
- `validation.py` performs infrastructure-specific SQLite/OGR conformance checks.

The GeoPackage adapter invokes pure normalization/geography rules but owns all SQL,
transactions, schema migration, OGR calls and the interrupted-refresh checkpoint
protocol. It does not expose its SQLite connection through the repository port.

## Driving adapters and composition

`cli.py` validates arguments, acquires the per-database lock, creates adapters,
authenticates, wires `SyncService`, writes the private completion artifact and
presents status/errors. Read-only status and geographic inspection are maintenance
operations implemented by the infrastructure adapters.

`backfill.py` supervises CLI subprocesses using the same installed package, records
its frozen mode/date, retries incomplete work and checks the result. The application
does not know about subprocesses. Resource lifetimes and single-writer locking are
the responsibility of driving adapters; library callers must preserve that contract.

## Compatibility

The CLI flags, original archive layout and application schema **2** are unchanged.
Root modules `api`, `formats`, `geography`, `storage`, `datasets`, `validation` and
`sync` retain compatibility facades. Existing callers using `Store` or tuple-shaped
parser results continue to work; new code should use typed domain values and the
explicit layers. Compatibility facades delegate rather than duplicate workflows.

## Verification

- `tests/test_architecture.py` rejects outward dependencies and verifies core import
  and geographic execution with third-party infrastructure imports blocked.
- `tests/test_application.py` exercises application policy using only in-memory
  ports, including retries, no-data checkpoints, manual activities and idempotence.
- Existing integration tests retain HTTP, CLI, GeoPackage, fault-injection,
  normalization and OGR round-trip coverage.
- Geographic changes require independent GDAL conformance validation; distribution
  changes require an installed-wheel test outside the source checkout.

Keep a running job pinned to its installed distribution. Build/test newer code in a
separate environment; do not replace a backfill's interpreter or package mid-run.
