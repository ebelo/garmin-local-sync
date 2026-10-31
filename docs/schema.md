# Storage and geography

The output is a **GeoPackage 1.3** (SQLite application ID `GPKG`) with the required
GeoPackage metadata tables, three registered feature layers, registered attribute
tables, and R-tree indexes. The application's current schema version is **3**.

## Spatial layers

| Layer | Geometry | CRS | Link |
| --- | --- | --- | --- |
| `activity_tracks` | 2D `MultiLineString` | EPSG:4326 | `activity_id` -> `activities.activity_id` |
| `activity_starts` | 2D `Point` | EPSG:4326 | `activity_id` -> `activities.activity_id` |
| `activity_tracks_3d` | `MultiLineString Z` (mandatory Z, no M) | EPSG:4326 horizontal; descriptive EPSG:5714 vertical metadata | `activity_id` -> `activities.activity_id` |

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

### Elevation-aware tracks

`activity_tracks` remains the complete horizontal representation; `activity_tracks_3d`
is derived independently from the same observations. Recorded `altitude_m` supplies
Z in metres without a geoid correction or other vertical transformation. Missing,
non-numeric or non-finite elevation breaks a 3D segment; it never becomes an invented
zero and gaps are not interpolated. Original values and diagnostics remain in raw
sample JSON. Isolated observations do not become degenerate line strings.

Repeated XYZ vertices are unnecessary duplicates, but a change in Z at the same XY
is preserved, including purely vertical segments. Original segment boundaries,
missing GPS and backwards times also split 3D tracks. At date-line boundaries only,
Z is linearly interpolated between the two source heights for derived geometry
vertices. Such boundary vertices are not added to the sample table.

The 3D layer carries table-scoped metadata through the standard `gpkg_metadata` /
`gpkg_metadata_reference` extension, written and read by GDAL:

| Metadata | Meaning |
|---|---|
| Horizontal CRS | EPSG:4326; longitude=X, latitude=Y |
| Elevation unit | Metres |
| Descriptive vertical CRS | **EPSG:5714, MSL height** |
| Assignment basis | Assumed/descriptive MSL interpretation, **not source-certified** |
| Exact source vertical datum | Not identified by the source fields |
| Vertical transformation | None; recorded heights are preserved |
| Provenance | `activity_samples.altitude_m`, raw sample JSON and retained originals |

Garmin describes watch elevation as above/below sea level. That does not identify a
particular geoid model for every imported file. EPSG:5714 is a descriptive reference,
not a claim that the producer certified a vertical datum or that heights were
transformed. Do not relabel the geometry EPSG:4979 (WGS84 ellipsoidal height).
FIT uses `enhanced_altitude` when present, otherwise `altitude`; GPX `ele` and TCX
`AltitudeMeters` are normalized into the same sample column. GPS versus barometric
or corrected measurement source is not guessed.

GeoPackage R-trees index XY envelopes, not heights. `garmin-sync check` validates
3D dimensions, finite/distinct XYZ vertices, date-line segmentation, metadata,
spatial indexes and source-to-geometry equality for completed 3D activities. Purely
vertical lines are validated in XYZ: GEOS `IsValid` alone is XY-only and may reject
them despite their valid 3D structure. Independent GeoPackage conformance is tested.


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

## Checkpoints and refresh

An activity's completion timestamp is cleared before refreshing its geometry. OGR
commits geometry and indexes; a subsequent SQLite transaction replaces samples and
messages and writes the completed checkpoint. If interrupted between commits, the
activity remains incomplete and is replaced on the next run. Previous sample rows
can remain until that replacement succeeds. For analyses requiring a fully imported
snapshot, join to `activities` and require `samples_synced_at IS NOT NULL`.

The same activity-import checkpoint covers samples, messages and all 2D/3D geometry.
There is no separate derived-geometry checkpoint or rebuild workflow. A completed
activity can legitimately have no 3D line: manual activities, isolated points and
missing-Z paths may not contain enough usable XYZ observations.

Health checkpoints are committed only after successful responses, including HTTP
204 (no data). Overlapping date windows replace normalized records in that interval;
full request responses remain archived by request key. Failed windows are retried.

### Schema policy

Normal sync creates both 2D and 3D layers and refreshes them together. This early
alpha does not maintain automatic upgrades from prototype schemas. For a fresh
import after a schema change, select a new export directory with `--root`; retain
the old export separately. Never run different writers against the same database.
Unrelated SQLite/GeoPackage files are also rejected before modification.
