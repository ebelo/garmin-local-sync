"""Pure geographic rules: ordered observations to segmented longitude/latitude paths."""

import math
from collections.abc import Iterable

from .models import ActivityGeography, Coordinate, Sample


def valid_coords(lat, lon):
    return (
        not isinstance(lat, bool)
        and not isinstance(lon, bool)
        and isinstance(lat, (int, float))
        and isinstance(lon, (int, float))
        and math.isfinite(lat)
        and math.isfinite(lon)
        and -90 <= lat <= 90
        and -180 <= lon <= 180
    )


def build_geography(samples: Iterable[Sample], fallback: Coordinate | None = None) -> ActivityGeography:
    tracks: list[tuple[Coordinate, ...]] = []
    points: list[Coordinate] = []
    first = None
    previous_segment = None
    previous_time = None

    def finish():
        nonlocal points
        if len(points) >= 2:
            tracks.append(tuple(points))
        points = []

    for sample in samples:
        timestamp, lat, lon = sample.timestamp_utc, sample.latitude, sample.longitude
        segment = sample.segment_index
        if (previous_segment is not None and segment != previous_segment) or (
            timestamp and previous_time and timestamp < previous_time
        ):
            finish()
        previous_segment, previous_time = segment, timestamp
        if lat is None or lon is None or not valid_coords(lat, lon):
            finish()
            continue
        point = Coordinate(lon, lat)
        if first is None:
            first = point
        if points and abs(lon - points[-1].longitude) > 180:
            old_lon, old_lat = points[-1].longitude, points[-1].latitude
            adjusted_lon = lon + (360 if lon < old_lon else -360)
            boundary = 180.0 if adjusted_lon > old_lon else -180.0
            fraction = (boundary - old_lon) / (adjusted_lon - old_lon)
            boundary_lat = old_lat + fraction * (lat - old_lat)
            edge = Coordinate(boundary, boundary_lat)
            if points[-1] != edge:
                points.append(edge)
            finish()
            points = [Coordinate(-boundary, boundary_lat)]
        if not points or points[-1] != point:
            points.append(point)
    finish()
    if first is None and fallback and valid_coords(fallback.latitude, fallback.longitude):
        first = fallback
    return ActivityGeography(tuple(tracks), first)
