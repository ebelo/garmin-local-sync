"""GeoPackage persistence and local data inspection."""

import contextlib
import datetime as dt
import math
import os
import sqlite3
from pathlib import Path
from urllib.parse import quote

from osgeo import ogr, osr

from .config import SCHEMA_VERSION
from .datasets import DETAIL_DATASETS
from .geography import activity_geometries
from .util import dumps, now

ogr.UseExceptions()


SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY);

CREATE TABLE IF NOT EXISTS activities(
 activity_id INTEGER PRIMARY KEY, name TEXT, activity_type TEXT,
 start_time_utc TEXT, start_time_local TEXT, distance_m REAL, duration_s REAL,
 moving_duration_s REAL, elevation_gain_m REAL, elevation_loss_m REAL,
 average_hr REAL, max_hr REAL, average_speed_mps REAL, calories REAL,
 training_load REAL, aerobic_training_effect REAL, anaerobic_training_effect REAL,
 start_latitude REAL, start_longitude REAL, summary_json TEXT NOT NULL,
 detail_json TEXT, fetched_at TEXT NOT NULL, samples_synced_at TEXT,
 sample_count INTEGER, gps_point_count INTEGER, source_format TEXT);
CREATE INDEX IF NOT EXISTS idx_activities_date ON activities(start_time_local);
CREATE TABLE IF NOT EXISTS activity_samples(
 fid INTEGER PRIMARY KEY,
 activity_id INTEGER NOT NULL REFERENCES activities(activity_id), sample_index INTEGER NOT NULL,
 timestamp_utc TEXT, latitude REAL, longitude REAL, altitude_m REAL,
 distance_m REAL, speed_mps REAL, heart_rate_bpm REAL, cadence_rpm REAL,
 power_w REAL, temperature_c REAL, raw_json TEXT NOT NULL, segment_index INTEGER NOT NULL DEFAULT 0,
 UNIQUE(activity_id,sample_index));
CREATE INDEX IF NOT EXISTS idx_samples_timestamp ON activity_samples(timestamp_utc);
CREATE TABLE IF NOT EXISTS activity_fit_messages(
 fid INTEGER PRIMARY KEY,
 activity_id INTEGER NOT NULL REFERENCES activities(activity_id), message_index INTEGER NOT NULL,
 message_type TEXT NOT NULL, raw_json TEXT NOT NULL,
 UNIQUE(activity_id,message_index));
CREATE TABLE IF NOT EXISTS api_responses(
 fid INTEGER PRIMARY KEY,
 dataset TEXT NOT NULL, request_key TEXT NOT NULL, start_date TEXT, end_date TEXT,
 payload_json TEXT NOT NULL, fetched_at TEXT NOT NULL, UNIQUE(dataset,request_key));
CREATE TABLE IF NOT EXISTS wellness_records(
 fid INTEGER PRIMARY KEY,
 dataset TEXT NOT NULL, record_key TEXT NOT NULL, calendar_date TEXT,
 payload_json TEXT NOT NULL, fetched_at TEXT NOT NULL, UNIQUE(dataset,record_key));
CREATE INDEX IF NOT EXISTS idx_wellness_date ON wellness_records(calendar_date,dataset);
CREATE TABLE IF NOT EXISTS daily_metrics(
 fid INTEGER PRIMARY KEY,
 dataset TEXT NOT NULL, record_key TEXT NOT NULL, calendar_date TEXT NOT NULL,
 metric TEXT NOT NULL, value REAL NOT NULL, UNIQUE(dataset,record_key,metric));
CREATE INDEX IF NOT EXISTS idx_metrics_date ON daily_metrics(calendar_date,metric);
CREATE TABLE IF NOT EXISTS sync_errors(
 fid INTEGER PRIMARY KEY, task TEXT NOT NULL UNIQUE, error TEXT NOT NULL, last_attempt TEXT NOT NULL);
CREATE VIEW IF NOT EXISTS activity_analytics AS
 SELECT activity_id,name,activity_type,start_time_local,start_time_utc,
 distance_m/1000.0 AS distance_km,duration_s/3600.0 AS duration_hours,
 moving_duration_s/3600.0 AS moving_hours,elevation_gain_m,elevation_loss_m,
 average_hr,max_hr,calories,training_load,aerobic_training_effect,
 average_speed_mps*3.6 AS average_speed_kmh,sample_count,gps_point_count
 FROM activities;
