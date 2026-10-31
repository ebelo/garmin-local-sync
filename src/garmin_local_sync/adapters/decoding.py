"""Decode original activity files without losing their sample geography."""

import contextlib
import datetime as dt
import io
import math
import xml.etree.ElementTree as ET
import zipfile

import fitdecode

from ..config import UTC
from ..domain.errors import DecodeError
from ..domain.geography import valid_coords
from ..domain.models import DecodedActivity
from ..util import dumps
from .mapping import message_from_row, sample_from_row


def sample_row(aid, index, values, segment=0):
    values = dict(values)
    nonfinite = {k: str(v) for k, v in values.items() if isinstance(v, float) and not math.isfinite(v)}
    if nonfinite:
        values.update({k: None for k in nonfinite})
        values["_nonfinite_values"] = nonfinite
    lat, lon = values.get("latitude"), values.get("longitude")
    if not valid_coords(lat, lon):
        lat, lon = None, None
    timestamp = values.get("timestamp")
    if isinstance(timestamp, dt.datetime):
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        timestamp = timestamp.astimezone(UTC).isoformat()
    altitude = values.get("enhanced_altitude")
    speed = values.get("enhanced_speed")
    return (
        aid,
        index,
        timestamp,
        lat,
        lon,
        altitude if altitude is not None else values.get("altitude"),
        values.get("distance"),
        speed if speed is not None else values.get("speed"),
        values.get("heart_rate"),
        values.get("cadence"),
        values.get("power"),
        values.get("temperature"),
        dumps(values),
        segment,
    )


def parse_fit(content, aid, offset=0):
    samples, messages = [], []
    segment = 0
    with fitdecode.FitReader(io.BytesIO(content), check_crc=fitdecode.CrcCheck.WARN) as reader:
        for frame in reader:
            if not isinstance(frame, fitdecode.FitDataMessage):
                continue
            values = {f.name: f.value for f in frame.fields}
            if frame.name == "record":
                for field in ["position_lat", "position_long"]:
                    value = values.get(field)
                    if value is not None:
                        values["latitude" if field == "position_lat" else "longitude"] = value * 180 / 2**31
                samples.append(sample_row(aid, offset + len(samples), values, segment))
            else:
                if (
                    frame.name == "event"
                    and values.get("event") == "timer"
                    and str(values.get("event_type", "")).startswith("stop")
                ):
                    segment += 1
                messages.append((aid, len(messages), frame.name, dumps(values)))
    return samples, messages


def parse_xml(content, aid, offset=0):
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        raise ValueError("Invalid GPX/TCX XML") from None
    samples = []
    segment_counter = -1

    def points(element, segment=0):
        nonlocal segment_counter
        tag = element.tag.rsplit("}", 1)[-1]
        if tag in ("trkseg", "Track", "rte"):
            segment_counter += 1
            segment = segment_counter
        if tag in ("trkpt", "rtept", "Trackpoint"):
            yield element, segment
        else:
            for child in element:
                yield from points(child, segment)

    for point, segment in points(root):
        values = {}
        if "lat" in point.attrib and "lon" in point.attrib:
            with contextlib.suppress(ValueError):
                values.update(latitude=float(point.attrib["lat"]), longitude=float(point.attrib["lon"]))
        for element in point.iter():
            name = element.tag.rsplit("}", 1)[-1]
            text = element.text.strip() if element.text else None
            if not text and name != "HeartRateBpm":
                continue
            mapping = {
                "LatitudeDegrees": "latitude",
                "LongitudeDegrees": "longitude",
                "ele": "altitude",
                "AltitudeMeters": "altitude",
                "DistanceMeters": "distance",
                "Speed": "speed",
                "hr": "heart_rate",
                "cad": "cadence",
                "Cadence": "cadence",
                "Watts": "power",
            }
            if name in ("time", "Time"):
                stamp = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
                if stamp.tzinfo is None:
                    values["timestamp_unzoned"] = text
                else:
                    values["timestamp"] = stamp
            elif name in mapping:
                with contextlib.suppress(ValueError):
                    values[mapping[name]] = float(text)
            elif name == "HeartRateBpm":
                value = next((e.text for e in element if e.tag.rsplit("}", 1)[-1] == "Value"), None)
                if value is not None:
                    values["heart_rate"] = float(value)
        samples.append(sample_row(aid, offset + len(samples), values, segment))
    return samples, []


def parse_archive(content, aid):
    if not content:
        raise RuntimeError("Empty activity download")
    files = []
    if zipfile.is_zipfile(io.BytesIO(content)):
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            infos = [x for x in archive.infolist() if not x.is_dir()]
            if sum(x.file_size for x in infos) > 512 * 1024**2:
                raise RuntimeError("Activity archive exceeds 512 MB uncompressed limit")
            for ext in (".fit", ".gpx", ".tcx"):
                selected = [x for x in infos if x.filename.lower().endswith(ext)]
                if selected:
                    files = [(ext, archive.read(x)) for x in selected]
                    break
    elif content[8:12] == b".FIT":
        files = [(".fit", content)]
    elif content.lstrip().startswith(b"<") or content.startswith((b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff")):
        files = [(".xml", content)]
    if not files:
        raise RuntimeError("No supported FIT/GPX/TCX file in activity download")
    samples, messages = [], []
    segment_offset = 0
    for ext, blob in files:
        rows, meta = (parse_fit if ext == ".fit" else parse_xml)(blob, aid, len(samples))
        messages.extend([(a, len(messages) + i, typ, data) for i, (a, _, typ, data) in enumerate(meta)])
        # Segment metadata marks file boundaries without inventing sensor records.
        adjusted = [(*row[:13], row[13] + segment_offset) for row in rows]
        samples.extend(adjusted)
        if adjusted:
            segment_offset = max(row[13] for row in adjusted) + 1
    return samples, messages, files[0][0].lstrip(".")


class OriginalActivityDecoder:
    def decode(self, content: bytes, activity_id: int) -> DecodedActivity:
        try:
            samples, messages, fmt = parse_archive(content, activity_id)
            return DecodedActivity(
                tuple(sample_from_row(s) for s in samples),
                tuple(message_from_row(m) for m in messages),
                fmt,
            )
        except (ValueError, RuntimeError, zipfile.BadZipFile, fitdecode.FitError) as error:
            raise DecodeError(str(error)) from None
