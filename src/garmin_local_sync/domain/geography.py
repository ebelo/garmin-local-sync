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


def date_line_edge(previous: Coordinate, point: Coordinate) -> Coordinate:
    adjusted_lon = point.longitude + (360 if point.longitude < previous.longitude else -360)
    boundary = 180.0 if adjusted_lon > previous.longitude else -180.0
    fraction = (boundary - previous.longitude) / (adjusted_lon - previous.longitude)
    latitude = previous.latitude + fraction * (point.latitude - previous.latitude)
    altitude = None
    if previous.altitude_m is not None and point.altitude_m is not None:
        altitude = (1 - fraction) * previous.altitude_m + fraction * point.altitude_m
    return Coordinate(boundary, latitude, altitude)


class TrackBuilder:
    """Accumulate valid paths, retaining gaps and splitting date-line crossings."""

    def __init__(self):
        self.tracks: list[tuple[Coordinate, ...]] = []
        self.points: list[Coordinate] = []
        self.first: Coordinate | None = None

    def finish(self):
        if len(self.points) >= 2:
            self.tracks.append(tuple(self.points))
        self.points = []

    def add(self, point: Coordinate):
        if self.first is None:
            self.first = point
        if self.points and abs(point.longitude - self.points[-1].longitude) > 180:
            edge = date_line_edge(self.points[-1], point)
            if self.points[-1] != edge:
                self.points.append(edge)
            self.finish()
            self.points = [Coordinate(-edge.longitude, edge.latitude, edge.altitude_m)]
        if not self.points or self.points[-1] != point:
            self.points.append(point)


def starts_new_segment(sample: Sample, previous_segment, previous_time):
    return (previous_segment is not None and sample.segment_index != previous_segment) or (
        sample.timestamp_utc and previous_time and sample.timestamp_utc < previous_time
    )


def valid_altitude(value):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def build_geography(
    samples: Iterable[Sample], fallback: Coordinate | None = None, *, with_elevation: bool = False
) -> ActivityGeography:
    builder = TrackBuilder()
    previous_segment = None
    previous_time = None

    for sample in samples:
        timestamp, lat, lon = sample.timestamp_utc, sample.latitude, sample.longitude
        segment = sample.segment_index
        if starts_new_segment(sample, previous_segment, previous_time):
            builder.finish()
        previous_segment, previous_time = segment, timestamp
        if lat is None or lon is None or not valid_coords(lat, lon):
            builder.finish()
            continue
        if with_elevation and not valid_altitude(sample.altitude_m):
            builder.finish()
            continue
        builder.add(Coordinate(lon, lat, sample.altitude_m if with_elevation else None))
    builder.finish()
    if (
        not with_elevation
        and builder.first is None
        and fallback
        and valid_coords(fallback.latitude, fallback.longitude)
    ):
        builder.first = fallback
    return ActivityGeography(tuple(builder.tracks), builder.first)
