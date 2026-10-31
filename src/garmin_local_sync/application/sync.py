"""Synchronization policies and workflows, depending only on domain values and ports."""

import datetime as dt

from ..domain.datasets import DATASETS, DETAIL_DATASETS, RANGE_DATASETS
from ..domain.errors import SourceError
from ..domain.models import DateWindow, DecodedActivity, SyncOptions
from .ports import ActivityDecoder, ActivitySource, ArchiveStore, Progress, SyncRepository


def quiet(message: str) -> None:
    # Default progress sink intentionally discards messages for noninteractive drivers.
    pass


def refresh_cutoff(days, today=None):
    if days == 0:
        return None
    return ((today or dt.date.today()) - dt.timedelta(days=days - 1)).isoformat()


def intervals(start, end, days=28):
    while start <= end:
        stop = min(end, start + dt.timedelta(days=days - 1))
        yield start.isoformat(), stop.isoformat()
        start = stop + dt.timedelta(days=1)


def covered(repository: SyncRepository, dataset: str, start: str, end: str) -> bool:
    cursor = start
    for window in repository.response_windows(dataset, start, end):
        a, b = window.start, window.end
        if a > cursor:
            return False
        if b >= cursor:
            if b >= end:
                return True
            cursor = (dt.date.fromisoformat(b) + dt.timedelta(days=1)).isoformat()
    return False


def sync_activity_list(
    source: ActivitySource,
    repository: SyncRepository,
    limit=None,
    include_children=False,
    progress: Progress = quiet,
):
    offset, count = 0, 0
    while True:
        size = min(100, limit - count) if limit else 100
        if size <= 0:
            break
        summaries = source.list_activities(offset, size, include_children)
        repository.save_summaries(summaries)
        count += len(summaries)
        progress(f"Activities indexed: {count}")
        if not summaries or len(summaries) < size:
            break
        offset += len(summaries)
    return count


def import_activity(source, repository, archives, decoder, activity, reuse_archive):
    """Complete one item; return None for a manual activity without an original."""
    aid = activity.activity_id
    detail = source.activity_detail(aid)
    content = archives.read(aid) if reuse_archive else None
    if content is None:
        try:
            content = source.download_activity(aid)
            archives.write(aid, content)
        except SourceError as error:
            if error.missing_file and detail.manual:
                repository.save_import(aid, detail, DecodedActivity())
                return None
            raise
    decoded = decoder.decode(content, aid)
    repository.save_import(aid, detail, decoded)
    return decoded


def needs_import(activity, refresh, cutoff):
    return (
        refresh
        or activity.failed
        or not activity.imported
        or (cutoff is not None and (activity.start_time_local or "")[:10] >= cutoff)
    )


def sync_activities(
    source: ActivitySource,
    repository: SyncRepository,
    archives: ArchiveStore,
    decoder: ActivityDecoder,
    refresh_days=7,
    limit=None,
    refresh=False,
    progress: Progress = quiet,
    today=None,
):
    cutoff = refresh_cutoff(refresh_days, today)
    rows = repository.activity_states(limit)
    pending = [a for a in rows if needs_import(a, refresh, cutoff)]
    progress(f"Activities to import/refresh: {len(pending)}/{len(rows)}")
    imported = 0
    for i, activity in enumerate(pending, 1):
        aid, day = activity.activity_id, activity.start_time_local
        try:
            reuse = not refresh and (cutoff is None or (day or "")[:10] < cutoff)
            decoded = import_activity(source, repository, archives, decoder, activity, reuse)
            if decoded is None:
                imported += 1
                continue
            imported += 1
            if i <= 3 or i % 25 == 0 or i == len(pending):
                progress(
                    f"Activities imported: {i}/{len(pending)}; last={aid}; samples={len(decoded.samples)}"
                )
        except (RuntimeError, ValueError) as error:
            if isinstance(error, SourceError) and error.fatal:
                raise
            repository.record_failure(f"activity:{aid}", error)
            progress(f"Activity {aid} incomplete: {type(error).__name__}; will retry next run")
    return imported


def sync_activity_workouts(
    source: ActivitySource,
    repository: SyncRepository,
    refresh_days=7,
    limit=None,
    refresh=False,
    progress: Progress = quiet,
    today=None,
):
    cutoff = refresh_cutoff(refresh_days, today)
    completed, skipped = 0, 0
    for activity in repository.activity_states(limit):
        aid, day = activity.activity_id, activity.start_time_local
        key, task = str(aid), f"activity_workouts:{aid}"
        if (
            repository.has_response("activity_workouts", key)
            and not repository.has_failure(task)
            and not refresh
            and (cutoff is None or (day or "")[:10] < cutoff)
        ):
            skipped += 1
            continue
        try:
            repository.save_response("activity_workouts", key, source.activity_workouts(aid))
            completed += 1
            if completed <= 3 or completed % 25 == 0:
                progress(f"Activity workouts: {completed} fetched, {skipped} already present")
        except RuntimeError as error:
            if isinstance(error, SourceError) and error.fatal:
                raise
            repository.record_failure(task, error)
            progress(f"Activity workouts {aid}: incomplete; will retry next run")
    progress(f"Activity workouts: {completed} fetched, {skipped} already present")
    return completed


