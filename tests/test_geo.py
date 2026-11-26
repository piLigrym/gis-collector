from __future__ import annotations

import pytest
from shapely.geometry import box, shape

from gis_collector.exceptions import GeometryError
from gis_collector.geo.cmr import granule_geometry
from gis_collector.geo.geojson import normalize_geometry, representative_lonlat
from gis_collector.geo.overpass import element_geometry
from gis_collector.geo.tiles import lonlat_to_tile, tile_bounds, tiles_covering


def ring(lon: float, lat: float, size: float) -> list[dict[str, float]]:
    return [
        {"lat": lat, "lon": lon},
        {"lat": lat, "lon": lon + size},
        {"lat": lat + size, "lon": lon + size},
        {"lat": lat + size, "lon": lon},
        {"lat": lat, "lon": lon},
    ]


# ------------------------------------------------------------------- tiles


def test_tile_roundtrip() -> None:
    x, y = lonlat_to_tile(31.85, 50.7, 11)
    west, south, east, north = tile_bounds(11, x, y)
    assert west <= 31.85 <= east and south <= 50.7 <= north
    assert lonlat_to_tile(0.0, 0.0, 1) == (1, 1)


def test_spec_sample_tile_intersects_chernihiv_bbox() -> None:
    region = box(30.49, 50.46, 33.50, 52.38)
    assert (11, 1205, 688) in tiles_covering(region, 11, max_tiles=10_000)


def test_tiles_covering_limit() -> None:
    with pytest.raises(GeometryError):
        tiles_covering(box(30.49, 50.46, 33.50, 52.38), 11, max_tiles=5)


# --------------------------------------------------------------- overpass


def test_node_and_open_way() -> None:
    assert element_geometry({"type": "node", "lat": 51.0, "lon": 31.0}) == {
        "type": "Point",
        "coordinates": [31.0, 51.0],
    }
    way = {"type": "way", "tags": {"hazard": "x"}, "geometry": ring(31, 51, 0.1)}
    assert element_geometry(way)["type"] == "LineString"  # closed but not an area feature


def test_closed_way_area_vs_line() -> None:
    way = {"type": "way", "tags": {"landuse": "landfill"}, "geometry": ring(31, 51, 0.1)}
    assert element_geometry(way)["type"] == "Polygon"
    way["tags"] = {"landuse": "landfill", "area": "no"}
    assert element_geometry(way)["type"] == "LineString"


def test_multipolygon_with_hole_and_split_outer() -> None:
    outer = ring(32.0, 51.0, 0.1)
    members = [
        # outer ring split in two ways that must be stitched together
        {"type": "way", "role": "outer", "geometry": outer[:3]},
        {"type": "way", "role": "outer", "geometry": outer[2:]},
        {"type": "way", "role": "inner", "geometry": ring(32.04, 51.04, 0.02)},
    ]
    geometry = element_geometry(
        {
            "type": "relation",
            "tags": {"type": "multipolygon", "landuse": "military"},
            "members": members,
        }
    )
    polygon = shape(geometry)
    assert geometry["type"] == "Polygon" and len(geometry["coordinates"]) == 2
    assert polygon.area == pytest.approx(0.1 * 0.1 - 0.02 * 0.02)


def test_non_area_relation_becomes_collection_and_nulls_are_ignored() -> None:
    relation = {
        "type": "relation",
        "tags": {"hazard": "x"},
        "members": [
            {"type": "node", "lat": 51.0, "lon": 31.0},
            {
                "type": "way",
                "role": "",
                "geometry": [{"lat": 51, "lon": 31}, None, {"lat": 51.1, "lon": 31.1}],
            },
        ],
    }
    assert element_geometry(relation)["type"] == "GeometryCollection"
    assert element_geometry({"type": "relation", "tags": {}, "members": []}) is None


# --------------------------------------------------------------------- cmr


def test_cmr_lat_lon_order_is_swapped_to_lon_lat() -> None:
    geometry = granule_geometry({"polygons": [["50.5 31.0 50.7 31.0 50.7 31.5 50.5 31.0"]]})
    assert geometry["type"] == "Polygon"
    assert geometry["coordinates"][0][0] == [31.0, 50.5]


def test_cmr_boxes_points_and_empty() -> None:
    assert granule_geometry({"boxes": ["50 31 51 32"]})["type"] == "Polygon"
    assert granule_geometry({"points": ["50.5 31.5"]}) == {
        "type": "Point",
        "coordinates": [31.5, 50.5],
    }
    assert granule_geometry({}) is None
    assert granule_geometry({"polygons": [["garbage"]]}) is None


# ------------------------------------------------------------ normalisation


def test_normalize_repairs_orients_and_rounds() -> None:
    bowtie = {"type": "Polygon", "coordinates": [[[0, 0], [1, 1], [1, 0], [0, 1], [0, 0]]]}
    fixed = normalize_geometry(bowtie)
    assert fixed is not None and shape(fixed).is_valid

    clockwise = {"type": "Polygon", "coordinates": [[[0, 0], [0, 1], [1, 1], [1, 0], [0, 0]]]}
    result = normalize_geometry(clockwise)
    assert shape(result).exterior.is_ccw

    point = normalize_geometry(
        {"type": "Point", "coordinates": [31.123456789123, 51.1]}, precision=7
    )
    assert point["coordinates"] == [31.1234568, 51.1]


def test_normalize_rejects_garbage() -> None:
    assert normalize_geometry(None) is None
    assert normalize_geometry({"type": "Point", "coordinates": [500, 500]}) is None
    assert normalize_geometry({"type": "Nope"}) is None
    assert normalize_geometry({"type": "Polygon", "coordinates": []}) is None


def test_representative_point_inside_concave_polygon() -> None:
    c_shape = {
        "type": "Polygon",
        "coordinates": [[[0, 0], [3, 0], [3, 1], [1, 1], [1, 2], [3, 2], [3, 3], [0, 3], [0, 0]]],
    }
    lon, lat = representative_lonlat(c_shape)
    assert shape(c_shape).contains(shape({"type": "Point", "coordinates": [lon, lat]}))
