from __future__ import annotations

import pytest

from gis_collector.exceptions import SpecError
from gis_collector.jsonpath import extract, extract_attribute, extract_one, has_wildcard

DATA = {
    "data": [{"external": {"updated": "t1"}}, {"external": {"updated": "t2"}}],
    "cdu_response": {"locations": [{"g": {"ids": ["a", "b"]}}, {"g": {"ids": ["c"]}}]},
}


def test_root_and_members() -> None:
    assert extract(DATA, "$") == [DATA]
    assert extract(DATA, "$.data[-1].external.updated") == ["t2"]
    assert extract_one(DATA, "data[0].external.updated") == "t1"


def test_wildcards_and_missing() -> None:
    assert extract(DATA, "cdu_response.locations[*].g.ids") == [["a", "b"], ["c"]]
    assert extract(DATA, "cdu_response.nothing[*].x") == []
    assert extract_one(DATA, "$.data[5]", default="d") == "d"


def test_description_suffix_is_ignored() -> None:
    assert extract({"coordinates": [1, 2]}, "coordinates[0] (longitude, string)") == [1]


def test_attribute_semantics() -> None:
    assert extract_attribute(DATA, "data[0].external.updated") == (True, "t1")
    assert extract_attribute(DATA, "data[*].external.updated") == (True, ["t1", "t2"])
    assert extract_attribute(DATA, "zzz") == (False, None)
    assert has_wildcard("a[*].b") and not has_wildcard("a.b")


def test_unsupported_syntax() -> None:
    with pytest.raises(SpecError):
        extract(DATA, "$..deep")
