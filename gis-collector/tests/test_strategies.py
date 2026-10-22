"""Strategy-level tests against the fake internet in ``world.py``."""

from __future__ import annotations

import pytest
from fakes import StaticBoundary, json_response, not_found
from shapely.geometry import box, shape
from world import (
    CADNUM,
    DREAM,
    OVERPASS_MAIN,
    TILE_A,
    TILE_B,
    register_cadastre,
    register_dream,
    register_earthdata,
    register_ecozagroza,
    register_overpass,
)

from gis_collector import GisCollector, Settings
from gis_collector.exceptions import RemoteRuntimeError
from gis_collector.geo.tiles import tile_bounds


def by_source(features):
    result: dict[str, list] = {}
    for f in features:
        result.setdefault(f.source_id, []).append(f)
    return result


# ------------------------------------------------------------------ ecozagroza


def test_ecozagroza_both_layers_and_exact_region_filter(fake, make_collector, specs) -> None:
    register_ecozagroza(fake)
    features = make_collector().collect([specs["ecozagroza_chernihiv"]])
    ids = sorted(f.properties["id"] for f in features)
    assert ids == [1, 2, 10]  # sensor 3 (Belarus) is dropped by point-in-polygon
    radiation = next(f for f in features if f.properties["id"] == 1)
    assert radiation.geometry == {"type": "Point", "coordinates": [31.3, 51.5]}
    assert "latitude" not in radiation.properties  # represented by geometry
    assert radiation.properties["layer"] == "radiation"
    assert {c.params["layer"] for c in fake.calls} == {"radiation", "air_pollution"}


# ------------------------------------------------------------------- earthdata


def test_earthdata_paging_polygons_and_variants(fake, make_collector, specs) -> None:
    register_earthdata(fake)
    features = make_collector().collect([specs["earthdata_chernihiv"]])
    assert len(features) == 40  # MOD14: 1 page of 20; VNP14: 4 pages of 5 (expected_count=20)
    calls = fake.calls_to("granules.json")
    assert [c.params.get("page_num") for c in calls if c.params["short_name"] == "VNP14"] == [
        1,
        2,
        3,
        4,
    ]
    first = features[0]
    assert first.geometry["type"] == "Polygon"
    assert first.geometry["coordinates"][0][0] == [31.0, 50.5]  # lat/lon swapped to lon/lat
    assert "polygons" not in first.properties and first.properties["title"].startswith("MOD14")
    assert "Authorization" not in (calls[0].headers or {})


def test_earthdata_bearer_token_is_sent_when_configured(fake, make_collector, specs) -> None:
    register_earthdata(fake)
    make_collector(tokens={"earthdata_chernihiv": "secret"}).collect([specs["earthdata_chernihiv"]])
    assert fake.calls[0].headers["Authorization"] == "Bearer secret"


# -------------------------------------------------------------------- overpass


def test_overpass_geometry_dedup_and_untagged(fake, make_collector, specs) -> None:
    register_overpass(fake)
    features = make_collector().collect([specs["overpass"]])
    main, farmland = features[:4], features[4:]
    assert [f.properties["_meta"]["feature_id"] for f in main] == [
        "node/1",
        "way/2",
        "way/3",
        "relation/4",
    ]  # duplicate node/1 and untagged node/5 dropped
    assert [f.geometry["type"] for f in main] == ["Point", "Polygon", "LineString", "Polygon"]
    assert len(main[3].geometry["coordinates"]) == 2  # outer ring + hole
    assert main[1].properties["tags.landuse"] == "landfill" and main[1].properties["bounds"]
    assert [f.properties["tags"] for f in farmland] == [{"landuse": "farmland"}]
    post = [c for c in fake.calls if c.method == "POST"]
    assert "area(3600071249)" in post[0].data["data"]


def test_overpass_sends_spec_user_agent_and_checks_status(fake, make_collector, specs) -> None:
    register_overpass(fake)
    make_collector().collect([specs["osm_chernihiv"]])
    post = next(c for c in fake.calls if c.method == "POST")
    assert post.headers["User-Agent"].startswith("GIS-Collector-Bot/1.0")
    assert fake.calls[0].url.endswith("/api/status")


def test_overpass_waits_for_slot_and_retries_runtime_error(fake, specs) -> None:
    sleeps: list[float] = []
    answers = iter(
        [
            {"elements": [], "remark": 'runtime error: Query timed out in "query" at line 1'},
            OVERPASS_MAIN,
        ]
    )
    fake.add(
        "GET",
        "https://overpass-api.de/api/status",
        b"Rate limit: 2\nSlot available after: 2026-10-16T10:00:00Z, in 12 seconds.\n",
    )
    fake.add("POST", "https://overpass-api.de/api/interpreter", lambda c: next(answers))
    collector = GisCollector(
        Settings(), http=fake, boundary=StaticBoundary(box(0, 0, 1, 1)), sleep=sleeps.append
    )
    features = collector.collect([specs["osm_chernihiv"]])
    assert len(features) == 4
    assert 13.0 in sleeps  # waited 12s + 1s for a free slot


