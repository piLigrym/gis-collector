from __future__ import annotations

import gzip

import pytest
from mvt_builder import (
    LINESTRING,
    POINT,
    POLYGON,
    encode_tile,
    line_geometry,
    multipolygon_geometry,
    point_geometry,
    polygon_geometry,
)

from gis_collector.exceptions import DecodeError
from gis_collector.geo.mvt import decode_tile
from gis_collector.geo.tiles import tile_bounds


def _tile(features: list[dict], name: str = "layer") -> bytes:
    return encode_tile([{"name": name, "extent": 4096, "features": features}])


def test_point_projection_and_property_types() -> None:
    data = _tile(
        [
            {
                "id": 7,
                "type": POINT,
                "geometry": point_geometry([(2048, 2048)]),
                "props": {"s": "text", "n": 3, "f": 1.5, "b": True},
            }
        ]
    )
    [feature] = decode_tile(data, 0, 0, 0)
    assert feature.feature_id == 7
    assert feature.properties == {"s": "text", "n": 3, "f": 1.5, "b": True}
    lon, lat = feature.geometry["coordinates"]
    assert lon == pytest.approx(0.0) and lat == pytest.approx(0.0)


def test_multipoint_and_line() -> None:
    data = _tile(
        [
            {"type": POINT, "geometry": point_geometry([(0, 0), (4096, 4096)])},
            {"type": LINESTRING, "geometry": line_geometry([(0, 0), (100, 100), (200, 0)])},
        ]
    )
    points, line = decode_tile(data, 5, 3, 2)
    assert points.geometry["type"] == "MultiPoint" and len(points.geometry["coordinates"]) == 2
    assert line.geometry["type"] == "LineString" and len(line.geometry["coordinates"]) == 3


def test_polygon_with_hole_and_multipolygon() -> None:
    outer = [(0, 0), (4096, 0), (4096, 4096), (0, 4096)]
    hole = [(1000, 1000), (2000, 1000), (2000, 2000), (1000, 2000)]
    data = _tile(
        [
            {"type": POLYGON, "geometry": polygon_geometry([outer, hole])},
            {"type": POLYGON, "geometry": multipolygon_geometry([[outer], [hole]])},
        ]
    )
    single, multi = decode_tile(data, 11, 1205, 688)
    assert single.geometry["type"] == "Polygon" and len(single.geometry["coordinates"]) == 2
    assert single.geometry["coordinates"][0][0] == single.geometry["coordinates"][0][-1]
    assert multi.geometry["type"] == "MultiPolygon" and len(multi.geometry["coordinates"]) == 2


def test_coordinates_fall_inside_tile_bounds() -> None:
    z, x, y = 11, 1205, 688
    data = _tile([{"type": POINT, "geometry": point_geometry([(10, 4000)])}])
    [feature] = decode_tile(data, z, x, y)
    west, south, east, north = tile_bounds(z, x, y)
    lon, lat = feature.geometry["coordinates"]
    assert west <= lon <= east and south <= lat <= north


def test_gzip_and_layer_filter() -> None:
    raw = encode_tile(
        [
            {"name": "a", "features": [{"type": POINT, "geometry": point_geometry([(1, 1)])}]},
            {"name": "b", "features": [{"type": POINT, "geometry": point_geometry([(2, 2)])}]},
        ]
    )
    result = decode_tile(gzip.compress(raw), 1, 0, 0, layers={"b"})
    assert [f.layer for f in result] == ["b"]


def test_malformed_input() -> None:
    with pytest.raises(DecodeError):
        decode_tile(b"\x1a\xff\xff", 0, 0, 0)
    with pytest.raises(DecodeError):
        decode_tile(b"\x1f\x8bnot-gzip", 0, 0, 0)
    assert decode_tile(b"", 0, 0, 0) == []
