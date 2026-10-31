"""Vendor/legacy representations converted at infrastructure boundaries."""

import json
from typing import Any

from ..domain.models import ActivitySummary, Message, Sample
from ..util import dumps

SUMMARY_FIELDS = {
    "activity_id": "activityId",
    "name": "activityName",
    "start_time_utc": "startTimeGMT",
    "start_time_local": "startTimeLocal",
    "distance_m": "distance",
    "duration_s": "duration",
    "moving_duration_s": "movingDuration",
    "elevation_gain_m": "elevationGain",
    "elevation_loss_m": "elevationLoss",
    "average_hr": "averageHR",
    "max_hr": "maxHR",
    "average_speed_mps": "averageSpeed",
    "calories": "calories",
    "training_load": "activityTrainingLoad",
    "aerobic_training_effect": "aerobicTrainingEffect",
    "anaerobic_training_effect": "anaerobicTrainingEffect",
    "start_latitude": "startLatitude",
    "start_longitude": "startLongitude",
    "owner_id": "ownerId",
}


def summary_from_payload(payload: dict) -> ActivitySummary:
    values: dict[str, Any] = {field: payload.get(key) for field, key in SUMMARY_FIELDS.items()}
    return ActivitySummary(
        **values,
        activity_type=payload.get("activityType", {}).get("typeKey"),
        raw=payload,
    )


def sample_from_row(row) -> Sample:
    return Sample(
        index=row[1],
        timestamp_utc=row[2],
        latitude=row[3],
        longitude=row[4],
        altitude_m=row[5],
        distance_m=row[6],
        speed_mps=row[7],
        heart_rate_bpm=row[8],
        cadence_rpm=row[9],
        power_w=row[10],
        temperature_c=row[11],
        raw=json.loads(row[12]),
        segment_index=row[13] if len(row) > 13 else 0,
    )


def sample_to_row(sample: Sample, activity_id: int):
    return (
        activity_id,
        sample.index,
        sample.timestamp_utc,
        sample.latitude,
        sample.longitude,
        sample.altitude_m,
        sample.distance_m,
        sample.speed_mps,
        sample.heart_rate_bpm,
        sample.cadence_rpm,
        sample.power_w,
        sample.temperature_c,
        dumps(sample.raw),
        sample.segment_index,
    )


def message_from_row(row) -> Message:
    return Message(row[1], row[2], json.loads(row[3]))
