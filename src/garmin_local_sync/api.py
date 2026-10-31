"""Compatibility imports for the HTTP transport; new code uses the explicit layers."""

from .adapters.http import Api, ApiError

__all__ = ["Api", "ApiError"]
