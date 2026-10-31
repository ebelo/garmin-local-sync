"""Resumable activity and health-data synchronization."""

import datetime as dt
import zipfile

import fitdecode

from .api import ApiError
from .datasets import DETAIL_DATASETS, DETAIL_PARAMS, RANGE_DATASETS, RANGE_DAYS
from .formats import parse_archive
from .util import atomic_write


def refresh_cutoff(days, today=None):
    """Inclusive start of N calendar days, or None to disable automatic refresh."""
    if days == 0:
        return None
    return ((today or dt.date.today()) - dt.timedelta(days=days - 1)).isoformat()


def intervals(start, end, days=28):
    while start <= end:
        stop = min(end, start + dt.timedelta(days=days - 1))
        yield start.isoformat(), stop.isoformat()
        start = stop + dt.timedelta(days=1)


def covered(store, dataset, start, end):
    """True if successfully archived windows cover this entire inclusive interval."""
    cursor = start
    for a, b in store.db.execute(
        "SELECT start_date,end_date FROM api_responses WHERE dataset=? AND start_date<=? AND end_date>=? ORDER BY start_date",
        (dataset, end, start),
    ):
        if a > cursor:
            return False
        if b >= cursor:
            if b >= end:
                return True
            cursor = (dt.date.fromisoformat(b) + dt.timedelta(days=1)).isoformat()
    return False


def sync_activity_list(api, store, limit=None, include_children=False):
    offset, count = 0, 0
    while True:
        size = min(100, limit - count) if limit else 100
        if size <= 0:
            break
        params = {"start": offset, "limit": size}
        if include_children:
            params["excludeChildren"] = "false"
        data = api.get("/activitylist-service/activities/search/activities", params)
        if not isinstance(data, list):
            raise RuntimeError("Unexpected activity list response")
        with store.db:
            for a in data:
                store.save_summary(a)
        count += len(data)
        print(f"Activities indexed: {count}", flush=True)
        if not data or len(data) < size:
            break
        offset += len(data)
    return count


def sync_activities(api, store, root, refresh_days=7, limit=None, refresh=False):
    cutoff = refresh_cutoff(refresh_days)
    query = "SELECT activity_id,start_time_local,samples_synced_at,EXISTS(SELECT 1 FROM sync_errors e WHERE e.task='activity:' || activities.activity_id) FROM activities ORDER BY start_time_local DESC,activity_id DESC"
    rows = store.db.execute(query).fetchall()
    if limit:
        rows = rows[:limit]
    pending = [
        (aid, day)
        for aid, day, synced, failed in rows
        if refresh or failed or not synced or (cutoff is not None and (day or "")[:10] >= cutoff)
    ]
    print(f"Activities to import/refresh: {len(pending)}/{len(rows)}", flush=True)
    imported = 0
    for i, (aid, day) in enumerate(pending, 1):
        try:
            detail = api.get(f"/activity-service/activity/{aid}")
            source = root / "archives" / f"{aid}.zip"
            if source.exists() and not refresh and (cutoff is None or (day or "")[:10] < cutoff):
                content = source.read_bytes()
            else:
                try:
                    content = api.get(f"/download-service/files/activity/{aid}", binary=True)
                    atomic_write(source, content)
                except ApiError as e:
                    # Manual/no-file entries are still useful activities; other failures retry next run.
                    metadata = detail.get("metadataDTO", {})
                    if e.status in (404, 204) and metadata.get("manualActivity"):
                        store.save_activity(aid, detail, [], [], "manual")
                        imported += 1
                        continue
                    raise
            samples, messages, fmt = parse_archive(content, aid)
            store.save_activity(aid, detail, samples, messages, fmt)
            imported += 1
            if i <= 3 or i % 25 == 0 or i == len(pending):
                print(
                    f"Activities imported: {i}/{len(pending)}; last={aid}; samples={len(samples)}", flush=True
                )
        except (ApiError, RuntimeError, ValueError, zipfile.BadZipFile, fitdecode.FitError) as e:
            if isinstance(e, ApiError) and e.status in (401, 403, 429):
                raise
            store.error(f"activity:{aid}", e)
            print(f"Activity {aid} incomplete: {type(e).__name__}; will retry next run", flush=True)
    return imported