def test_overpass_gives_up_on_persistent_runtime_error(fake, make_collector, specs) -> None:
    fake.add("GET", "https://overpass-api.de/api/status", b"2 slots available now.")
    fake.add(
        "POST",
        "https://overpass-api.de/api/interpreter",
        {"elements": [], "remark": "runtime error: out of memory"},
    )
    report = make_collector().run([specs["osm_chernihiv"]])
    assert not report.ok and "RemoteRuntimeError" in report.failed[0].detail
    assert issubclass(RemoteRuntimeError, Exception)


# ------------------------------------------------------------------- cadastre


def test_cadastre_tiles_merge_filter_and_skip_templated(fake, make_collector, specs) -> None:
    register_cadastre(fake)
    report = make_collector().run([specs["kadastrova_karta"]])
    assert report.ok
    groups = by_source(report.features)["kadastrova_karta"]
    parcels = [f for f in groups if f.properties["_meta"]["layer"] == "land_polygons"]
    sales = [f for f in groups if f.properties["_meta"]["layer"] == "land_sales_points"]
    assert len(parcels) == 2 and len(sales) == 1

    split = next(f for f in parcels if f.properties["cadnum"] == CADNUM)
    assert split.geometry["type"] == "Polygon"  # two tile fragments dissolved into one
    west_a, south_a, east_a, north_a = tile_bounds(*TILE_A)
    west_b, _, east_b, _ = tile_bounds(*TILE_B)
    minx, miny, maxx, maxy = shape(split.geometry).bounds
    assert minx > west_a and maxx < east_b and maxx > east_a  # spans both tiles
    assert split.properties["area"] == 1.5 and split.properties["_meta"]["tile_zoom"] == 11

    assert sales[0].geometry["type"] == "Point"
    assert (
        sales[0].properties["sale_price"] == "100000" and sales[0].properties["promoted"] is False
    )

    assert any(e.status == "skipped" for e in report.endpoints)  # styles.json
    assert not any(c.url.startswith("https://back.geoserver.click") for c in fake.calls)
    # auctions are requested at z8
    assert any("/auctions/8/" in c.url for c in fake.calls)


def test_cadastre_region_filter_drops_parcels_outside_polygon(fake, specs) -> None:
    register_cadastre(fake)
    west, south, east, north = tile_bounds(*TILE_A)
    _, _, east_b, _ = tile_bounds(*TILE_B)
    southern_half = box(west + 1e-4, south + 1e-4, east_b - 1e-4, (south + north) / 2)
    collector = GisCollector(
        Settings(max_workers=2),
        http=fake,
        boundary=StaticBoundary(southern_half),
        sleep=lambda _s: None,
    )
    parcels = [
        f
        for f in collector.collect([specs["kadastrova_karta"]])
        if f.properties["_meta"].get("layer") == "land_polygons"
    ]
    assert [f.properties["cadnum"] for f in parcels] == [CADNUM]  # the northern parcel is dropped


def test_cadastre_enrichment_via_rest_lookup(fake, make_collector, specs) -> None:
    register_cadastre(fake)
    features = make_collector(template_enrich_limit=1).collect([specs["kadastrova_karta"]])
    lookup = [f for f in features if f.properties["_meta"]["endpoint"].endswith("/{cadnum}")]
    assert len(lookup) == 1
    assert lookup[0].geometry["type"] == "MultiPolygon"
    assert lookup[0].properties["address"] is None and "geometry" not in lookup[0].properties
    called = [c.url for c in fake.calls_to("geoserver.click")]
    assert called and called[0].endswith(f"/landuse/{CADNUM}")  # colons stay unescaped


def test_style_zoom_limits_clamp_configured_zoom(fake, make_collector, specs) -> None:
    register_cadastre(fake)
    fake.routes.insert(
        0,
        (
            "GET",
            "https://kadastrova-karta.com/map/styles.json",
            lambda c: json_response(
                {
                    "sources": {
                        "kadastr": {
                            "minzoom": 13,
                            "maxzoom": 16,
                            "tiles": [
                                "https://kadastrova-karta.com/tiles/maps/kadastr/{z}/{x}/{y}.pbf"
                            ],
                        }
                    }
                }
            ),
        ),
    )
    # restrict to one parcel-sized region to keep the tile grid small at z13
    tiny = box(31.85, 50.70, 31.86, 50.71)
    collector = GisCollector(
        Settings(max_workers=2), http=fake, boundary=StaticBoundary(tiny), sleep=lambda _s: None
    )
    collector.collect([specs["kadastrova_karta"]])
    assert any("/kadastr/13/" in c.url for c in fake.calls)
    assert not any("/kadastr/11/" in c.url for c in fake.calls)


# ------------------------------------------------------------------------ DREAM