CREATE VIEW IF NOT EXISTS daily_analytics AS
 SELECT calendar_date,
 max(CASE WHEN dataset='daily' AND metric='movement.steps.value' THEN value END) AS steps,
 max(CASE WHEN dataset='daily' AND metric='heartRate.restingValue' THEN value END) AS resting_hr,
 max(CASE WHEN dataset='daily' AND metric='stress.avgLevel' THEN value END) AS stress_average,
 max(CASE WHEN dataset='daily' AND metric='bodyBattery.maxValue' THEN value END) AS body_battery_max,
 max(CASE WHEN dataset='daily' AND metric='bodyBattery.minValue' THEN value END) AS body_battery_min,
 max(CASE WHEN dataset='daily' AND metric='calories.burnedTotal' THEN value END) AS calories_burned,
 max(CASE WHEN dataset='sleep' AND metric='sleepTimeSeconds' THEN value/3600.0 END) AS sleep_hours,
 max(CASE WHEN dataset='sleep' AND metric='sleepScores.overall.value' THEN value END) AS sleep_score,
 max(CASE WHEN dataset='hrv' AND metric='lastNightAvg' THEN value END) AS hrv_last_night_ms,
 max(CASE WHEN dataset='weight' AND metric='latestWeight.weight' THEN value/1000.0 END) AS weight_kg
 FROM daily_metrics GROUP BY calendar_date;
