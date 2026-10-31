"""Compatibility imports for the GeoPackage adapter; new code uses the explicit layers."""

from .adapters.geopackage import (
    SCHEMA,
    GeoPackageRepository,
    status,
)
from .domain.health import flatten_numbers, record_date, records

Store = GeoPackageRepository
__all__ = ["SCHEMA", "GeoPackageRepository", "Store", "flatten_numbers", "record_date", "records", "status"]
