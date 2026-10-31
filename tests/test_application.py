"""Business workflows exercised entirely through in-memory ports."""

import datetime as dt

import pytest

from garmin_local_sync.application.sync import SyncService, sync_activities, sync_wellness
from garmin_local_sync.domain.datasets import DATASETS
from garmin_local_sync.domain.errors import FailureKind, SourceError
from garmin_local_sync.domain.geography import build_geography
from garmin_local_sync.domain.health import normalize_response
from garmin_local_sync.domain.models import (
    ActivityDetail,
    ActivityState,
    ActivitySummary,
    DateWindow,
    DecodedActivity,
    Sample,
    SyncOptions,
)


class MemoryRepository:
    def __init__(self):
        self.summaries = {}
        self.imports = {}
        self.responses = {}
        self.failures = {}

    def save_summaries(self, summaries):
        self.summaries.update({s.activity_id: s for s in summaries})

    def activity_states(self, limit=None):
        result = [
            ActivityState(
                aid, summary.start_time_local, aid in self.imports, f"activity:{aid}" in self.failures
            )
            for aid, summary in sorted(self.summaries.items(), reverse=True)
        ]
        return result[:limit] if limit is not None else result

    def save_import(self, activity_id, detail, decoded):
        self.imports[activity_id] = decoded
        self.clear_failure(f"activity:{activity_id}")

    def earliest_activity_date(self):
        return dt.date(2020, 1, 1)

    def first_wellness_date(self):
        return dt.date(2020, 1, 1)

    def response_windows(self, dataset, start, end):
        return sorted(
            [
                window
                for (name, _), (_, window) in self.responses.items()
                if name == dataset and window and window.start <= end and window.end >= start
            ],
            key=lambda w: w.start,
        )

    def has_response(self, dataset, key):
        return (dataset, key) in self.responses

    def save_response(self, dataset, key, data, window=None):
        self.responses[dataset, key] = data, window
        self.clear_failure(f"{dataset}:{key}")

    def has_failure(self, task):
        return task in self.failures

    def record_failure(self, task, error):
        self.failures[task] = error

    def clear_failure(self, task):
        self.failures.pop(task, None)

    def snapshot(self):
        return {
            "activities": len(self.summaries),
            "activities_imported": len(self.imports),
            "sync_errors": len(self.failures),
        }


class MemoryArchives:
    def __init__(self):
        self.contents = {}

    def read(self, activity_id):
        return self.contents.get(activity_id)

    def write(self, activity_id, content):
        self.contents[activity_id] = content


class Decoder:
    def decode(self, content, activity_id):
        assert content == b"original observations"
        return DecodedActivity(
            (Sample(0, latitude=10, longitude=20, raw={"retained": True}),), (), "synthetic"
        )


class MemorySource:
    def __init__(self):
        self.missing = True
        self.downloads = []
        self.health_requests = []
        self.workout_requests = []
        self.children = []

    def list_activities(self, offset, limit, include_children):
        self.children.append(include_children)
        return [ActivitySummary(aid, start_time_local="2020-01-01 10:00:00") for aid in (1, 2)]

    def activity_detail(self, activity_id):
        return ActivityDetail({"retained": True})

    def download_activity(self, activity_id):
        self.downloads.append(activity_id)
        if activity_id == 2 and self.missing:
            raise SourceError(FailureKind.NOT_FOUND, "Original unavailable")
        return b"original observations"

    def activity_workouts(self, activity_id):
        self.workout_requests.append(activity_id)
        return []

    def health(self, dataset, window):
        self.health_requests.append((dataset, window))
        return None

    def context(self):
        yield "profile", {"synthetic": True}
        yield "heart_rate_zones", []


def test_comprehensive_use_case_retries_only_missing_items_then_becomes_idempotent():
    source, repo, archives = MemorySource(), MemoryRepository(), MemoryArchives()
    service = SyncService(source, repo, archives, Decoder(), today=dt.date(2020, 1, 1))
    options = SyncOptions(start=dt.date(2020, 1, 1), end=dt.date(2020, 1, 1), all_data=True, refresh_days=0)
    assert service.run(options) == {"activities": 2, "activities_imported": 1, "sync_errors": 1}
    assert {name for name, _ in source.health_requests} == DATASETS.keys()
    assert len(source.health_requests) == 20
    assert source.children == [True]
    assert len(source.workout_requests) == 2  # Missing original does not hide performed workouts.
    source.missing = False
    assert service.run(options)["sync_errors"] == 0
    assert service.run(options)["activities_imported"] == 2
    assert sorted(source.downloads) == [1, 2, 2]
    assert len(source.health_requests) == 20  # Successful no-data is checkpointed too.
    assert len(source.workout_requests) == 2
    assert repo.imports[1].samples[0].raw == {"retained": True}


