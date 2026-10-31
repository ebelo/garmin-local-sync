"""Compatibility imports for the OGR geometry adapter; new code uses the explicit layers."""

from .adapters.geometry import activity_geometries

__all__ = ["activity_geometries"]
