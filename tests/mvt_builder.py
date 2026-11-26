"""Minimal MVT *encoder* used only by the tests (the package ships a decoder only)."""

from __future__ import annotations

import struct
from typing import Any

Ring = list[tuple[int, int]]

POINT, LINESTRING, POLYGON = 1, 2, 3


def _varint(value: int) -> bytes:
    out = bytearray()
    value &= (1 << 64) - 1
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _key(field: int, wire: int) -> bytes:
    return _varint((field << 3) | wire)


def _len_delimited(field: int, payload: bytes) -> bytes:
    return _key(field, 2) + _varint(len(payload)) + payload


def _zigzag(value: int) -> int:
    return (value << 1) ^ (value >> 63)


def _command(cmd: int, count: int) -> int:
    return (count << 3) | cmd


def _area2(ring: Ring) -> int:
    closed = ring + [ring[0]]
    return sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(closed, closed[1:], strict=False))


def point_geometry(points: list[tuple[int, int]]) -> list[int]:
    commands = [_command(1, len(points))]
    x = y = 0
    for px, py in points:
        commands += [_zigzag(px - x), _zigzag(py - y)]
        x, y = px, py
    return commands


def line_geometry(line: list[tuple[int, int]]) -> list[int]:
    commands = [_command(1, 1), _zigzag(line[0][0]), _zigzag(line[0][1])]
    commands.append(_command(2, len(line) - 1))
    for (x1, y1), (x2, y2) in zip(line, line[1:], strict=False):
        commands += [_zigzag(x2 - x1), _zigzag(y2 - y1)]
    return commands


def polygon_geometry(rings: list[Ring]) -> list[int]:
    """One polygon: ring 0 is the exterior, the others are holes."""
    return multipolygon_geometry([rings])


def multipolygon_geometry(polygons: list[list[Ring]]) -> list[int]:
    commands: list[int] = []
    x = y = 0
    for rings in polygons:
        for index, ring in enumerate(rings):
            wanted_positive = index == 0
            if (_area2(ring) > 0) != wanted_positive:
                ring = list(reversed(ring))
            commands += [_command(1, 1), _zigzag(ring[0][0] - x), _zigzag(ring[0][1] - y)]
            x, y = ring[0]
            commands.append(_command(2, len(ring) - 1))
            for px, py in ring[1:]:
                commands += [_zigzag(px - x), _zigzag(py - y)]
                x, y = px, py
            commands.append(_command(7, 1))
    return commands


def _value(value: Any) -> bytes:
    if isinstance(value, bool):
        return _key(7, 0) + _varint(int(value))
    if isinstance(value, str):
        return _len_delimited(1, value.encode())
    if isinstance(value, float):
        return _key(3, 1) + struct.pack("<d", value)
    if isinstance(value, int):
        return _key(4, 0) + _varint(value)
    raise TypeError(value)


def encode_tile(layers: list[dict[str, Any]]) -> bytes:
    """``layers``: ``[{"name", "extent", "features": [{"id", "type", "geometry", "props"}]}]``."""
    tile = b""
    for layer in layers:
        keys: list[str] = []
        values: list[Any] = []
        features = b""
        for feature in layer["features"]:
            tags: list[int] = []
            for k, v in feature.get("props", {}).items():
                if k not in keys:
                    keys.append(k)
                if v not in values or type(v) is not type(values[values.index(v)]):
                    values.append(v)
                    value_index = len(values) - 1
                else:
                    value_index = values.index(v)
                tags += [keys.index(k), value_index]
            body = b""
            if feature.get("id") is not None:
                body += _key(1, 0) + _varint(feature["id"])
            if tags:
                body += _len_delimited(2, b"".join(_varint(t) for t in tags))
            body += _key(3, 0) + _varint(feature["type"])
            body += _len_delimited(4, b"".join(_varint(c) for c in feature["geometry"]))
            features += _len_delimited(2, body)
        payload = _len_delimited(1, layer["name"].encode()) + features
        payload += b"".join(_len_delimited(3, k.encode()) for k in keys)
        payload += b"".join(_len_delimited(4, _value(v)) for v in values)
        payload += _key(5, 0) + _varint(layer.get("extent", 4096))
        payload += _key(15, 0) + _varint(2)
        tile += _len_delimited(3, payload)
    return tile
