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
