"""Compatibility imports for the GeoPackage validation; new code uses the explicit layers."""

from .adapters.validation import check_store

__all__ = ["check_store"]
