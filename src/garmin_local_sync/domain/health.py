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


def response_day(dataset, record, window: DateWindow | None):
    start, end = (window.start, window.end) if window else (None, None)
    day = record_date(record)
    if start == end and (dataset in DETAIL_DATASETS or day is None):
        return start
    return day


def response_key(dataset, key, record, day, index, multiple):
    rid = str(day or record.get("id") or record.get("uuid") or f"{key}:{index}")
    if dataset in DETAIL_DATASETS and multiple:
        return f"{rid}:{record.get('id', index)}"
    if dataset == "wellness_activities":
        identity = record.get("activityUuid")
        if isinstance(identity, dict):
            identity = identity.get("uuid")
        return str(identity or f"{rid}:{index}")
    if dataset == "weight_entries":
        identity = record.get("id") or record.get("timestampGMT") or record.get("date")
        return f"{day}:{identity if identity is not None else index}"
    return rid


def normalize_response(dataset, key, data, window: DateWindow | None = None):
    entries = records(data)
    for index, record in enumerate(entries):
        day = response_day(dataset, record, window)
        rid = response_key(dataset, key, record, day, index, len(entries) > 1)
        yield WellnessRecord(rid, day, record, tuple(flatten_numbers(record)) if day else ())
