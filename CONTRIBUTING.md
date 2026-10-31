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
python scripts/check_quality.py
```

All tests must run without a Garmin key or network access. Geographic changes need
OGR round-trip assertions and GDAL GeoPackage conformance validation. The synthetic
example generator is the preferred reproducible fixture, not an account export.

## Quality gate

`python scripts/check_quality.py` runs the same gate locally and in GitHub Actions:

| Check | Purpose |
|---|---|
| Ruff lint | Invalid Python, undefined names, unused/import ordering, common bugs and modern Python conventions |
| Ruff format | Consistent automatic code formatting |
| mypy | Type errors across all package modules, including bodies of unannotated functions |
| Bandit | Security-sensitive patterns in production Python |
| detect-secrets | Secret candidates in tracked and non-ignored new files; no network verification |
| pytest + coverage | Offline unit, application, adapter, architecture and geography tests; 85% minimum branch-aware coverage |
| Build + Twine | Wheel/sdist construction and strict distribution metadata/README checks |
| Installed-wheel smoke | Imports and CLI entry points outside the checkout, using a fresh environment |
| SQLite/OGR + GDAL | Synthetic database integrity, geographic round-trips and independent strict GeoPackage conformance |
| pip-audit | Known vulnerabilities in the freshly resolved runtime Python dependencies |

Reports, exact tool versions, resolved runtime requirements, distributions and the
synthetic GeoPackage go to ignored `reports/quality/`; use `--reports-dir PATH` to
retain a run elsewhere. The full gate needs package-index/advisory-service access
for building, installing and auditing. It never uses Garmin credentials or real data.

Type checking is **not** strict typing: vendor JSON intentionally permits `Any`, and
GDAL/fitdecode lack usable type stubs. Their missing stubs alone are exempted; no
package modules are excluded. The coverage percentage combines statement and branch
opportunities; subprocess smoke tests are additional checks, not counted coverage.
Bandit suppressions are narrow, documented reviews of fixed SQL identifiers, escaped
metadata identifiers, fixed-interpreter argv subprocesses and a public token URL;
never add blanket exclusions to silence genuine findings.

The audit excludes this unpublished package itself, the development tools and
system-managed GDAL. It records the runtime versions actually installed for the wheel;
it does not certify every version allowed by dependency ranges or the whole host.
Upgrade OS/GDAL packages through the platform package manager separately.

QFit's QGIS/Qt runtime matrix does not apply to this application-neutral tool. Hosted
SonarCloud analysis is not run by this local gate and is not configured until a hosted
repository/service setup exists. Ruff replaces overlapping Flake8/import/style checks.

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
