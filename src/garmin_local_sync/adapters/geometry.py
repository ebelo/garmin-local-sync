"""GDAL encoding of pure geographic values; never hand-encode GeoPackage blobs."""

from collections.abc import Sequence

from osgeo import ogr

from ..domain.geography import build_geography
from ..domain.models import Coordinate, Sample


def activity_geometries(samples: Sequence[Sample], fallback=None):
    origin = Coordinate(*fallback) if fallback else None
    geography = build_geography(samples, origin)
    track = None
    if geography.segments:
        track = ogr.Geometry(ogr.wkbMultiLineString)
        for segment in geography.segments:
            line = ogr.Geometry(ogr.wkbLineString)
            for point in segment:
                line.AddPoint_2D(point.longitude, point.latitude)
            track.AddGeometry(line)
    start = None
    if geography.start is not None:
        start = ogr.Geometry(ogr.wkbPoint)
        start.AddPoint_2D(geography.start.longitude, geography.start.latitude)
    return track, start


ELEVATION_METADATA = {
    "horizontal_crs": "EPSG:4326",
    "elevation_unit": "m",
    "elevation_source": "activity_samples.altitude_m",
    "elevation_field_provenance": "FIT enhanced_altitude then altitude; GPX ele; TCX AltitudeMeters",
    "source_provenance": "activity_samples.raw_json and retained original files",
    "vertical_crs": "EPSG:5714",
    "vertical_crs_name": "MSL height",
    "vertical_crs_assignment": "descriptive assumed MSL interpretation; not source-certified",
    "exact_source_vertical_datum": "not identified by source fields",
    "vertical_transformation": "none; recorded heights preserved",
    "measurement_method": "GPS/barometric/corrected source not inferred",
    "missing_elevation_policy": "split; no zero fill or gap interpolation",
    "date_line_boundary_policy": "derived linear height interpolation; not a source observation",
}

SPATIAL_LAYERS = (
    ("activity_tracks", ogr.wkbMultiLineString),
    ("activity_starts", ogr.wkbPoint),
    ("activity_tracks_3d", ogr.wkbMultiLineString25D),
)


def activity_track_3d(samples: Sequence[Sample]):
    geography = build_geography(samples, with_elevation=True)
    if not geography.segments:
        return None
    track = ogr.Geometry(ogr.wkbMultiLineString25D)
    for segment in geography.segments:
        line = ogr.Geometry(ogr.wkbLineString25D)
        for point in segment:
            line.AddPoint(point.longitude, point.latitude, point.altitude_m)
        track.AddGeometry(line)
    return track
