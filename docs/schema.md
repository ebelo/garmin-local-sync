# Storage and geography

The output is a **GeoPackage 1.3** (SQLite application ID `GPKG`) with the required
GeoPackage metadata tables, two registered feature layers, registered attribute
tables, and R-tree indexes. The application's current schema version is **2**.

## Spatial layers

| Layer | Geometry | CRS | Link |
| --- | --- | --- | --- |
| `activity_tracks` | 2D `MultiLineString` | EPSG:4326 | `activity_id` -> `activities.activity_id` |
| `activity_starts` | 2D `Point` | EPSG:4326 | `activity_id` -> `activities.activity_id` |

Each layer has an integer feature ID, unique `activity_id`, `start_time` (activity-local
time), and `activity_type`. The activity link is validated by `garmin-sync check`;
GDAL manages geometry encoding and spatial-index triggers.

Coordinates are **longitude, latitude**, in degrees. No projected coordinates are
stored under a geographic CRS. FIT positions are converted from semicircles using
`degrees = semicircles * 180 / 2**31`. Missing, non-finite, or out-of-range pairs do
not become geometry. `(0, 0)` is a valid location and is not treated as missing.

A track may contain multiple line strings. Segments are separated by original GPX
`trkseg`/TCX `Track` boundaries, FIT timer-stop events, separate archive members,
missing GPS, or backwards sample times. Consecutive repeated positions remain in
samples but are unnecessary duplicate vertices in the line geometry. A stationary
or single-point activity may have a start point but does not get a degenerate line.

At the date line, a crossing is represented by separate lines ending/starting at
opposite +/-180-degree boundaries. Boundary latitude is linearly interpolated in
longitude/latitude coordinate space. These are derived geometry vertices, not new
sensor observations. This prevents a nearly 360-degree edge across the globe.

Elevation is deliberately separate from 2D geometry. Missing elevation is NULL,
never an invented zero. No vertical datum or geoid correction is inferred. The
original altitude value and other source fields remain available in sample JSON.

## Attribute tables

| Table | Purpose / identity |
| --- | --- |
| `activities` | One activity per integer `activity_id`, normalized totals, original summary/detail JSON, and import checkpoint |
| `activity_samples` | One original record per `(activity_id, sample_index)`; `segment_index` marks source boundaries |
| `activity_fit_messages` | Other FIT messages keyed by `(activity_id, message_index)`; includes sessions, laps, events, sets, and device fields |
| `api_responses` | Complete health/profile JSON for each `(dataset, request_key)` with requested dates and fetch timestamp |
| `wellness_records` | Normalized daily/event records identified by `(dataset, record_key)` |
| `daily_metrics` | Numeric scalar leaves identified by dataset, record key, and original JSON field path |
| `sync_errors` | Incomplete tasks with safe error descriptions and last-attempt timestamps |

Attribute tables have integer primary keys for GeoPackage interoperability. Composite
identities have unique constraints. Sample/message records have foreign keys to
activities. Sample order is the original record order, not a re-sort by timestamp.

`activity_samples` stores UTC timestamp, latitude, longitude, altitude, distance,
speed, heart rate, cadence, power, temperature, original fields as JSON, and segment
index. Sample indices are consecutive within an activity; no synthetic separator
rows are inserted. Values with no position can still carry useful sensor data.
Do not assume each sample produces one geometry vertex.

## Units and time

| Field | Unit / meaning |
| --- | --- |
| `distance_m`, `altitude_m`, elevation totals | Metres |
| `duration_s`, `moving_duration_s` | Seconds |
| `speed_mps`, `average_speed_mps` | Metres/second |
| `heart_rate_bpm` | Beats/minute |
| `cadence_rpm` | Original decoded cadence, not an assumed running steps/minute conversion |
| `power_w` | Watts |
| `temperature_c` | Degrees Celsius |
| FIT set weights in original message JSON | Decoded FIT kilograms; exercise identity still needs verification |
| Weight-range API responses | Observed raw gram values; `daily_analytics.weight_kg` divides by 1,000 |

FIT timestamps are UTC by the FIT format's convention. Zoned XML timestamps are
normalized to UTC with an explicit offset. XML timestamps lacking a timezone are
retained as `timestamp_unzoned` in raw JSON and do not populate `timestamp_utc`.
Activity-local and GMT summary timestamps are kept in separate columns; the original
Garmin summary remains authoritative. Numeric non-finite sample values become NULL
with a diagnostic in `_nonfinite_values` inside the sample's raw JSON.

Arrays stay in raw JSON. `daily_metrics` exposes scalar numeric leaves, not expanded
time-series arrays or Boolean flags. Original downloads and responses are retained
so additional normalizations can be added without another Garmin fetch.

## Views

- `activity_analytics`: activity totals in km/hours and average speed in km/h.
- `daily_analytics`: daily steps, resting heart rate, stress, Body Battery bounds,
  sleep duration/score, overnight HRV, and weight in kg. Missing data stays NULL.

The archive preserves Garmin's reported values and possible sentinels. It does not
infer effort, maximum strength, calories consumed, or missing observations. In
particular, FIT exercise-category candidates may differ from corrected Garmin
Connect exercise names; neither a first set nor a watch classification is a
standardized strength test.

## Checkpoints, refresh, and migration

An activity's completion timestamp is cleared before refreshing its geometry. OGR
commits geometry and indexes; a subsequent SQLite transaction replaces samples and
messages and writes the completed checkpoint. If interrupted between commits, the
activity remains incomplete and is replaced on the next run. Previous sample rows
can remain until that replacement succeeds. For analyses requiring a fully imported
snapshot, join to `activities` and require `samples_synced_at IS NOT NULL`.

Health checkpoints are committed only after successful responses, including HTTP
204 (no data). Overlapping date windows replace normalized records in that interval;
full request responses remain archived by request key. Failed windows are retried.

Experimental schema-1 databases from the original prototype are recognized. A sync
write-open adds `segment_index` with default zero and changes the schema version to
2. Read-only `status` and `check` do not migrate or modify a database. Unknown schema
versions and unrelated SQLite/GeoPackage files are rejected before modification.
Back up a prototype database before its first upgrade. Existing old geometries are
not silently rewritten; use `sync --refresh` to re-decode their original archives
with the new segment/date-line rules. Do not run old and new writers together.
