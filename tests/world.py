"""A small fake "internet" that mimics the six sources described in ``specs/``."""

from __future__ import annotations

import re
from typing import Any

from fakes import Call, FakeHttpClient, not_found
from mvt_builder import POINT, POLYGON, encode_tile, point_geometry, polygon_geometry
from shapely.geometry import box

from gis_collector.geo.tiles import tile_point_to_lonlat

CHERNIHIV_REGION = box(30.49, 50.46, 33.50, 52.38)
CADNUM = "7423883000:02:001:3044"
TILE_A = (11, 1205, 688)
TILE_B = (11, 1206, 688)


# ------------------------------------------------------------------ ecozagroza


def register_ecozagroza(fake: FakeHttpClient) -> None:
    def sensors(call: Call) -> list[dict[str, Any]]:
        layer = (call.params or {}).get("layer")
        if layer == "radiation":
            return [
                {
                    "id": 1,
                    "latitude": 51.5,
                    "longitude": 31.3,
                    "layer": "radiation",
                    "markerIcon": "green",
                    "indexValue": 0.12,
                },
                {
                    "id": 2,
                    "latitude": 51.0,
                    "longitude": 32.0,
                    "layer": "radiation",
                    "markerIcon": "green",
                    "indexValue": 0.10,
                },
                {
                    "id": 3,
                    "latitude": 52.9,
                    "longitude": 30.9,
                    "layer": "radiation",  # Belarus
                    "markerIcon": "green",
                    "indexValue": 0.11,
                },
            ]
        return [
            {
                "id": 10,
                "latitude": 51.2,
                "longitude": 31.9,
                "layer": "air_pollution",
                "markerIcon": "yellow",
                "indexValue": 42,
            }
        ]

    fake.add("GET", "https://sensor.ecozagroza.gov.ua/api/sensors", sensors)


# ------------------------------------------------------------------- earthdata


def register_earthdata(fake: FakeHttpClient) -> None:
    def granules(call: Call) -> dict[str, Any]:
        params = call.params or {}
        page, size = int(params.get("page_num", 1)), int(params["page_size"])
        entries = [
            {
                "id": f"G{params['short_name']}-{page}-{i}",
                "title": f"{params['short_name']}.A2026.{page}.{i}",
                "time_start": "2026-10-15T10:00:00.000Z",
                "time_end": "2026-10-15T10:05:00.000Z",
                "polygons": [["50.5 31.0 50.7 31.0 50.7 31.5 50.5 31.5 50.5 31.0"]],
                "links": [{"href": "https://example.org/file.hdf"}],
            }
            for i in range(size)
        ]
        return {"feed": {"entry": entries}}

    fake.add("GET", "https://cmr.earthdata.nasa.gov/search/granules.json", granules)


# -------------------------------------------------------------------- overpass


def _square(lon: float, lat: float, size: float) -> list[dict[str, float]]:
    return [
        {"lat": lat, "lon": lon},
        {"lat": lat, "lon": lon + size},
        {"lat": lat + size, "lon": lon + size},
        {"lat": lat + size, "lon": lon},
        {"lat": lat, "lon": lon},
    ]


OVERPASS_MAIN = {
    "elements": [
        {"type": "node", "id": 1, "lat": 51.5, "lon": 31.3, "tags": {"hazard": "radiation"}},
        {
            "type": "way",
            "id": 2,
            "tags": {"landuse": "landfill"},
            "geometry": _square(31.0, 51.0, 0.01),
            "bounds": {"minlat": 51.0, "minlon": 31.0, "maxlat": 51.01, "maxlon": 31.01},
        },
        {
            "type": "way",
            "id": 3,
            "tags": {"hazard": "fence"},
            "geometry": _square(31.1, 51.1, 0.01),
        },
        {
            "type": "relation",
            "id": 4,
            "tags": {"type": "multipolygon", "landuse": "military"},
            "members": [
                {"type": "way", "ref": 40, "role": "outer", "geometry": _square(32.0, 51.0, 0.1)},
                {
                    "type": "way",
                    "ref": 41,
                    "role": "inner",
                    "geometry": _square(32.04, 51.04, 0.02),
                },
            ],
        },
        {"type": "node", "id": 5, "lat": 51.0, "lon": 31.0},  # skeleton node (no tags)
        {"type": "node", "id": 1, "lat": 51.5, "lon": 31.3, "tags": {"hazard": "radiation"}},
    ]
}

