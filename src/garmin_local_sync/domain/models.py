"""Data crossing the application boundary; no HTTP, SQL, filesystem or GDAL types."""

from dataclasses import dataclass, field
from datetime import date
from typing import Any

type Payload = dict[str, Any]
type JsonValue = Any


@dataclass(frozen=True)
class ActivitySummary:
    activity_id: int
    name: str | None = None
    activity_type: str | None = None
    start_time_utc: str | None = None
    start_time_local: str | None = None
    distance_m: float | None = None
    duration_s: float | None = None
    moving_duration_s: float | None = None
    elevation_gain_m: float | None = None
    elevation_loss_m: float | None = None
    average_hr: float | None = None
    max_hr: float | None = None
    average_speed_mps: float | None = None
    calories: float | None = None
    training_load: float | None = None
    aerobic_training_effect: float | None = None
    anaerobic_training_effect: float | None = None
    start_latitude: float | None = None
    start_longitude: float | None = None
    owner_id: int | None = None
    raw: Payload = field(default_factory=dict)


@dataclass(frozen=True)
class ActivityState:
    activity_id: int
    start_time_local: str | None
    imported: bool
    failed: bool = False


@dataclass(frozen=True)
class ActivityDetail:
    raw: Payload
    manual: bool = False


@dataclass(frozen=True)
class Sample:
    index: int
    timestamp_utc: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    altitude_m: float | None = None
    distance_m: float | None = None
    speed_mps: float | None = None
    heart_rate_bpm: float | None = None
    cadence_rpm: float | None = None
    power_w: float | None = None
    temperature_c: float | None = None
    raw: Payload = field(default_factory=dict)
    segment_index: int = 0


@dataclass(frozen=True)
class Message:
    index: int
    message_type: str
    raw: Payload


@dataclass(frozen=True)
class DecodedActivity:
    samples: tuple[Sample, ...] = ()
    messages: tuple[Message, ...] = ()
    source_format: str = "manual"


@dataclass(frozen=True)
class DateWindow:
    start: str
    end: str

    @property
    def key(self) -> str:
        return f"{self.start}/{self.end}"


@dataclass(frozen=True)
class WellnessRecord:
    record_key: str
    calendar_date: str | None
    raw: Payload
    metrics: tuple[tuple[str, float], ...]


@dataclass(frozen=True)
class Coordinate:
    longitude: float
    latitude: float
    altitude_m: float | None = None


@dataclass(frozen=True)
class ActivityGeography:
    segments: tuple[tuple[Coordinate, ...], ...]
    start: Coordinate | None


@dataclass(frozen=True)
class SyncOptions:
    end: date
    start: date | None = None
    activity_limit: int | None = None
    skip_activities: bool = False
    skip_health: bool = False
    all_data: bool = False
    activity_workouts: bool = False
    daily_details: bool = False
    datasets: str | None = None
    refresh_days: int = 7
    refresh: bool = False

    def __post_init__(self):
        if self.activity_limit is not None and self.activity_limit <= 0:
            raise ValueError("Activity limit must be positive")
        if self.refresh_days < 0:
            raise ValueError("Refresh days cannot be negative")
        if self.start is not None and self.start > self.end:
            raise ValueError("Start date must be on/before end date")
        if self.all_data and self.datasets:
            raise ValueError("All-data mode cannot select a dataset subset")
