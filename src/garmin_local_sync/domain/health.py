"""Pure daily-record identity and numeric normalization rules."""

import contextlib
import datetime as dt
import math

from .datasets import DETAIL_DATASETS
from .models import DateWindow, WellnessRecord


def records(data):
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for key in ["data", "hrvSummaries", "dailyWeightSummaries", "summaries"]:
            if isinstance(data.get(key), list):
                return [x for x in data[key] if isinstance(x, dict)]
        return [data]
    return []


def record_date(record):
    for key in ["calendarDate", "summaryDate", "date", "startTimestampLocal"]:
        value = record.get(key)
        if isinstance(value, str) and len(value) >= 10:
            with contextlib.suppress(ValueError):
                return dt.date.fromisoformat(value[:10]).isoformat()
    return None


def flatten_numbers(value, prefix=""):
    # Arrays remain intact in raw JSON; scalar leaves are convenient SQL metrics.
    if isinstance(value, dict):
        for key, child in value.items():
            yield from flatten_numbers(child, f"{prefix}.{key}" if prefix else key)
    elif isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        yield prefix, value


def normalize_response(dataset, key, data, window: DateWindow | None = None):
    start, end = (window.start, window.end) if window else (None, None)
    entries = records(data)
    for index, record in enumerate(entries):
        day = record_date(record)
        if dataset in DETAIL_DATASETS and start == end:
            day = start
        if day is None and start == end:
            day = start
        rid = str(day or record.get("id") or record.get("uuid") or f"{key}:{index}")
        if dataset in DETAIL_DATASETS and len(entries) > 1:
            rid = f"{rid}:{record.get('id', index)}"
        if dataset == "wellness_activities":
            identity = record.get("activityUuid")
            if isinstance(identity, dict):
                identity = identity.get("uuid")
            rid = str(identity or f"{rid}:{index}")
        if dataset == "weight_entries":
            identity = record.get("id") or record.get("timestampGMT") or record.get("date")
            rid = f"{day}:{identity if identity is not None else index}"
        yield WellnessRecord(rid, day, record, tuple(flatten_numbers(record)) if day else ())