def sync_activity_workouts(api, store, refresh_days=7, limit=None, refresh=False):
    """Archive each activity's performed workouts, independently of original files."""
    cutoff = refresh_cutoff(refresh_days)
    rows = store.db.execute(
        "SELECT activity_id,start_time_local FROM activities ORDER BY start_time_local DESC,activity_id DESC"
    ).fetchall()
    if limit:
        rows = rows[:limit]
    completed, skipped = 0, 0
    for aid, day in rows:
        key = str(aid)
        task = f"activity_workouts:{key}"
        checkpoint = store.db.execute(
            "SELECT 1 FROM api_responses WHERE dataset='activity_workouts' AND request_key=?", (key,)
        ).fetchone()
        failed = store.db.execute("SELECT 1 FROM sync_errors WHERE task=?", (task,)).fetchone()
        if checkpoint and not failed and not refresh and (cutoff is None or (day or "")[:10] < cutoff):
            skipped += 1
            continue
        try:
            data = api.get(f"/activity-service/activity/{aid}/workouts")
            if not isinstance(data, list):
                raise RuntimeError("Unexpected activity workouts response")
            store.response("activity_workouts", key, data)
            completed += 1
            if completed <= 3 or completed % 25 == 0:
                print(f"Activity workouts: {completed} fetched, {skipped} already present", flush=True)
        except (ApiError, RuntimeError) as error:
            if isinstance(error, ApiError) and error.status in (401, 403, 429):
                raise
            store.error(task, error)
            print(f"Activity workouts {aid}: incomplete; will retry next run", flush=True)
    print(f"Activity workouts: {completed} fetched, {skipped} already present", flush=True)
    return completed


def sync_wellness(
    api, store, start, end, refresh_days=7, refresh=False, detailed=False, datasets=None, discover_start=False
):
    cutoff = refresh_cutoff(refresh_days)
    wanted = set(datasets.split(",")) if datasets else set(RANGE_DATASETS)
    if detailed:
        wanted.update(DETAIL_DATASETS)
    unknown = wanted - RANGE_DATASETS.keys() - DETAIL_DATASETS.keys()
    if unknown:
        raise RuntimeError("Unknown datasets: " + ", ".join(sorted(unknown)))
    requests_done = 0
    for dataset in sorted(wanted, key=lambda x: (x != "daily", x)):
        dataset_start = start
        if discover_start and dataset not in ("daily", "weight", "weight_entries", "weight_goals"):
            first = store.db.execute(
                "SELECT min(calendar_date) FROM wellness_records WHERE dataset='daily' AND json_extract(payload_json,'$.includesWellnessData')=1"
            ).fetchone()[0]
            if first:
                dataset_start = max(start, dt.date.fromisoformat(first))
        windows = intervals(
            dataset_start, end, 1 if dataset in DETAIL_DATASETS else RANGE_DAYS.get(dataset, 28)
        )
        completed, skipped = 0, 0
        consecutive_failures = 0

        def fetch_window(a, b):
            nonlocal completed, skipped, requests_done, consecutive_failures
            key = f"{a}/{b}"
            failed = store.db.execute(
                "SELECT 1 FROM sync_errors WHERE task=?", (f"{dataset}:{key}",)
            ).fetchone()
            if (
                not failed
                and not refresh
                and (cutoff is None or b < cutoff)
                and covered(store, dataset, a, b)
            ):
                skipped += 1
                return True
            if dataset in RANGE_DATASETS:
                path, mode = RANGE_DATASETS[dataset]
                path = path.format(start=a, end=b)
                params = (
                    {"startDate": a, "endDate": b}
                    if mode == "params"
                    else {"includeAll": "true"}
                    if mode == "weight"
                    else None
                )
            else:
                path, date_param = DETAIL_DATASETS[dataset]
                path = path.format(day=a)
                params = dict(DETAIL_PARAMS.get(dataset, {}))
                if date_param:
                    params[date_param] = a
                params = params or None
            try:
                data = api.get(path, params)
                store.response(dataset, key, data, a, b)
                completed += 1
                requests_done += 1
                consecutive_failures = 0
                if completed % 25 == 0:
                    print(f"Health {dataset}: {completed} date windows fetched; through {b}", flush=True)
                return True
            except ApiError as e:
                if e.status in (401, 403, 429):
                    raise
                # Some Garmin date ranges fail even below the documented maximum.
                # Split them, retaining independent successful subrange checkpoints.
                if e.status in (400, 413, 500, 502, 503, 504) and a < b:
                    first, last = dt.date.fromisoformat(a), dt.date.fromisoformat(b)
                    middle = first + (last - first) // 2
                    print(f"Health {dataset}: splitting failed range {a}/{b} (HTTP {e.status})", flush=True)
                    left = fetch_window(a, middle.isoformat())
                    right = fetch_window((middle + dt.timedelta(days=1)).isoformat(), b)
                    if left and right:
                        with store.db:
                            store.clear_error(f"{dataset}:{key}")
                    return left and right
                store.error(f"{dataset}:{key}", e)
                print(f"Health {dataset} {key}: HTTP {e.status}; recorded for retry", flush=True)
                consecutive_failures += 1
                if consecutive_failures >= 5:
                    raise RuntimeError("Repeated Garmin failures; completed data saved, rerun to resume")
                return False

        for a, b in windows:
            fetch_window(a, b)
        print(f"Health {dataset}: {completed} windows fetched, {skipped} already present", flush=True)
    return requests_done
