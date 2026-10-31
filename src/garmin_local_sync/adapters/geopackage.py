"""GeoPackage persistence and local data inspection."""

import datetime as dt
import os
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import quote

from osgeo import ogr, osr

from ..config import SCHEMA_VERSION
from ..domain.health import normalize_response
from ..domain.models import ActivityState, DateWindow, Message, Sample
from ..util import dumps, now
from .geometry import ELEVATION_METADATA, SPATIAL_LAYERS, activity_geometries, activity_track_3d
from .mapping import sample_to_row, summary_from_payload

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
 sample_count INTEGER, gps_point_count INTEGER, source_format TEXT, geometry_3d_synced_at TEXT);
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


class GeoPackageRepository:
    def __init__(self, path, read_only=False):
        self.path = Path(path).expanduser().resolve()
        self.read_only = read_only
        existing = self.path.exists()
        version = None
        if existing or read_only:
            version = self._open_existing()
            self.schema_version = version
            if read_only:
                return
            self.db.close()
        try:
            self._initialize(version, existing)
        except BaseException:
            connection = getattr(self, "db", None)
            if connection is not None:
                connection.close()
            raise

    def _open_existing(self):
        if not self.path.exists():
            raise FileNotFoundError("GeoPackage does not exist; run sync first or select --database")
        self.db = sqlite3.connect(
            "file:" + quote(str(self.path), safe="/") + "?mode=ro", uri=True, timeout=60
        )
        try:
            if self.db.execute("PRAGMA application_id").fetchone()[0] != 0x47504B47:
                raise RuntimeError("Not a GeoPackage; refusing to modify this file")
            versions = self.db.execute("SELECT version FROM schema_version").fetchall()
            if len(versions) != 1 or versions[0][0] not in (1, 2, SCHEMA_VERSION):
                raise RuntimeError("Unsupported database schema version")
            self.db.execute("SELECT activity_id,summary_json FROM activities LIMIT 0")
            return versions[0][0]
        except (sqlite3.DatabaseError, RuntimeError) as error:
            self.db.close()
            if isinstance(error, RuntimeError):
                raise
            raise RuntimeError("Not a supported garmin-local-sync GeoPackage") from None

    def _initialize(self, version, existing):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not existing:
            self._create_spatial_layers()
        os.chmod(self.path, 0o600)
        self.db = sqlite3.connect(self.path, timeout=60)
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(SCHEMA)
        if version is None:
            self.db.execute("INSERT INTO schema_version VALUES(?)", (SCHEMA_VERSION,))
        elif version < SCHEMA_VERSION:
            self._migrate(version)
        self.schema_version = SCHEMA_VERSION
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

    def _create_spatial_layers(self):
        ds = ogr.GetDriverByName("GPKG").CreateDataSource(str(self.path), options=["VERSION=1.3"])
        srs = osr.SpatialReference()
        srs.ImportFromEPSG(4326)
        srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        for name, geom_type in SPATIAL_LAYERS:
            self._create_layer(ds, name, geom_type, srs)
        ds = None

    @staticmethod
    def _create_layer(ds, name, geom_type, srs):
        layer = ds.CreateLayer(name, srs, geom_type, options=["SPATIAL_INDEX=YES"])
        layer.CreateField(ogr.FieldDefn("activity_id", ogr.OFTInteger64))
        layer.CreateField(ogr.FieldDefn("start_time", ogr.OFTString))
        layer.CreateField(ogr.FieldDefn("activity_type", ogr.OFTString))
        ds.ExecuteSQL(f'CREATE UNIQUE INDEX "idx_{name}_id" ON "{name}"(activity_id)')
        if name == "activity_tracks_3d":
            layer.SetMetadata(ELEVATION_METADATA)

    def _migrate(self, version):
        # Attribute/layer steps are repeatable after interruption. Mark version last.
        with self.db:
            columns = {r[1] for r in self.db.execute("PRAGMA table_info(activity_samples)")}
            if version == 1 and "segment_index" not in columns:
                self.db.execute(
                    "ALTER TABLE activity_samples ADD COLUMN segment_index INTEGER NOT NULL DEFAULT 0"
                )
            columns = {r[1] for r in self.db.execute("PRAGMA table_info(activities)")}
            if "geometry_3d_synced_at" not in columns:
                self.db.execute("ALTER TABLE activities ADD COLUMN geometry_3d_synced_at TEXT")
        self._ensure_3d_layer()
        with self.db:
            self.db.execute("UPDATE schema_version SET version=?", (SCHEMA_VERSION,))

    def _ensure_3d_layer(self):
        ds = ogr.Open(str(self.path), 1)
        try:
            layer = ds.GetLayerByName("activity_tracks_3d")
            if layer is None:
                srs = osr.SpatialReference()
                srs.ImportFromEPSG(4326)
                srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
                self._create_layer(ds, "activity_tracks_3d", ogr.wkbMultiLineString25D, srs)
            else:
                if layer.GetGeomType() != ogr.wkbMultiLineString25D:
                    raise RuntimeError("Existing 3D layer has incompatible geometry")
                layer.SetMetadata({**layer.GetMetadata(), **ELEVATION_METADATA})
            layer = None
        finally:
            ds = None

    def close(self):
        if not self.read_only and self.db.total_changes:
            with self.db:
                self.db.execute(
                    "UPDATE gpkg_contents SET last_change=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE data_type='attributes'"
                )
        self.db.close()

    def save_summary(self, payload):
        """Save one vendor summary at the infrastructure boundary."""
        self._save_summary(summary_from_payload(payload))

    def save_summaries(self, summaries):
        with self.db:
            for summary in summaries:
                self._save_summary(summary)

    def _save_summary(self, summary):
        owner = summary.owner_id
        if owner is not None:
            if self.owner_id is not None and owner != self.owner_id:
                raise RuntimeError(
                    "This GeoPackage belongs to a different Garmin account; select a separate database"
                )
            self.owner_id = owner
        values = [
            getattr(summary, name)
            for name in (
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
            )
        ] + [dumps(summary.raw), now()]
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
            # column names are fixed above; values are bound.
            f"INSERT INTO activities({','.join(cols)}) VALUES({','.join('?' for _ in cols)}) ON CONFLICT(activity_id) DO UPDATE SET {updates}",  # nosec B608
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
        track, first = activity_geometries(samples, fallback=self._start_coordinates(aid))
        self._write_geometries(
            aid,
            {
                "activity_tracks": track,
                "activity_starts": first,
                "activity_tracks_3d": activity_track_3d(samples),
            },
        )

    def _start_coordinates(self, aid):
        return self.db.execute(
            "SELECT start_longitude,start_latitude FROM activities WHERE activity_id=?", (aid,)
        ).fetchone()

    def _write_geometries(self, aid, geometries):
        # OGR owns GeoPackage geometry encoding and spatial-index triggers.
        self.db.commit()
        ds = ogr.Open(str(self.path), 1)
        ds.StartTransaction()
        try:
            info = self.db.execute(
                "SELECT start_time_local,activity_type,start_longitude,start_latitude FROM activities WHERE activity_id=?",
                (aid,),
            ).fetchone()
            for name, geometry in geometries.items():
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

    def save_activity(
        self, aid: int, detail: dict, samples: Sequence[Sample], messages: Sequence[Message], fmt: str
    ):
        # Invalidate an old checkpoint before either connection commits a refresh.
        # A crash between geometry and attributes must remain visibly incomplete.
        with self.db:
            self.db.execute(
                "UPDATE activities SET samples_synced_at=NULL,geometry_3d_synced_at=NULL WHERE activity_id=?",
                (aid,),
            )
        self.save_geometry(aid, samples)
        with self.db:
            self.db.execute("DELETE FROM activity_samples WHERE activity_id=?", (aid,))
            self.db.executemany(
                "INSERT INTO activity_samples(activity_id,sample_index,timestamp_utc,latitude,longitude,altitude_m,distance_m,speed_mps,heart_rate_bpm,cadence_rpm,power_w,temperature_c,raw_json,segment_index) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (sample_to_row(sample, aid) for sample in samples),
            )
            self.db.execute("DELETE FROM activity_fit_messages WHERE activity_id=?", (aid,))
            self.db.executemany(
                "INSERT INTO activity_fit_messages(activity_id,message_index,message_type,raw_json) VALUES(?,?,?,?)",
                ((aid, m.index, m.message_type, dumps(m.raw)) for m in messages),
            )
            self.db.execute(
                "UPDATE activities SET detail_json=?,samples_synced_at=?,sample_count=?,gps_point_count=?,source_format=?,geometry_3d_synced_at=? WHERE activity_id=?",
                (
                    dumps(detail),
                    now(),
                    len(samples),
                    sum(s.latitude is not None for s in samples),
                    fmt,
                    now(),
                    aid,
                ),
            )
            self.clear_error(f"activity:{aid}")
            self.clear_error(f"geometry3d:{aid}")

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
            window = DateWindow(start, end) if start and end else None
            for normalized in normalize_response(dataset, key, data, window):
                rid, day, record = normalized.record_key, normalized.calendar_date, normalized.raw
                self.db.execute(
                    "INSERT INTO wellness_records(dataset,record_key,calendar_date,payload_json,fetched_at) VALUES(?,?,?,?,?) ON CONFLICT(dataset,record_key) DO UPDATE SET calendar_date=excluded.calendar_date,payload_json=excluded.payload_json,fetched_at=excluded.fetched_at",
                    (dataset, rid, day, dumps(record), now()),
                )
                self.db.execute("DELETE FROM daily_metrics WHERE dataset=? AND record_key=?", (dataset, rid))
                if day:
                    self.db.executemany(
                        "INSERT INTO daily_metrics(dataset,record_key,calendar_date,metric,value) VALUES(?,?,?,?,?)",
                        [(dataset, rid, day, k, v) for k, v in normalized.metrics],
                    )
            self.clear_error(f"{dataset}:{key}")

    def activity_states(self, limit=None):
        query = """SELECT activity_id,start_time_local,samples_synced_at,
          EXISTS(SELECT 1 FROM sync_errors e WHERE e.task='activity:' || activities.activity_id)
          FROM activities ORDER BY start_time_local DESC,activity_id DESC"""
        if limit is not None:
            rows = self.db.execute(query + " LIMIT ?", (limit,))
        else:
            rows = self.db.execute(query)
        return [ActivityState(aid, day, bool(synced), bool(failed)) for aid, day, synced, failed in rows]

    def save_import(self, activity_id, detail, decoded):
        self.save_activity(
            activity_id,
            detail.raw,
            decoded.samples,
            decoded.messages,
            decoded.source_format,
        )

    def pending_3d_count(self):
        if self.schema_version < 3:
            return None
        return self.db.execute(
            "SELECT count(*) FROM activities WHERE samples_synced_at IS NOT NULL "
            "AND geometry_3d_synced_at IS NULL"
        ).fetchone()[0]

    def _stored_geographic_samples(self, aid):
        rows = self.db.execute(
            "SELECT sample_index,timestamp_utc,latitude,longitude,altitude_m,segment_index "
            "FROM activity_samples WHERE activity_id=? ORDER BY sample_index",
            (aid,),
        )
        return [
            Sample(i, stamp, lat, lon, altitude, segment_index=segment)
            for i, stamp, lat, lon, altitude, segment in rows
        ]

    def _rebuild_3d_activity(self, aid):
        with self.db:
            self.db.execute("UPDATE activities SET geometry_3d_synced_at=NULL WHERE activity_id=?", (aid,))
        samples = self._stored_geographic_samples(aid)
        self._write_geometries(aid, {"activity_tracks_3d": activity_track_3d(samples)})
        with self.db:
            self.db.execute("UPDATE activities SET geometry_3d_synced_at=? WHERE activity_id=?", (now(), aid))
            self.clear_error(f"geometry3d:{aid}")

    def rebuild_3d(self, limit=None, refresh=False):
        query = "SELECT activity_id FROM activities WHERE samples_synced_at IS NOT NULL"
        if not refresh:
            query += " AND geometry_3d_synced_at IS NULL"
        query += " ORDER BY start_time_local DESC,activity_id DESC"
        parameters: tuple = ()
        if limit is not None:
            query += " LIMIT ?"
            parameters = (limit,)
        activities = self.db.execute(query, parameters).fetchall()
        completed, failed = 0, 0
        for (aid,) in activities:
            try:
                self._rebuild_3d_activity(aid)
                completed += 1
            except (RuntimeError, ValueError, sqlite3.DatabaseError) as error:
                self.record_failure(f"geometry3d:{aid}", error)
                failed += 1
        return {
            "activities_processed": completed,
            "activities_failed": failed,
            "pending_3d_activities": self.pending_3d_count(),
            "tracks_3d": self.db.execute("SELECT count(*) FROM activity_tracks_3d").fetchone()[0],
        }

    def earliest_activity_date(self):
        value = self.db.execute("SELECT min(start_time_local) FROM activities").fetchone()[0]
        return dt.date.fromisoformat(value[:10]) if value else None

    def first_wellness_date(self):
        value = self.db.execute(
            "SELECT min(calendar_date) FROM wellness_records WHERE dataset='daily' "
            "AND json_extract(payload_json,'$.includesWellnessData')=1"
        ).fetchone()[0]
        return dt.date.fromisoformat(value) if value else None

    def response_windows(self, dataset, start, end):
        return [
            DateWindow(a, b)
            for a, b in self.db.execute(
                "SELECT start_date,end_date FROM api_responses WHERE dataset=? AND start_date<=? "
                "AND end_date>=? ORDER BY start_date",
                (dataset, end, start),
            )
        ]

    def has_response(self, dataset, key):
        return (
            self.db.execute(
                "SELECT 1 FROM api_responses WHERE dataset=? AND request_key=?",
                (dataset, key),
            ).fetchone()
            is not None
        )

    def save_response(self, dataset, key, data, window=None):
        self.response(dataset, key, data, window.start if window else None, window.end if window else None)

    def has_failure(self, task):
        return self.db.execute("SELECT 1 FROM sync_errors WHERE task=?", (task,)).fetchone() is not None

    def record_failure(self, task, error):
        self.error(task, error)

    def clear_failure(self, task):
        with self.db:
            self.clear_error(task)

    def snapshot(self):
        return status(self)


def status(store):
    result: dict = {"database": str(store.path), "schema_version": store.schema_version}
    for name in [
        "activities",
        "activity_samples",
        "activity_tracks",
        "activity_starts",
        "daily_metrics",
        "sync_errors",
    ]:
        # fixed table allowlist above.
        result[name] = store.db.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]  # nosec B608
    if store.schema_version >= 3:
        result["activity_tracks_3d"] = store.db.execute("SELECT count(*) FROM activity_tracks_3d").fetchone()[
            0
        ]
    result["pending_3d_activities"] = store.pending_3d_count()
    result["activities_imported"] = store.db.execute(
        "SELECT count(*) FROM activities WHERE samples_synced_at IS NOT NULL"
    ).fetchone()[0]
    result["activity_dates"] = store.db.execute(
        "SELECT min(start_time_local),max(start_time_local) FROM activities"
    ).fetchone()
    result["health_datasets"] = [
        dict(zip(["dataset", "records", "first_date", "last_date"], row, strict=True))
        for row in store.db.execute(
            "SELECT dataset,count(*),min(calendar_date),max(calendar_date) FROM wellness_records GROUP BY dataset ORDER BY dataset"
        )
    ]
    return result
