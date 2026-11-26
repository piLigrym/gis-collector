"""Dependency-free Mapbox Vector Tile (MVT / PBF) decoder.

Implements the subset of the Protocol Buffers wire format used by the MVT spec
v1/v2 (https://github.com/mapbox/vector-tile-spec). Geometry is returned as
GeoJSON in WGS84, already projected from tile coordinates.
"""

from __future__ import annotations

import gzip
import math
import struct
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from gis_collector.exceptions import DecodeError
from gis_collector.geo.tiles import tile_point_to_lonlat

_GEOM_POINT, _GEOM_LINE, _GEOM_POLYGON = 1, 2, 3
_CMD_MOVE_TO, _CMD_LINE_TO, _CMD_CLOSE = 1, 2, 7


@dataclass(frozen=True, slots=True)
class DecodedFeature:
    layer: str
    feature_id: int | None
    properties: dict[str, Any]
    geometry: dict[str, Any] | None  # GeoJSON, WGS84


@dataclass(slots=True)
class _RawLayer:
    name: str = ""
    extent: int = 4096
    keys: list[str] = field(default_factory=list)
    values: list[Any] = field(default_factory=list)
    features: list[bytes] = field(default_factory=list)


# ------------------------------------------------------------------ protobuf basics


def _varint(buf: bytes, pos: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while True:
        if pos >= len(buf):
            raise DecodeError("Truncated varint in MVT")
        byte = buf[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, pos
        shift += 7
        if shift > 70:
            raise DecodeError("Varint too long in MVT")


def _fields(buf: bytes) -> Iterator[tuple[int, int, int | bytes]]:
    """Yield ``(field_number, wire_type, value)``; value is int or raw bytes."""
    pos = 0
    while pos < len(buf):
        key, pos = _varint(buf, pos)
        field_no, wire = key >> 3, key & 7
        if wire == 0:
            value, pos = _varint(buf, pos)
            yield field_no, wire, value
        elif wire == 2:
            length, pos = _varint(buf, pos)
            if pos + length > len(buf):
                raise DecodeError("Truncated length-delimited field in MVT")
            yield field_no, wire, buf[pos : pos + length]
            pos += length
        elif wire == 1:
            yield field_no, wire, buf[pos : pos + 8]
            pos += 8
        elif wire == 5:
            yield field_no, wire, buf[pos : pos + 4]
            pos += 4
        else:
            raise DecodeError(f"Unsupported protobuf wire type {wire}")


def _packed_varints(buf: bytes) -> list[int]:
    out: list[int] = []
    pos = 0
    while pos < len(buf):
        value, pos = _varint(buf, pos)
        out.append(value)
    return out


def _zigzag(value: int) -> int:
    return (value >> 1) ^ -(value & 1)


def _decode_value(buf: bytes) -> Any:
    for field_no, _wire, raw in _fields(buf):
        if field_no == 1 and isinstance(raw, bytes):
            return raw.decode("utf-8", errors="replace")
        if field_no == 2 and isinstance(raw, bytes):
            return struct.unpack("<f", raw)[0]
        if field_no == 3 and isinstance(raw, bytes):
            return struct.unpack("<d", raw)[0]
        if field_no == 4 and isinstance(raw, int):  # int64, two's complement
            return raw - (1 << 64) if raw >= (1 << 63) else raw
        if field_no == 5 and isinstance(raw, int):  # uint64
            return raw
        if field_no == 6 and isinstance(raw, int):  # sint64
            return _zigzag(raw)
        if field_no == 7 and isinstance(raw, int):  # bool
            return bool(raw)
    return None


# ----------------------------------------------------------------------- tile level


def _parse_layer(buf: bytes) -> _RawLayer:
    layer = _RawLayer()
    for field_no, _wire, raw in _fields(buf):
        if field_no == 1 and isinstance(raw, bytes):
            layer.name = raw.decode("utf-8", errors="replace")
        elif field_no == 2 and isinstance(raw, bytes):
            layer.features.append(raw)
        elif field_no == 3 and isinstance(raw, bytes):
            layer.keys.append(raw.decode("utf-8", errors="replace"))
        elif field_no == 4 and isinstance(raw, bytes):
            layer.values.append(_decode_value(raw))
        elif field_no == 5 and isinstance(raw, int):
            layer.extent = raw
    return layer


def _parse_feature(
    buf: bytes, layer: _RawLayer
) -> tuple[int | None, dict[str, Any], int, list[int]]:
    feature_id: int | None = None
    geom_type = 0
    tags: list[int] = []
    commands: list[int] = []
    for field_no, _wire, raw in _fields(buf):
        if field_no == 1 and isinstance(raw, int):
            feature_id = raw
        elif field_no == 2 and isinstance(raw, bytes):
            tags = _packed_varints(raw)
        elif field_no == 3 and isinstance(raw, int):
            geom_type = raw
        elif field_no == 4 and isinstance(raw, bytes):
            commands = _packed_varints(raw)
    properties: dict[str, Any] = {}
    for key_index, value_index in zip(tags[0::2], tags[1::2], strict=False):
        if key_index < len(layer.keys) and value_index < len(layer.values):
            properties[layer.keys[key_index]] = layer.values[value_index]
    return feature_id, properties, geom_type, commands


Ring = list[tuple[int, int]]


def _decode_paths(commands: list[int]) -> list[tuple[Ring, bool]]:
    """Execute MVT drawing commands → list of ``(points, closed)`` paths."""
    paths: list[tuple[Ring, bool]] = []
    current: Ring = []
    x = y = 0
    i = 0
    while i < len(commands):
        header = commands[i]
        i += 1
        cmd, count = header & 7, header >> 3
        if cmd == _CMD_CLOSE:
            if current:
                current.append(current[0])
                paths.append((current, True))
                current = []
            continue
        for _ in range(count):
            if i + 1 >= len(commands):
                raise DecodeError("Truncated MVT geometry")
            x += _zigzag(commands[i])
            y += _zigzag(commands[i + 1])
            i += 2
            if cmd == _CMD_MOVE_TO:
                if current:
                    paths.append((current, False))
                current = [(x, y)]
            elif cmd == _CMD_LINE_TO:
                current.append((x, y))
            else:
                raise DecodeError(f"Unknown MVT command {cmd}")
    if current:
        paths.append((current, False))
    return paths


def _signed_area2(ring: Ring) -> int:
    """Twice the signed area (shoelace). Positive ⇒ exterior ring in tile space."""
    return sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(ring, ring[1:], strict=False))


def _geometry_to_geojson(
    geom_type: int, commands: list[int], z: int, x: int, y: int, extent: int
) -> dict[str, Any] | None:
    paths = _decode_paths(commands)
    if not paths:
        return None

    def project(ring: Ring) -> list[list[float]]:
        return [list(tile_point_to_lonlat(z, x, y, px, py, extent)) for px, py in ring]

    if geom_type == _GEOM_POINT:
        # A MoveTo with count > 1 yields several single-point paths.
        points = [p for path, _ in paths for p in project(path)]
        if len(points) == 1:
            return {"type": "Point", "coordinates": points[0]}
        return {"type": "MultiPoint", "coordinates": points}

    if geom_type == _GEOM_LINE:
        lines = [project(path) for path, _ in paths if len(path) >= 2]
        if not lines:
            return None
        if len(lines) == 1:
            return {"type": "LineString", "coordinates": lines[0]}
        return {"type": "MultiLineString", "coordinates": lines}

    if geom_type == _GEOM_POLYGON:
        polygons: list[list[Ring]] = []
        for path, _closed in paths:
            if len(path) < 4:
                continue
            if _signed_area2(path) > 0 or not polygons:
                polygons.append([path])  # exterior ring starts a new polygon
            else:
                polygons[-1].append(path)  # interior ring (hole)
        if not polygons:
            return None
        projected = [[project(ring) for ring in poly] for poly in polygons]
        if len(projected) == 1:
            return {"type": "Polygon", "coordinates": projected[0]}
        return {"type": "MultiPolygon", "coordinates": projected}

    return None


def decode_tile(
    data: bytes,
    z: int,
    x: int,
    y: int,
    *,
    layers: set[str] | None = None,
) -> list[DecodedFeature]:
    """Decode an MVT tile into features (geometry in WGS84 GeoJSON).

    Args:
        data: raw tile bytes (gzip-compressed input is detected automatically).
        z, x, y: tile address, needed to project tile coordinates.
        layers: restrict decoding to these layer names (``None`` = all).
    """
    if data[:2] == b"\x1f\x8b":
        try:
            data = gzip.decompress(data)
        except (OSError, EOFError) as exc:
            raise DecodeError(f"Corrupt gzip tile: {exc}") from exc
    result: list[DecodedFeature] = []
    try:
        for field_no, _wire, raw in _fields(data):
            if field_no != 3 or not isinstance(raw, bytes):
                continue
            layer = _parse_layer(raw)
            if layers is not None and layer.name not in layers:
                continue
            for feature_bytes in layer.features:
                feature_id, props, geom_type, commands = _parse_feature(feature_bytes, layer)
                geometry = _geometry_to_geojson(geom_type, commands, z, x, y, layer.extent)
                result.append(DecodedFeature(layer.name, feature_id, props, geometry))
    except (struct.error, IndexError, ValueError, OverflowError) as exc:
        raise DecodeError(f"Malformed vector tile {z}/{x}/{y}: {exc}") from exc
    return result


def is_finite_number(value: Any) -> bool:
    return isinstance(value, int | float) and math.isfinite(value)
