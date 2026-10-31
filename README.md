# garmin-local-sync

**Your Garmin activities and health data in a local GeoPackage, ready for SQL and Python.**

A local-first CLI built on the official Garmin Connect Personal API. It preserves
geography, timestamped observations and raw responses without requiring a server,
cloud database or particular GIS application.

## What you get

- Activity summaries and available original FIT/GPX/TCX files.
- EPSG:4326 tracks and start points with spatial indexes: longitude=X, latitude=Y.
- Segmented 2D tracks linked to timestamped samples; elevation stays in nullable
  attributes rather than becoming invented geometry heights.
- Health summaries and optional detailed daily data, with complete raw JSON retained.
- Resumable, incremental imports, bounded retries and visible incomplete items.

## Install and try it

Supported initially on **Linux/WSL**, with **Python 3.12+** and matching system
**GDAL 3.6+** bindings. Native Windows is not supported; macOS is untested.

On Ubuntu 24.04:

```bash
sudo apt-get install python3-gdal python3-venv
git clone https://github.com/ebelo/garmin-local-sync.git
cd garmin-local-sync
/usr/bin/python3 -m venv --system-site-packages .venv
source .venv/bin/activate
python -m pip install .

# Create and validate a completely synthetic database—no account or network needed.
garmin-sync-example --output /tmp/garmin-example.gpkg
garmin-sync check --database /tmp/garmin-example.gpkg
```

Create the environment with the system Python matching your GDAL installation.

## Sync your data

[Create and securely store your personal API key](docs/usage.md#set-up-your-personal-api-key),
then run:

```bash
garmin-sync sync              # activities and health summaries
garmin-sync sync --all-data   # all supported read endpoints, including daily details
garmin-sync status
garmin-sync check
```

Run again to resume or update. Data defaults to
`~/.local/share/garmin-local-sync/`; use `--root DIR` for another location.
Full daily-detail history can take hours. `--start`/`--end` limit health requests,
not activity history.

This is an **alpha**, independent project, not endorsed by Garmin. Each user needs
their own Personal API key and must follow Garmin's API terms. Missing original
files remain flagged as incomplete; historical deletions are not propagated.
Never publish real databases, activity files, health records or credentials.

## Documentation

- [Usage, credentials, refresh options and limitations](docs/usage.md)
- [Schema, units and geographic fidelity](docs/schema.md)
- [API coverage](docs/api-coverage.md) · [SQL examples](analytics.sql)
- [Architecture](docs/architecture.md) · [Changelog](CHANGELOG.md)
- [Contributing and quality checks](CONTRIBUTING.md) · [Security reporting](SECURITY.md)

For development, install `python -m pip install -e '.[dev]'` and run
`python scripts/check_quality.py`. Tests and examples use synthetic data only.

## Licence

[MIT](LICENSE). Copyright 2026 Emmanuel Belo. Garmin is a trademark of its owner.

[Garmin Personal API documentation](https://developer.garmin.com/gc-developer-program/personal/personal-api/)
