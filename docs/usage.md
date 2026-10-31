# Usage guide

**Sync your Garmin activity and health data into a local GeoPackage, preserving
geography and time-series data for analysis.**

A small, local-first command-line tool built on the official **Garmin Connect
Personal API**. Activity tracks are real GeoPackage geometries; samples, health
records, and original responses remain available to SQL and Python. No server,
cloud database, or particular GIS application is required.

## What it stores

- All indexed activity summaries and available original FIT/GPX/TCX downloads.
- EPSG:4326 start points and segmented tracks with spatial indexes.
- Timestamped position, elevation, heart rate, speed, cadence, power, and temperature
  samples, plus original FIT fields and non-record messages.
- Daily health summaries, sleep, HRV, Body Battery, steps, stress, hydration, weight,
  weight goals, and wellness activity summaries. Detailed daily endpoints are optional.
- Complete raw JSON responses for fields that do not yet have dedicated columns.

Imports are resumable and idempotent. Recent data is refreshed, older completed
history is reused, rate limits are respected, and failures remain visible for retry.

## Requirements and installation

Initial supported platform: **Linux, including WSL**, with **Python 3.12+** and system
**GDAL 3.6+ Python bindings**. Native Windows is not supported yet: locking and the
detached runner use POSIX facilities. macOS is not currently tested.

On Ubuntu 24.04:

```bash
sudo apt-get install python3-gdal python3-venv
git clone https://github.com/ebelo/garmin-local-sync.git
cd garmin-local-sync
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
python -m pip install .
garmin-sync --help
```

Use the system Python matching `python3-gdal` to create the virtual environment.
The project deliberately does not install a possibly incompatible GDAL wheel over
your system library.

## Try it without a Garmin account

Generate a completely synthetic GeoPackage containing ordinary tracks, separated
segments, a date-line crossing, timestamped samples, and health records:

```bash
garmin-sync-example --output /tmp/garmin-example.gpkg
garmin-sync check --database /tmp/garmin-example.gpkg
garmin-sync status --database /tmp/garmin-example.gpkg
```

No account, credentials, network access, or real activity data is used.

## Set up your personal API key

