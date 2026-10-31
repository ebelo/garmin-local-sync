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
- Run `ruff check .`, `ruff format --check .`, and `pytest` in the project environment.
  Geography changes require a synthetic OGR round-trip and conformance validation.
- Build and smoke-test the wheel when packaging or entry points change.
- Do not publish or push unless the user explicitly authorizes that action.
