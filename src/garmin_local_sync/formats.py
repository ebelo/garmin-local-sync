"""Compatibility imports for the activity decoding; new code uses the explicit layers."""

from .adapters.decoding import parse_archive, parse_fit, parse_xml, sample_row
from .domain.geography import valid_coords

__all__ = ["parse_archive", "parse_fit", "parse_xml", "sample_row", "valid_coords"]
