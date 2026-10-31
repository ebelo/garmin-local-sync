"""Faithful EPSG:4326 geometry from ordered, segmented activity samples."""

from osgeo import ogr

from .formats import valid_coords


def activity_geometries(samples, fallback=None):
    """Return a 2D MultiLineString and first valid point, without bridging gaps.

    Samples retain elevation and every repeated position in the attribute table.
    Geometry omits consecutive duplicate coordinates and splits at the date line;
    interpolated +/-180 degree boundary vertices exist only in the geometry.
    """
    tracks = ogr.Geometry(ogr.wkbMultiLineString)
    points = []
    first = None
    previous_segment = None
    previous_time = None

    def finish():
        nonlocal points
        if len(points) >= 2:
            line = ogr.Geometry(ogr.wkbLineString)
            for lon, lat in points:
                line.AddPoint_2D(lon, lat)
            tracks.AddGeometry(line)
        points = []

    for sample in samples:
        timestamp, lat, lon = sample[2:5]
        segment = sample[13] if len(sample) > 13 else 0
        if (previous_segment is not None and segment != previous_segment) or (
            timestamp and previous_time and timestamp < previous_time
        ):
            finish()
        previous_segment, previous_time = segment, timestamp
        if not valid_coords(lat, lon):
            finish()
            continue
        point = (lon, lat)
        if first is None:
            first = point
        if points and abs(lon - points[-1][0]) > 180:
            old_lon, old_lat = points[-1]
            adjusted_lon = lon + (360 if lon < old_lon else -360)
            boundary = 180.0 if adjusted_lon > old_lon else -180.0
            fraction = (boundary - old_lon) / (adjusted_lon - old_lon)
            boundary_lat = old_lat + fraction * (lat - old_lat)
            edge = (boundary, boundary_lat)
            if points[-1] != edge:
                points.append(edge)
            finish()
            points = [(-boundary, boundary_lat)]
        if not points or points[-1] != point:
            points.append(point)
    finish()
    if first is None and fallback and valid_coords(fallback[1], fallback[0]):
        first = fallback
    start = None
    if first is not None:
        start = ogr.Geometry(ogr.wkbPoint)
        start.AddPoint_2D(*first)
    return tracks if tracks.GetGeometryCount() else None, start