def test_failed_range_splits_then_reuses_successful_subrange_coverage():
    repo = MemoryRepository()

    class Source(MemorySource):
        def health(self, dataset, window):
            self.health_requests.append((dataset, window))
            if window.start != window.end:
                raise SourceError(FailureKind.TRANSIENT, "Range too dense")
            return {"calendarDate": window.start}

    source = Source()
    start, end = dt.date(2020, 1, 1), dt.date(2020, 1, 4)
    assert sync_wellness(source, repo, start, end, datasets="daily", refresh_days=0) == 4
    assert len(source.health_requests) == 7
    assert sync_wellness(source, repo, start, end, datasets="daily", refresh_days=0) == 0
    assert len(source.health_requests) == 7


def test_manual_missing_original_is_complete_but_real_missing_file_is_not():
    repo, archives, source = MemoryRepository(), MemoryArchives(), MemorySource()
    repo.save_summaries([ActivitySummary(2)])
    source.activity_detail = lambda _: ActivityDetail({}, manual=True)
    assert sync_activities(source, repo, archives, Decoder(), refresh_days=0) == 1
    assert repo.imports[2].source_format == "manual"
    assert not repo.imports[2].samples and not repo.failures


@pytest.mark.parametrize("kind", [FailureKind.AUTHENTICATION, FailureKind.PERMISSION, FailureKind.RATE_LIMIT])
def test_fatal_source_errors_stop_without_claiming_successful_coverage(kind):
    class Source(MemorySource):
        def health(self, dataset, window):
            raise SourceError(kind, "Stop without checkpoint")

    repo = MemoryRepository()
    with pytest.raises(SourceError):
        sync_wellness(Source(), repo, dt.date(2020, 1, 1), dt.date(2020, 1, 1), datasets="daily")
    assert not repo.responses


def test_archive_reuse_and_refresh_policy_do_not_require_a_filesystem():
    repo, archives, source = MemoryRepository(), MemoryArchives(), MemorySource()
    repo.save_summaries([ActivitySummary(1, start_time_local="2020-01-01")])
    archives.write(1, b"original observations")
    sync_activities(source, repo, archives, Decoder(), refresh_days=0)
    assert not source.downloads
    sync_activities(source, repo, archives, Decoder(), refresh=True)
    assert source.downloads == [1]


def test_requested_day_identity_and_nested_arrays_are_preserved_without_a_database():
    window = DateWindow("2020-01-02", "2020-01-02")
    payload = {"startTimestampLocal": "2020-01-01T22:00:00", "hrvReadings": [[1, 40]]}
    (record,) = normalize_response("hrv_detail", window.key, payload, window)
    assert record.calendar_date == record.record_key == "2020-01-02"
    assert record.raw["hrvReadings"] == [[1, 40]]
    weights = [{"calendarDate": "2020-01-02", "timestampGMT": n, "weight": 70} for n in (1, 2)]
    assert len({r.record_key for r in normalize_response("weight_entries", window.key, weights, window)}) == 2


def test_pure_geography_retains_gaps_and_does_not_invent_stationary_lines():
    samples = [
        Sample(0, latitude=0, longitude=0),
        Sample(1, latitude=0, longitude=0),
        Sample(2),
        Sample(3, latitude=10, longitude=20),
        Sample(4, latitude=11, longitude=21),
        Sample(5, latitude=12, longitude=22, segment_index=1),
    ]
    shape = build_geography(samples)
    assert len(shape.segments) == 1
    assert len(shape.segments[0]) == 2
    assert shape.start.longitude == shape.start.latitude == 0
    assert len(samples) == 6


@pytest.mark.parametrize(
    "overrides",
    [
        {"activity_limit": 0},
        {"refresh_days": -1},
        {"start": dt.date(2020, 1, 2)},
        {"all_data": True, "datasets": "daily"},
    ],
)
def test_invalid_sync_options_are_rejected_for_non_cli_drivers_too(overrides):
    with pytest.raises(ValueError):
        SyncOptions(end=dt.date(2020, 1, 1), **overrides)