OVERPASS_FARMLAND = {
    "elements": [
        {
            "type": "way",
            "id": 100,
            "tags": {"landuse": "farmland"},
            "geometry": _square(31.5, 51.5, 0.05),
        }
    ]
}


def register_overpass(fake: FakeHttpClient) -> None:
    fake.add(
        "GET",
        "https://overpass-api.de/api/status",
        b"Connected as: 1.2.3.4\nRate limit: 2\n2 slots available now.\n",
    )

    def interpreter(call: Call) -> dict[str, Any]:
        query = str((call.data or {}).get("data", ""))
        if "farmland" in query and "landfill" not in query:
            return OVERPASS_FARMLAND
        return OVERPASS_MAIN

    fake.add("POST", "https://overpass-api.de/api/interpreter", interpreter)


# -------------------------------------------------------------------- cadastre


def _tile_bytes(layer: str, features: list[dict[str, Any]]) -> bytes:
    return encode_tile([{"name": layer, "extent": 4096, "features": features}])


def _parcel(cadnum: str, ring: list[tuple[int, int]], **extra: Any) -> dict[str, Any]:
    props = {
        "cadnum": cadnum,
        "ownership": "private",
        "purpose": "agri",
        "purpose_code": "01.01",
        "area": 1.5,
        "category": "agricultural",
        **extra,
    }
    return {"type": POLYGON, "geometry": polygon_geometry([ring]), "props": props}


def cadastre_tiles() -> dict[tuple[int, int, int], bytes]:
    return {
        TILE_A: _tile_bytes(
            "land_polygons",
            [
                _parcel(CADNUM, [(3000, 3000), (4096, 3000), (4096, 3800), (3000, 3800)]),
                _parcel("7423883000:02:001:9999", [(500, 500), (900, 500), (900, 900), (500, 900)]),
            ],
        ),
        TILE_B: _tile_bytes(
            "land_polygons",
            [
                _parcel(CADNUM, [(0, 3000), (1000, 3000), (1000, 3800), (0, 3800)]),
            ],
        ),
    }


def register_cadastre(fake: FakeHttpClient) -> None:
    kadastr = cadastre_tiles()
    sales = _tile_bytes(
        "land_sales_points",
        [
            {
                "type": POINT,
                "geometry": point_geometry([(2000, 3500)]),
                "props": {
                    "title": "Land plot",
                    "description": "d",
                    "public_url": "https://olx.example/1",
                    "sale_price": "100000",
                    "currency": "UAH",
                    "published_on": "2026-10-01",
                    "price_label": "100 000 UAH",
                    "promoted": False,
                },
            }
        ],
    )
    pattern = r"https://kadastrova-karta\.com/tiles/maps/(\w+)/(\d+)/(\d+)/(\d+)\.pbf"

    def tile(call: Call) -> bytes:
        match = re.fullmatch(pattern, call.url)
        assert match, call.url
        layer, z, x, y = match.group(1), *(int(g) for g in match.groups()[1:])
        if layer == "kadastr" and (z, x, y) in kadastr:
            return kadastr[(z, x, y)]
        if layer == "sales" and (z, x, y) == TILE_A:
            return sales
        return not_found(call)  # type: ignore[return-value]

    fake.add("GET", re.compile(pattern), tile)
    fake.add(
        "GET",
        "https://kadastrova-karta.com/map/styles.json",
        {
            "version": 8,
            "sources": {
                "kadastr": {
                    "type": "vector",
                    "minzoom": 5,
                    "maxzoom": 16,
                    "tiles": ["https://kadastrova-karta.com/tiles/maps/kadastr/{z}/{x}/{y}.pbf"],
                }
            },
            "layers": [],
        },
    )
    lon, lat = tile_point_to_lonlat(*TILE_A, 3500, 3400, 4096)
    fake.add(
        "GET",
        re.compile(r"https://back\.geoserver\.click/api/landuse/.+"),
        {
            "cadnum": CADNUM,
            "category": "agricultural",
            "purpose_code": "01.01",
            "purpose": "agri",
            "use": "farming",
            "area": 1.5,
            "unit_area": "ha",
            "ownershipcode": "100",
            "ownership": "private",
            "address": None,
            "geometry": {
                "type": "MultiPolygon",
                "coordinates": [
                    [[[lon, lat], [lon + 0.001, lat], [lon + 0.001, lat + 0.001], [lon, lat]]]
                ],
            },
        },
    )


