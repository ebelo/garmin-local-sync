## Change and reason

Describe the change and link the relevant issue, if any.

## Verification

List the checks run and their results. Code changes should pass
`python scripts/check_quality.py`; geographic changes also need meaningful OGR
round-trip and independent GDAL conformance evidence.

- [ ] Inputs and fixtures are synthetic; no credentials or personal exports included.
- [ ] Geography, units, checkpoints and compatibility are preserved or changes documented.
- [ ] Documentation/changelog updated where needed.