"""


class Store:
    def __init__(self, path, read_only=False):
        self.path = Path(path).expanduser().resolve()
        self.read_only = read_only
        existing = self.path.exists()
        version = None
        if read_only and not existing:
            raise FileNotFoundError("GeoPackage does not exist; run sync first or select --database")
        if existing or read_only:
            self.db = sqlite3.connect(
                "file:" + quote(str(self.path), safe="/") + "?mode=ro", uri=True, timeout=60
            )
            try:
                if self.db.execute("PRAGMA application_id").fetchone()[0] != 0x47504B47:
                    raise RuntimeError("Not a GeoPackage; refusing to modify this file")
                versions = self.db.execute("SELECT version FROM schema_version").fetchall()
                if len(versions) != 1 or versions[0][0] not in (1, SCHEMA_VERSION):
                    raise RuntimeError("Unsupported database schema version")
                version = versions[0][0]
                self.db.execute("SELECT activity_id,summary_json FROM activities LIMIT 0")
            except (sqlite3.DatabaseError, RuntimeError) as error:
                self.db.close()
                if isinstance(error, RuntimeError):
                    raise
                raise RuntimeError("Not a supported garmin-local-sync GeoPackage") from None
            if read_only:
                return
            self.db.close()
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not existing:
            ds = ogr.GetDriverByName("GPKG").CreateDataSource(str(self.path), options=["VERSION=1.3"])
            srs = osr.SpatialReference()
            srs.ImportFromEPSG(4326)
            srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
            for name, geom_type in [
                ("activity_tracks", ogr.wkbMultiLineString),
                ("activity_starts", ogr.wkbPoint),
            ]:
                layer = ds.CreateLayer(name, srs, geom_type, options=["SPATIAL_INDEX=YES"])
                layer.CreateField(ogr.FieldDefn("activity_id", ogr.OFTInteger64))
                layer.CreateField(ogr.FieldDefn("start_time", ogr.OFTString))
                layer.CreateField(ogr.FieldDefn("activity_type", ogr.OFTString))
                ds.ExecuteSQL(f'CREATE UNIQUE INDEX "idx_{name}_id" ON "{name}"(activity_id)')
            ds = None
        os.chmod(self.path, 0o600)
        self.db = sqlite3.connect(self.path, timeout=60)
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(SCHEMA)
        if version is None:
            self.db.execute("INSERT INTO schema_version VALUES(?)", (SCHEMA_VERSION,))
        elif version == 1:
            with self.db:
                self.db.execute(
                    "ALTER TABLE activity_samples ADD COLUMN segment_index INTEGER NOT NULL DEFAULT 0"
                )
                self.db.execute("UPDATE schema_version SET version=?", (SCHEMA_VERSION,))
        for name in [
            "activities",
            "activity_samples",
            "activity_fit_messages",
            "api_responses",
            "wellness_records",
            "daily_metrics",
            "sync_errors",
        ]:
            self.db.execute(
                "INSERT OR IGNORE INTO gpkg_contents(table_name,data_type,identifier,srs_id) VALUES(?, 'attributes', ?, NULL)",
                (name, name),
            )
        self.db.commit()
        previous_owner = self.db.execute(
            "SELECT json_extract(summary_json,'$.ownerId') FROM activities WHERE json_extract(summary_json,'$.ownerId') IS NOT NULL LIMIT 1"
        ).fetchone()
        self.owner_id = previous_owner[0] if previous_owner else None

    def close(self):
        if not self.read_only and self.db.total_changes:
            with self.db:
                self.db.execute(
                    "UPDATE gpkg_contents SET last_change=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE data_type='attributes'"
                )
        self.db.close()

    def save_summary(self, a):
        owner = a.get("ownerId")
        if owner is not None:
            if self.owner_id is not None and owner != self.owner_id:
                raise RuntimeError(
                    "This GeoPackage belongs to a different Garmin account; select a separate database"
                )
            self.owner_id = owner
        atype = a.get("activityType", {}).get("typeKey")
        keys = [
            "activityId",
            "activityName",
            None,
            "startTimeGMT",
            "startTimeLocal",
            "distance",
            "duration",
            "movingDuration",
            "elevationGain",
            "elevationLoss",
            "averageHR",
            "maxHR",
            "averageSpeed",
            "calories",
            "activityTrainingLoad",
            "aerobicTrainingEffect",
            "anaerobicTrainingEffect",
            "startLatitude",
            "startLongitude",
        ]
        values = [a.get(k) if k else atype for k in keys] + [dumps(a), now()]
        cols = [
            "activity_id",
            "name",
            "activity_type",
            "start_time_utc",
            "start_time_local",
            "distance_m",
            "duration_s",
            "moving_duration_s",
            "elevation_gain_m",
            "elevation_loss_m",
            "average_hr",
            "max_hr",
            "average_speed_mps",
            "calories",
            "training_load",
            "aerobic_training_effect",
            "anaerobic_training_effect",
            "start_latitude",
            "start_longitude",
            "summary_json",
            "fetched_at",
        ]
        updates = ",".join(f"{c}=excluded.{c}" for c in cols[1:])
        self.db.execute(
            f"INSERT INTO activities({','.join(cols)}) VALUES({','.join('?' for _ in cols)}) ON CONFLICT(activity_id) DO UPDATE SET {updates}",
            values,
        )

    def error(self, task, error):
        with self.db:
            self.db.execute(
                "INSERT INTO sync_errors(task,error,last_attempt) VALUES(?,?,?) ON CONFLICT(task) DO UPDATE SET error=excluded.error,last_attempt=excluded.last_attempt",
                (task, str(error), now()),
            )

    def clear_error(self, task):
        self.db.execute("DELETE FROM sync_errors WHERE task=?", (task,))

    def save_geometry(self, aid, samples):
        # OGR owns GeoPackage geometry encoding and spatial-index triggers.
        self.db.commit()
        ds = ogr.Open(str(self.path), 1)
        ds.StartTransaction()
        try:
            info = self.db.execute(
                "SELECT start_time_local,activity_type,start_longitude,start_latitude FROM activities WHERE activity_id=?",
                (aid,),
            ).fetchone()
            track, first = activity_geometries(samples, fallback=(info[2], info[3]))
            for name, geometry in [("activity_tracks", track), ("activity_starts", first)]:
                layer = ds.GetLayerByName(name)
                layer.SetAttributeFilter(f"activity_id={int(aid)}")
                fids = [f.GetFID() for f in layer]
                layer.SetAttributeFilter(None)
                for fid in fids:
                    layer.DeleteFeature(fid)
                if geometry is not None:
                    feature = ogr.Feature(layer.GetLayerDefn())
                    feature.SetField("activity_id", aid)
                    feature.SetField("start_time", info[0] or "")
                    feature.SetField("activity_type", info[1] or "")
                    feature.SetGeometry(geometry)
                    layer.CreateFeature(feature)
            ds.CommitTransaction()
        except Exception:
            ds.RollbackTransaction()
            raise
        finally:
            ds = None

    def save_activity(self, aid, detail, samples, messages, fmt):
        # Invalidate an old checkpoint before either connection commits a refresh.
        # A crash between geometry and attributes must remain visibly incomplete.
        with self.db:
            self.db.execute("UPDATE activities SET samples_synced_at=NULL WHERE activity_id=?", (aid,))
        self.save_geometry(aid, samples)
        with self.db:
            self.db.execute("DELETE FROM activity_samples WHERE activity_id=?", (aid,))
            self.db.executemany(
                "INSERT INTO activity_samples(activity_id,sample_index,timestamp_utc,latitude,longitude,altitude_m,distance_m,speed_mps,heart_rate_bpm,cadence_rpm,power_w,temperature_c,raw_json,segment_index) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                samples,
            )
            self.db.execute("DELETE FROM activity_fit_messages WHERE activity_id=?", (aid,))
            self.db.executemany(
                "INSERT INTO activity_fit_messages(activity_id,message_index,message_type,raw_json) VALUES(?,?,?,?)",
                messages,
            )
            self.db.execute(
                "UPDATE activities SET detail_json=?,samples_synced_at=?,sample_count=?,gps_point_count=?,source_format=? WHERE activity_id=?",
                (dumps(detail), now(), len(samples), sum(s[3] is not None for s in samples), fmt, aid),
            )
            self.clear_error(f"activity:{aid}")

    def response(self, dataset, key, data, start=None, end=None):
        with self.db:
            self.db.execute(
                "INSERT INTO api_responses(dataset,request_key,start_date,end_date,payload_json,fetched_at) VALUES(?,?,?,?,?,?) ON CONFLICT(dataset,request_key) DO UPDATE SET payload_json=excluded.payload_json,fetched_at=excluded.fetched_at",
                (dataset, key, start, end, dumps(data), now()),
            )
            if start and end:
                self.db.execute(
                    "DELETE FROM daily_metrics WHERE dataset=? AND calendar_date BETWEEN ? AND ?",
                    (dataset, start, end),
                )
                self.db.execute(
                    "DELETE FROM wellness_records WHERE dataset=? AND calendar_date BETWEEN ? AND ?",
                    (dataset, start, end),
                )
            entries = records(data)
            for index, record in enumerate(entries):
                day = record_date(record)
                if dataset in DETAIL_DATASETS and start == end:
                    # Overnight data can start on the preceding local date. Its
                    # daily identity is the requested day, not that start timestamp.
                    day = start
                if day is None and start == end:
                    day = start
                rid = str(day or record.get("id") or record.get("uuid") or f"{key}:{index}")
                # One daily summary per date, but nutrition/detail arrays may have many entries.
                if dataset in DETAIL_DATASETS and len(entries) > 1:
                    rid = f"{rid}:{record.get('id', index)}"
                if dataset == "wellness_activities":
                    identity = record.get("activityUuid")
                    if isinstance(identity, dict):
                        identity = identity.get("uuid")
                    rid = str(identity or f"{rid}:{index}")
                if dataset == "weight_entries":
                    # Multiple measurements on one date must not overwrite one another.
                    identity = record.get("id") or record.get("timestampGMT") or record.get("date")
                    rid = f"{day}:{identity if identity is not None else index}"
                self.db.execute(
                    "INSERT INTO wellness_records(dataset,record_key,calendar_date,payload_json,fetched_at) VALUES(?,?,?,?,?) ON CONFLICT(dataset,record_key) DO UPDATE SET calendar_date=excluded.calendar_date,payload_json=excluded.payload_json,fetched_at=excluded.fetched_at",
                    (dataset, rid, day, dumps(record), now()),
                )
                self.db.execute("DELETE FROM daily_metrics WHERE dataset=? AND record_key=?", (dataset, rid))
                if day:
                    self.db.executemany(
                        "INSERT INTO daily_metrics(dataset,record_key,calendar_date,metric,value) VALUES(?,?,?,?,?)",
                        [(dataset, rid, day, k, v) for k, v in flatten_numbers(record)],
                    )
            self.clear_error(f"{dataset}:{key}")


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


def status(store):
    result = {"database": str(store.path)}
    for name in [
        "activities",
        "activity_samples",
        "activity_tracks",
        "activity_starts",
        "daily_metrics",
        "sync_errors",
    ]:
        result[name] = store.db.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]
    result["activities_imported"] = store.db.execute(
        "SELECT count(*) FROM activities WHERE samples_synced_at IS NOT NULL"
    ).fetchone()[0]
    result["activity_dates"] = store.db.execute(
        "SELECT min(start_time_local),max(start_time_local) FROM activities"
    ).fetchone()
    result["health_datasets"] = [
        dict(zip(["dataset", "records", "first_date", "last_date"], row))
        for row in store.db.execute(
            "SELECT dataset,count(*),min(calendar_date),max(calendar_date) FROM wellness_records GROUP BY dataset ORDER BY dataset"
        )
    ]
    return result