def test_dream_objects_and_ideas(fake, make_collector, specs) -> None:
    register_dream(fake)
    features = make_collector().collect([specs["dream_damage_chernihiv"]])
    objects = {
        f.properties["internal.id"]: f
        for f in features
        if "/objects/" in f.properties["_meta"]["endpoint"]
    }
    assert sorted(objects) == ["a1", "a3", "a5"]  # a2 outside UA74, a4 is 404
    assert objects["a1"].geometry == {"type": "Point", "coordinates": [31.29, 51.49]}
    assert objects["a1"].properties["_meta"]["geometry_source"] == "native"
    assert objects["a3"].geometry is None and objects["a3"].properties["_meta"]["katottg"] == [
        "UA74040030010098765"
    ]
    assert "cdu_response.locations[*].geometry.coordinates" not in objects["a1"].properties
    assert objects["a1"].properties["cdu_response.title[*].translation"] == ["Object a1"]

    idea = next(f for f in features if f.properties["internal.id"] == "i1")
    assert idea.geometry == objects["a1"].geometry  # inherited from the related object
    assert idea.properties["_meta"]["geometry_source"] == "related_object"


def test_dream_cursor_pagination_and_stuck_cursor(fake, make_collector, specs) -> None:
    spec = specs["dream_damage_chernihiv"].model_copy(deep=True)
    for endpoint in spec.endpoints:
        if endpoint.pagination_details:
            endpoint.pagination_details.page_size = 3
    spec.endpoints = [e for e in spec.endpoints if "/objects" in e.url]

    def record(i: int, updated: str) -> dict:
        return {"internal": {"id": f"o{i}", "code": f"C{i}"}, "external": {"updated": updated}}

    pages = {
        None: [record(1, "t1"), record(2, "t2"), record(3, "t3")],
        "t3": [record(3, "t3"), record(4, "t4"), record(5, "t5")],
        "t5": [record(5, "t5")],
    }
    fake.add("GET", f"{DREAM}/objects", lambda c: {"data": pages[(c.params or {}).get("from")]})
    fake.add(
        "GET",
        f"{DREAM}/objects/o1",
        {
            "internal": {"id": "o1"},
            "cdu_response": {
                "locations": [{"gazetteer": {"scheme": "UA-CATOTTG", "identifiers": ["UA74000"]}}]
            },
        },
    )
    fake.add("GET", lambda c: "/objects/o" in c.url, not_found)
    features = make_collector().collect([spec])
    assert [f.properties["internal.id"] for f in features] == ["o1"]
    index_calls = [c for c in fake.calls if c.url == f"{DREAM}/objects"]
    assert [(c.params or {}).get("from") for c in index_calls] == [None, "t3", "t5"]
    detail_ids = [c.url.rsplit("/", 1)[-1] for c in fake.calls if c.url != f"{DREAM}/objects"]
    assert sorted(detail_ids) == [
        "o1",
        "o2",
        "o3",
        "o4",
        "o5",
    ]  # o3 fetched once despite the overlap


def test_dream_stops_when_cursor_does_not_advance(fake, make_collector, specs) -> None:
    spec = specs["dream_damage_chernihiv"].model_copy(deep=True)
    for endpoint in spec.endpoints:
        if endpoint.pagination_details:
            endpoint.pagination_details.page_size = 2
    spec.endpoints = [e for e in spec.endpoints if "/objects" in e.url]
    stuck = {
        "data": [{"internal": {"id": f"s{i}"}, "external": {"updated": "same"}} for i in (1, 2)]
    }
    fake.add("GET", f"{DREAM}/objects", stuck)
    fake.add("GET", lambda c: "/objects/s" in c.url, not_found)
    make_collector().collect([spec])
    index_calls = [c for c in fake.calls if c.url == f"{DREAM}/objects"]
    assert len(index_calls) == 2  # second page repeats the records → cursor judged stuck


# --------------------------------------------------------------- error handling


def test_failed_endpoint_does_not_abort_others(fake, make_collector, specs) -> None:
    register_ecozagroza(fake)  # earthdata route is missing → AssertionError inside the fake
    fake.add("GET", "https://cmr.earthdata.nasa.gov/search/granules.json", not_found)
    report = make_collector().run([specs["earthdata_chernihiv"], specs["ecozagroza_chernihiv"]])
    assert [e.status for e in report.endpoints] == ["failed", "ok"]
    assert len(report.features) == 3


def test_strict_mode_raises(fake, make_collector, specs) -> None:
    fake.add("GET", "https://cmr.earthdata.nasa.gov/search/granules.json", not_found)
    with pytest.raises(Exception, match="404"):
        make_collector(strict=True).collect([specs["earthdata_chernihiv"]])


def test_source_selection_and_inaccessible(fake, make_collector, specs) -> None:
    register_ecozagroza(fake)
    spec = specs["ecozagroza_chernihiv"]
    collector = make_collector()
    assert collector.collect([spec], include={"nothing"}) == []
    assert collector.collect([spec], exclude={"ecozagroza_chernihiv"}) == []
    down = spec.model_copy(update={"accessible": False})
    report = collector.run([down])
    assert report.endpoints[0].status == "skipped" and not report.features
    assert not fake.calls
