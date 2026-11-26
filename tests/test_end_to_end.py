"""All six real spec files through the full pipeline, CLI included."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fakes import FakeHttpClient
from world import register_all

from gis_collector import cli
from gis_collector.collector import GisCollector


def test_all_specs_are_supported(fake: FakeHttpClient, make_collector, specs) -> None:
    register_all(fake)
    report = make_collector().run(list(specs.values()))

    assert report.ok, [e.model_dump() for e in report.failed]
    assert all(e.strategy for e in report.endpoints)  # every endpoint resolved to a strategy

    per_source: dict[str, int] = {}
    for feature in report.features:
        per_source[feature.source_id] = per_source.get(feature.source_id, 0) + 1
    assert per_source == {
        "kadastrova_karta": 3,
        "dream_damage_chernihiv": 4,
        "earthdata_chernihiv": 40,
        "ecozagroza_chernihiv": 3,
        "osm_chernihiv": 4,
        "overpass": 5,
    }


def test_output_contract(fake: FakeHttpClient, make_collector, specs) -> None:
    register_all(fake)
    rows = [f.model_dump() for f in make_collector().collect(list(specs.values()))]
    assert rows
    for row in rows:
        assert set(row) == {"source_id", "geometry", "properties"}
        assert isinstance(row["source_id"], str) and isinstance(row["properties"], dict)
        assert row["geometry"] is None or {"type", "coordinates"} <= set(row["geometry"]) | {"type"}
    json.dumps(rows, ensure_ascii=False)  # everything is JSON serialisable


def test_meta_can_be_disabled(fake: FakeHttpClient, make_collector, specs) -> None:
    register_all(fake)
    features = make_collector(include_meta=False).collect([specs["ecozagroza_chernihiv"]])
    assert features and all("_meta" not in f.properties for f in features)


def test_plan_resolves_every_endpoint(specs) -> None:
    collector = GisCollector.__new__(GisCollector)  # plan() needs no network, no HTTP client
    from gis_collector.strategies.registry import build_default_registry

    collector._registry = build_default_registry()
    rows = collector.plan(list(specs.values()))
    assert len(rows) == 14 and all(r.strategy and not r.problem for r in rows)
    assert {r.strategy for r in rows} == {
        "mvt_tiles",
        "metadata",
        "templated_lookup",
        "cursor_index_detail",
        "consumed_detail",
        "json_api",
        "overpass",
    }


def test_cli_plan(capsys: pytest.CaptureFixture[str], specs) -> None:
    assert (
        cli.main(
            [
                "plan",
                "--specs-dir",
                str(Path(__file__).parent.parent / "specs"),
                "--source",
                "overpass",
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert out.count("overpass") >= 2 and "api/interpreter" in out


def test_cli_missing_specs_dir_is_fatal(tmp_path: Path) -> None:
    assert cli.main(["plan", "--specs-dir", str(tmp_path / "nope")]) == cli.EXIT_FATAL


def test_cli_writes_all_formats(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake: FakeHttpClient
) -> None:
    register_all(fake)
    from fakes import StaticBoundary
    from world import CHERNIHIV_REGION

    original_init = GisCollector.__init__

    def patched_init(self, settings=None, **kwargs):  # inject fakes into the CLI's collector
        original_init(
            self,
            settings,
            http=fake,
            boundary=StaticBoundary(CHERNIHIV_REGION),
            sleep=lambda _s: None,
        )

    monkeypatch.setattr(GisCollector, "__init__", patched_init)
    specs_dir = str(Path(__file__).parent.parent / "specs")
    base = [
        "collect",
        "--specs-dir",
        specs_dir,
        "--source",
        "ecozagroza_chernihiv",
        "--log-level",
        "ERROR",
    ]

    out_json = tmp_path / "out.json"
    assert cli.main([*base, "-o", str(out_json)]) == 0
    rows = json.loads(out_json.read_text(encoding="utf-8"))
    assert len(rows) == 3 and set(rows[0]) == {"source_id", "geometry", "properties"}

    out_jsonl = tmp_path / "out.jsonl"
    assert cli.main([*base, "-o", str(out_jsonl), "--format", "jsonl"]) == 0
    assert len(out_jsonl.read_text(encoding="utf-8").splitlines()) == 3

    out_geo = tmp_path / "out.geojson"
    assert cli.main([*base, "-o", str(out_geo), "--format", "geojson"]) == 0
    collection = json.loads(out_geo.read_text(encoding="utf-8"))
    assert collection["type"] == "FeatureCollection"
    assert collection["features"][0]["properties"]["source_id"] == "ecozagroza_chernihiv"
