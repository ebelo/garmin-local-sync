"""Compatibility facade; canonical workflows live in application.sync behind ports."""

from .adapters.decoding import OriginalActivityDecoder
from .adapters.filesystem import FileArchiveStore
from .adapters.garmin import GarminSource
from .application import sync as workflows
from .application.sync import covered, intervals, refresh_cutoff

__all__ = [
    "covered",
    "intervals",
    "refresh_cutoff",
    "sync_activity_list",
    "sync_activities",
    "sync_activity_workouts",
    "sync_wellness",
]


def console(message):
    print(message, flush=True)


def sync_activity_list(api, store, limit=None, include_children=False):
    return workflows.sync_activity_list(GarminSource(api), store, limit, include_children, console)


def sync_activities(api, store, root, refresh_days=7, limit=None, refresh=False):
    return workflows.sync_activities(
        GarminSource(api),
        store,
        FileArchiveStore(root),
        OriginalActivityDecoder(),
        refresh_days,
        limit,
        refresh,
        console,
    )


def sync_activity_workouts(api, store, refresh_days=7, limit=None, refresh=False):
    return workflows.sync_activity_workouts(GarminSource(api), store, refresh_days, limit, refresh, console)


def sync_wellness(
    api, store, start, end, refresh_days=7, refresh=False, detailed=False, datasets=None, discover_start=False
):
    return workflows.sync_wellness(
        GarminSource(api),
        store,
        start,
        end,
        refresh_days,
        refresh,
        detailed,
        datasets,
        discover_start,
        console,
    )
