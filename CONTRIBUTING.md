# Contributing

Keep the project focused: an application-neutral Garmin Personal API -> GeoPackage
pipeline with faithful geography, useful local tables, and reliable synchronization.

## Environment

Use Python 3.12+ with matching system GDAL bindings, then:

```bash
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
ruff check .
ruff format --check .
pytest
python -m build
```

All tests must run without a Garmin key or network access. Geographic changes need
OGR round-trip assertions and GDAL GeoPackage conformance validation. The synthetic
example generator is the preferred reproducible fixture, not an account export.

## Useful contributions

- Better handling of documented API responses and recoverable failures.
- Geographic edge cases that preserve original records and coordinate semantics.
- Carefully documented units, migrations, and SQL/Python examples.
- Installation fixes and additional independently tested platforms.

Avoid features tied to a particular GIS application, hosted multi-user account
integrations, and analysis claims that go beyond the recorded data.

## Reports and pull requests

Include the Python/GDAL/platform versions, command, expected result, and a minimal
synthetic reproduction. Do not attach API keys, bearer tokens, real GeoPackages,
original personal FIT files, health data, home-location tracks, or unredacted logs.
Preserve idempotence and visibly incomplete checkpoints when addressing errors.

Contributions are licensed under the project's MIT licence.
