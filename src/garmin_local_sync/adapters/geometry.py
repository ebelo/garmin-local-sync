"""GDAL encoding of pure geographic values; never hand-encode GeoPackage blobs."""

from osgeo import ogr

from ..domain.geography import build_geography
from ..domain.models import Coordinate, Sample


def activity_geometries(samples, fallback=None):
    # Geometry needs only position/order/segment values, not a second JSON decode
    # of every sensor payload in the legacy storage representation.
    observations = (
        s
        if isinstance(s, Sample)
        else Sample(
            s[1],
            timestamp_utc=s[2],
            latitude=s[3],
            longitude=s[4],
            segment_index=s[13] if len(s) > 13 else 0,
        )
        for s in samples
    )
    origin = Coordinate(*fallback) if fallback else None
    geography = build_geography(observations, origin)
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
