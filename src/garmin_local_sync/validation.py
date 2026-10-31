"""Read-only checks of SQLite integrity and the geographic storage contract."""

import math

from osgeo import ogr


def check_store(store):
    db = store.db
    errors = []
    integrity = db.execute("PRAGMA integrity_check").fetchall()
    if integrity != [("ok",)]:
        errors.append("SQLite integrity check failed")
    foreign_keys = db.execute("PRAGMA foreign_key_check").fetchall()
    if foreign_keys:
        errors.append(f"{len(foreign_keys)} foreign-key violations")
    for table in (
        "activities",
        "activity_samples",
        "activity_fit_messages",
        "api_responses",
        "wellness_records",
        "daily_metrics",
        "sync_errors",
    ):
        registered = db.execute(
            "SELECT data_type,srs_id FROM gpkg_contents WHERE table_name=?", (table,)
        ).fetchone()
        if registered != ("attributes", None):
            errors.append(f"{table}: missing or invalid attribute-table registration")
    invalid = db.execute("""SELECT count(*) FROM activity_samples WHERE
        (latitude IS NULL) != (longitude IS NULL) OR latitude < -90 OR latitude > 90
        OR longitude < -180 OR longitude > 180""").fetchone()[0]
    if invalid:
        errors.append(f"{invalid} samples have invalid coordinate pairs")
    counts = db.execute("""SELECT a.activity_id FROM activities a LEFT JOIN
        (SELECT activity_id,count(*) AS n,sum(latitude IS NOT NULL) AS gps
         FROM activity_samples GROUP BY activity_id) s USING(activity_id)
        WHERE a.samples_synced_at IS NOT NULL AND
        (a.sample_count != coalesce(s.n,0) OR a.gps_point_count != coalesce(s.gps,0))""").fetchall()
    if counts:
        errors.append(f"{len(counts)} completed activities have inconsistent sample counts")

    layers = {}
    ds = ogr.Open(str(store.path), 0)
    if ds is None:
        raise RuntimeError("GDAL could not open the GeoPackage")
    for name, expected in [("activity_tracks", ogr.wkbMultiLineString), ("activity_starts", ogr.wkbPoint)]:
        layer = ds.GetLayerByName(name)
        if layer is None:
            errors.append(f"{name}: missing spatial layer")
            continue
        layers[name] = layer.GetFeatureCount()
        registration = db.execute(
            "SELECT geometry_type_name,srs_id,z,m FROM gpkg_geometry_columns WHERE table_name=?", (name,)
        ).fetchone()
        wanted = ("MULTILINESTRING" if expected == ogr.wkbMultiLineString else "POINT", 4326, 0, 0)
        if registration != wanted:
            errors.append(f"{name}: invalid geometry/CRS/dimension registration")
        srs = layer.GetSpatialRef()
        if srs is None or srs.GetAuthorityCode(None) != "4326":
            errors.append(f"{name}: CRS is not EPSG:4326")
        orphans = db.execute(
            f'SELECT count(*) FROM "{name}" g LEFT JOIN activities a USING(activity_id) WHERE a.activity_id IS NULL'
        ).fetchone()[0]
        if orphans:
            errors.append(f"{name}: {orphans} orphan activity links")
        rtree = f"rtree_{name}_{layer.GetGeometryColumn()}"
        has_index = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (rtree,)
        ).fetchone()
        if not has_index:
            errors.append(f"{name}: missing spatial index")
        elif db.execute(f'SELECT count(*) FROM "{rtree}"').fetchone()[0] != layers[name]:
            errors.append(f"{name}: spatial-index count differs from feature count")
        for feature in layer:
            geom = feature.GetGeometryRef()
            label = f"{name} feature {feature.GetFID()}"
            if geom is None or geom.IsEmpty() or not geom.IsValid():
                errors.append(f"{label}: missing, empty, or invalid geometry")
                continue
            if geom.GetGeometryType() != expected:
                errors.append(f"{label}: incorrect geometry type or dimensions")
            envelope = geom.GetEnvelope()
            if not all(math.isfinite(x) for x in envelope) or not (
                -180 <= envelope[0] <= envelope[1] <= 180 and -90 <= envelope[2] <= envelope[3] <= 90
            ):
                errors.append(f"{label}: geometry outside valid longitude/latitude bounds")
            if has_index:
                bounds = db.execute(
                    f'SELECT minx,maxx,miny,maxy FROM "{rtree}" WHERE id=?', (feature.GetFID(),)
                ).fetchone()
                if bounds is None or not (
                    bounds[0] <= envelope[0]
                    and bounds[1] >= envelope[1]
                    and bounds[2] <= envelope[2]
                    and bounds[3] >= envelope[3]
                ):
                    errors.append(f"{label}: spatial index does not contain the geometry envelope")
            if expected == ogr.wkbMultiLineString:
                for i in range(geom.GetGeometryCount()):
                    points = geom.GetGeometryRef(i).GetPoints()
                    if any(abs(a[0] - b[0]) > 180 for a, b in zip(points, points[1:])):
                        errors.append(f"{label}: unsplit date-line crossing")
    ds = None
    return {
        "valid": not errors,
        "integrity": integrity[0][0],
        "foreign_key_errors": len(foreign_keys),
        "geographic_layers": layers,
        "errors": errors,
        "incomplete_activities": db.execute(
            "SELECT count(*) FROM activities WHERE samples_synced_at IS NULL"
        ).fetchone()[0],
    }
