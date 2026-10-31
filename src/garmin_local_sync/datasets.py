"""Compatibility imports for the Garmin endpoint catalog; new code uses the explicit layers."""

from .adapters.garmin_routes import DETAIL_DATASETS, DETAIL_PARAMS, RANGE_DATASETS
from .domain.datasets import RANGE_DATASETS as POLICIES

RANGE_DAYS = {name: spec.window_days for name, spec in POLICIES.items() if spec.window_days != 28}

__all__ = ["DETAIL_DATASETS", "DETAIL_PARAMS", "RANGE_DATASETS", "RANGE_DAYS"]