Create a key in [Garmin Connect](https://connect.garmin.com/app/settings/personalApiToken).
It is shown once. On Linux, enter it without putting its value in shell history:

```bash
install -d -m 700 ~/.config/garmin
umask 077
read -r -s -p 'Garmin Personal API key: ' garmin_personal_key
printf '\n'
printf '%s\n' "$garmin_personal_key" > ~/.config/garmin/connect-personal-api-key
unset garmin_personal_key
chmod 600 ~/.config/garmin/connect-personal-api-key
```

The key is exchanged for a short-lived bearer token and cached with owner-only
permissions. Tokens renew automatically. Replace the key file when the personal
key expires or is revoked. Keys and tokens are never written into the GeoPackage.
Use `--api-key-file PATH` to select another private key file.

## Sync

```bash
# Full activity history and available health-summary history.
garmin-sync sync

# Run again to resume unfinished imports and refresh the most recent seven days.
garmin-sync sync

# Fetch every documented read endpoint, including all daily details and workouts.
garmin-sync sync --all-data

# A small live test: the latest three activities and a bounded health interval.
garmin-sync sync --activity-limit 3 --start 2026-01-01 --end 2026-01-07

# Force a chosen health interval, including time before the first activity.
garmin-sync sync --skip-activities --start 2020-01-01 --end 2020-12-31

# Include detailed daily HR/sleep/HRV/SpO2/nutrition/fitness-age responses.
garmin-sync sync --skip-activities --start 2026-01-01 --end 2026-01-07 --daily-details

# Select a subset of health datasets.
garmin-sync sync --skip-activities --datasets daily,sleep,hrv

garmin-sync status
garmin-sync check
```

`--start` and `--end` bound **health requests**, not activity history.
`--all-data` covers all 26 documented GET operations, including performed workouts,
multisport child activities, individual weight measurements and all nine daily-detail
datasets. See [API coverage and scope](api-coverage.md); daily arrays are retained
in full JSON, and a full detail backfill can take many hours.

`--activity-limit N` explicitly limits activity indexing/import. `--skip-health`
syncs only activities. `--refresh` refetches completed history, including historical
edits; `--refresh-days N` changes the recent refresh window (including today), and
`--refresh-days 0` disables automatic recent refresh while still retrying failures.

Without an explicit `--start`, daily summaries are scanned from the earliest indexed
activity. Other wearable datasets start at the first daily summary reporting
`includesWellnessData=true`; weight and weight goals keep the full interval. Supply
`--start` to disable that inference and request an exact health interval.

The default minimum request interval is one second, with bounded retries honoring
`Retry-After`. This is a conservative client default, not a claim about Garmin's quota.
`--delay SECONDS` overrides it. Dense date ranges that fail are split into smaller,
independently checkpointed requests.

### One-off detached backfill

```bash
garmin-sync-backfill start
garmin-sync-backfill start --all-data --refresh-days 0
garmin-sync-backfill status
```

The detached runner survives the terminal closing, makes one retry pass for item
errors, and runs the final database/geography check. It marks the job `complete` only
when every indexed activity is imported, no item errors remain, and checks pass.
It installs no scheduler and does not automatically restart after a reboot.
Both commands accept `--root`; the runner also accepts `--api-key-file` and `--delay`.

## Local files

| File | Default location |
| --- | --- |
| Personal API key | `~/.config/garmin/connect-personal-api-key` |
| Bearer cache | Beside the key, named `local-sync-bearer.json` |
| GeoPackage | `~/.local/share/garmin-local-sync/garmin.gpkg` |
| Original activity files | `~/.local/share/garmin-local-sync/archives/` |
| Last completed sync report | `~/.local/share/garmin-local-sync/last-sync.json` |
| Detached job log/report | `backfill.log` / `backfill-status.json` in the same data directory |

Use `--root DIR` for a different data directory or `--database FILE.gpkg` to choose
the database separately. Keep original archives beside their corresponding account's
data directory. A different Garmin account belongs in a different database/root.
Files created by the tool are owner-readable/writable only. Credentials, activity
files, and databases are excluded from this repository.

## Geographic fidelity

- Longitude is **X**, latitude is **Y**, in **EPSG:4326**. FIT semicircles are converted
  to degrees; invalid coordinate pairs become NULL rather than fabricated positions.
- GPX/TCX segment boundaries, separate archive members, FIT timer stops, missing GPS,
  and backwards timestamps break tracks. They do not add artificial sensor samples.
- Date-line crossings are split at +/-180 degrees instead of drawing a line across
  the world. Boundary vertices are interpolated only for geometry; original samples
  remain unchanged. Consecutive duplicate positions are omitted from track geometry,
  not from timestamped samples.
- Tracks are **2D MultiLineStrings**, not invented 3D paths. Elevation remains in
  `activity_samples.altitude_m`, with unknown elevation NULL. Vertical datum is not
  inferred or converted. Start locations are 2D Points.
- An activity with no route may have a valid summary start point, but no track is
  invented. A single point is not promoted into a line. Zero coordinates are valid.
- GDAL writes GeoPackage geometry and spatial indexes. `check` verifies CRS/type
  registration, validity, bounds, activity links, date-line handling, and R-tree
  envelopes in addition to SQLite integrity and foreign keys.

See [the schema](schema.md) for tables, units, sample linkage, and limitations.

## Analyse locally

```python
from pathlib import Path
import sqlite3

path = Path.home() / ".local/share/garmin-local-sync/garmin.gpkg"
with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
    rows = db.execute("""
        SELECT substr(start_time_local, 1, 7) AS month, activity_type,
               count(*) AS sessions, sum(distance_km) AS km
        FROM activity_analytics
        GROUP BY month, activity_type
        ORDER BY month DESC
    """).fetchall()
```

[More SQL examples](../analytics.sql) work without network access. Use OGR or another
GeoPackage-aware library to read geometry; the geometry column is not a bare WKB blob.

## Scope and current limits

This is an **alpha personal-data export tool**, not an official Garmin product,
clinical system, or hosted multi-user integration. Each user supplies their own
Personal API key and remains subject to Garmin's API terms. It complements rather
than replaces [Garmin's official Python client](https://github.com/garmin/connect-personal-api-python).

Historical deletions are not propagated; the local database is an archive.
Historical edits outside the refresh window require `--refresh`. FIT exercise labels
can differ from corrected Garmin Connect exercise names and must not be treated as
verified lift identities. Detailed daily endpoints are optional because they can
generate many requests. Dates before the earliest indexed activity require an
explicit `--start` to backfill health data.

Garmin can return HTTP 404 for an original file even when the activity summary still
declares that a FIT file exists. The summary remains indexed, but the activity stays
incomplete with a visible `sync_errors` entry. The tool does not invent the missing
track or silently count the original-file import as successful.

Exit codes: **0** requested work/check succeeded; **1** stopped or failed validation;
**2** recorded item errors remain; **130** interrupted. A valid partial database is
not the same as a completed history import. Re-run to resume incomplete work.

## Development

See the [contribution guide](../CONTRIBUTING.md) for environment setup, the shared
Python quality gate, optional SonarQube analysis and pull requests. The tool follows
lightweight [hexagonal architecture](architecture.md); dependencies point inward.

## References and licence

[Personal API](https://developer.garmin.com/gc-developer-program/personal/personal-api/)
· [FAQ](https://developer.garmin.com/gc-developer-program/personal/personal-faq/)
· [API reference](https://developer.garmin.com/gc-developer-program/personal/swagger-docs/)

MIT licensed; see [LICENSE](../LICENSE). Garmin is a trademark of its owner. This
project is independent and is not endorsed by Garmin.

## Offline 3D track upgrade

Version 0.5.0 adds schema-3 derived 3D tracks. To upgrade an existing export after
its current writer has stopped, back it up and run:

```sh
garmin-sync rebuild-3d --root /path/to/private/export
garmin-sync check --root /path/to/private/export
```

This uses stored samples only: no Garmin credentials, refetches or source-record
changes. It preserves complete 2D routes. Resume by repeating the command; use
`--activity-limit N` for batches or `--refresh` to regenerate completed 3D outputs.
Source imports must be complete before their derived 3D track is processed.
`pending_3d_activities` counts unprocessed completed imports; a processed activity
can legitimately have no 3D line if elevation or enough valid observations are absent.

Read-only `status` and `check` still accept older schemas without migrating them.
See [the 3D data and migration contract](schema.md#elevation-aware-tracks), including
why EPSG:5714 is descriptive, missing heights split tracks and vertical lines need
XYZ-aware validation. Do not use an old writer after upgrading to schema 3.