# ----------------------------------------------------------------------- DREAM

DREAM = "https://public-api.dream.gov.ua/marketplace/public/dream"


def _location(
    code: str | dict[str, str], coords: list[str] | None, scheme: str | None = "UA-CATOTTG"
):
    gazetteer: dict[str, Any] = {"identifiers": [code]}
    if scheme:
        gazetteer["scheme"] = scheme
    location: dict[str, Any] = {"gazetteer": gazetteer, "description": "вул. Тестова"}
    if coords:
        location["geometry"] = {"coordinates": coords}
    return location


def dream_objects() -> dict[str, dict[str, Any] | None]:
    def obj(identifier: str, location: dict[str, Any]) -> dict[str, Any]:
        return {
            "internal": {"id": identifier, "code": f"OBJ-{identifier}", "status": "active"},
            "cdu_response": {
                "date": "2026-01-01",
                "status": "active",
                "title": [{"translation": f"Object {identifier}"}],
                "locations": [location],
                "relatedProcesses": [],
            },
        }

    return {
        "a1": obj("a1", _location("UA74020010010012345", ["31.29", "51.49"])),
        "a2": obj("a2", _location("UA32040010010011111", None)),  # Kyiv oblast → filtered
        "a3": obj("a3", _location("UA74040030010098765", None)),  # no coordinates
        "a4": None,  # 404
        "a5": obj(
            "a5", _location({"scheme": "UA-CATOTTG", "id": "UA74060010010011111"}, None, None)
        ),
    }


def dream_ideas() -> dict[str, dict[str, Any]]:
    return {
        "i1": {
            "internal": {"id": "i1", "code": "IDEA-1", "status": "active"},
            "cdu_response": {
                "code": "IDEA-1",
                "type": "project",
                "status": "active",
                "title": [{"translation": "Відновлення школи"}],
                "approaches": [
                    {"items": [{"locations": [_location("UA74020010010012345", None)]}]}
                ],
                "relatedProcesses": [{"identifier": "a1", "relationship": "object"}],
            },
        }
    }


def _index(records: dict[str, Any]) -> dict[str, Any]:
    return {
        "data": [
            {
                "internal": {"id": key, "code": f"C-{key}"},
                "external": {"updated": f"2026-10-0{n}T00:00:00Z"},
            }
            for n, key in enumerate(records, start=1)
        ]
    }


def register_dream(fake: FakeHttpClient) -> None:
    objects, ideas = dream_objects(), dream_ideas()
    fake.add("GET", f"{DREAM}/objects", _index(objects))
    fake.add("GET", f"{DREAM}/ideas", _index(ideas))

    def detail(store: dict[str, Any], prefix: str):
        def handler(call: Call):
            payload = store.get(call.url.removeprefix(prefix))
            return payload if payload is not None else not_found(call)

        return handler

    fake.add(
        "GET", re.compile(rf"{re.escape(DREAM)}/objects/.+"), detail(objects, f"{DREAM}/objects/")
    )
    fake.add("GET", re.compile(rf"{re.escape(DREAM)}/ideas/.+"), detail(ideas, f"{DREAM}/ideas/"))


def register_all(fake: FakeHttpClient) -> FakeHttpClient:
    register_ecozagroza(fake)
    register_earthdata(fake)
    register_overpass(fake)
    register_cadastre(fake)
    register_dream(fake)
    return fake
