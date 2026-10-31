# Contributor and agent guidance

- This is a Garmin Personal API -> GeoPackage pipeline, independent of any particular
  desktop GIS application. Geographic fidelity and useful local analytics are core.
- Use GDAL/OGR for geometry reads/writes and spatial-index maintenance. Plain SQLite
  is for nonspatial attributes. Longitude is X; latitude is Y; CRS is EPSG:4326.
- Retain original samples, elevations, timestamps, segment boundaries, and source
  payloads. Never fabricate a zero elevation or bridge gaps/date-line discontinuities.
- Never commit credentials, token caches, original personal activity files, database
  exports, or identifiable real-user fixtures. Tests/examples must be synthetic.
- FIT exercise-category candidates are not verified corrected exercise names. Avoid
  strength claims based on ambiguous movement labels or cross-equipment loads.
- Preserve idempotence, checkpoint honesty, bounded retries, and one-writer locking.
- Keep typed Sample/Message values through decoding and geometry; positional rows
  and JSON encoding belong only at the SQL persistence boundary.
- Keep dependency direction inward: domain uses only the standard library;
  application imports domain and its own ports, never adapters or root-level helpers.
  HTTP paths, SQL, filesystem operations and GDAL objects belong to adapters.
- Add workflows through `application.sync.SyncService` and the ports. Import concrete
  adapters from `adapters/`; do not reintroduce root-level compatibility shims. Test
  core policy with in-memory ports and adapters with integration/geography tests.
- Run `ruff check .`, `ruff format --check .`, and `pytest` in the project environment.
- Before release-ready claims, run `python scripts/check_quality.py` for the shared
  local/CI gate: lint/format, types, security/secrets, coverage, packaging, installed
  CLI/GeoPackage conformance and resolved-runtime dependency audit. Keep reports ignored.
  Geography changes require a synthetic OGR round-trip and conformance validation.
- Build and smoke-test the wheel when packaging or entry points change.
- Do not publish or push unless the user explicitly authorizes that action.
