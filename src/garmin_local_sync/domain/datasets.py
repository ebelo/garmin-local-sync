"""Dataset semantics and window policy; vendor request paths belong to adapters."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Dataset:
    name: str
    daily_detail: bool = False
    window_days: int = 28
    discover_from_wellness: bool = True


RANGE_DATASETS = {
    item.name: item
    for item in (
        Dataset("daily", window_days=183, discover_from_wellness=False),
        Dataset("sleep"),
        Dataset("hrv", window_days=365),
        Dataset("body_battery"),
        Dataset("steps"),
        Dataset("stress"),
        Dataset("hydration"),
        Dataset("weight", window_days=365, discover_from_wellness=False),
        Dataset("weight_entries", window_days=365, discover_from_wellness=False),
        Dataset("weight_goals", window_days=365, discover_from_wellness=False),
        Dataset("wellness_activities", window_days=365),
    )
}
DETAIL_DATASETS = {
    name: Dataset(name, daily_detail=True, window_days=1)
    for name in (
        "heart_rate",
        "sleep_detail",
        "hrv_detail",
        "spo2",
        "food_logs",
        "fitness_age",
        "daily_detail",
        "menstrual_summary",
        "menstrual_day",
    )
}
DATASETS = RANGE_DATASETS | DETAIL_DATASETS