class WellnessDatasetSync:
    """Keep checkpoints and failure counters scoped to one health dataset."""

    def __init__(self, source, repository, dataset, progress, *, cutoff, refresh):
        self.source, self.repository = source, repository
        self.dataset, self.progress = dataset, progress
        self.cutoff, self.refresh = cutoff, refresh
        self.completed = 0
        self.skipped = 0
        self.consecutive_failures = 0

    def fetch(self, start, end):
        window = DateWindow(start, end)
        task = f"{self.dataset}:{window.key}"
        if (
            not self.repository.has_failure(task)
            and not self.refresh
            and (self.cutoff is None or end < self.cutoff)
            and covered(self.repository, self.dataset, start, end)
        ):
            self.skipped += 1
            return True
        try:
            data = self.source.health(self.dataset, window)
            self.repository.save_response(self.dataset, window.key, data, window)
            self.completed += 1
            self.consecutive_failures = 0
            if self.completed % 25 == 0:
                self.progress(f"Health {self.dataset}: {self.completed} date windows fetched; through {end}")
            return True
        except SourceError as error:
            return self.failed_window(window, error)

    def failed_window(self, window, error):
        if error.fatal:
            raise error
        if error.can_split_range and window.start < window.end:
            return self.split_window(window, error)
        task = f"{self.dataset}:{window.key}"
        self.repository.record_failure(task, error)
        self.progress(f"Health {self.dataset} {window.key}: {error.kind.value}; recorded for retry")
        self.consecutive_failures += 1
        if self.consecutive_failures >= 5:
            raise RuntimeError("Repeated source failures; completed data saved, rerun to resume") from error
        return False

    def split_window(self, window, error):
        first, last = dt.date.fromisoformat(window.start), dt.date.fromisoformat(window.end)
        middle = first + (last - first) // 2
        self.progress(f"Health {self.dataset}: splitting failed range {window.key} ({error.kind.value})")
        # Both halves must run even when the left half remains incomplete.
        left = self.fetch(window.start, middle.isoformat())
        right = self.fetch((middle + dt.timedelta(days=1)).isoformat(), window.end)
        if left and right:
            self.repository.clear_failure(f"{self.dataset}:{window.key}")
        return left and right


def wanted_datasets(datasets, detailed):
    wanted = set(datasets.split(",")) if datasets else set(RANGE_DATASETS)
    if detailed:
        wanted.update(DETAIL_DATASETS)
    unknown = wanted - DATASETS.keys()
    if unknown:
        raise RuntimeError("Unknown datasets: " + ", ".join(sorted(unknown)))
    return sorted(wanted, key=lambda x: (x != "daily", x))


def dataset_start_date(repository, dataset, start, discover_start):
    if discover_start and DATASETS[dataset].discover_from_wellness:
        first = repository.first_wellness_date()
        if first:
            return max(start, first)
    return start


def sync_wellness(
    source: ActivitySource,
    repository: SyncRepository,
    start,
    end,
    refresh_days=7,
    refresh=False,
    detailed=False,
    datasets=None,
    discover_start=False,
    progress: Progress = quiet,
    today=None,
):
    cutoff = refresh_cutoff(refresh_days, today)
    requests_done = 0
    for dataset in wanted_datasets(datasets, detailed):
        dataset_start = dataset_start_date(repository, dataset, start, discover_start)
        worker = WellnessDatasetSync(source, repository, dataset, progress, cutoff=cutoff, refresh=refresh)
        for a, b in intervals(dataset_start, end, DATASETS[dataset].window_days):
            worker.fetch(a, b)
        requests_done += worker.completed
        progress(f"Health {dataset}: {worker.completed} windows fetched, {worker.skipped} already present")
    return requests_done


class SyncService:
    """Inbound use case, wired by a CLI, scheduler or another driving adapter."""

    def __init__(
        self,
        source: ActivitySource,
        repository: SyncRepository,
        archives: ArchiveStore,
        decoder: ActivityDecoder,
        progress: Progress = quiet,
        today=None,
    ):
        self.source, self.repository = source, repository
        self.archives, self.decoder = archives, decoder
        self.progress, self.today = progress, today

    def run(self, options: SyncOptions) -> dict:
        source, repository, progress = self.source, self.repository, self.progress
        if not options.skip_activities:
            sync_activity_list(source, repository, options.activity_limit, options.all_data, progress)
        if not options.skip_health:
            start = (
                options.start or repository.earliest_activity_date() or options.end - dt.timedelta(days=90)
            )
            progress(f"Health date range: {start} through {options.end}")
            for dataset, data in source.context():
                repository.save_response(dataset, "current", data)
            sync_wellness(
                source,
                repository,
                start,
                options.end,
                options.refresh_days,
                options.refresh,
                options.daily_details or options.all_data,
                options.datasets,
                options.start is None,
                progress,
                self.today,
            )
        if not options.skip_activities:
            sync_activities(
                source,
                repository,
                self.archives,
                self.decoder,
                options.refresh_days,
                options.activity_limit,
                options.refresh,
                progress,
                self.today,
            )
            if options.activity_workouts or options.all_data:
                sync_activity_workouts(
                    source,
                    repository,
                    options.refresh_days,
                    options.activity_limit,
                    options.refresh,
                    progress,
                    self.today,
                )
        return repository.snapshot()
