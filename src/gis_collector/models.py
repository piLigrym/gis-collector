"""Pydantic models: source specifications (input) and the collected Feature (output).

The models are intentionally tolerant (``extra="ignore"``): the JSON files in
``specs/`` come from different authors and are not perfectly uniform (see the
``attributes`` field, which is a list of strings in some files and a list of
objects in others).
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_PLACEHOLDER = re.compile(r"\{(\w+)\}")


# --------------------------------------------------------------------------- output


class Feature(BaseModel):
    """One collected object. ``model_dump()`` yields exactly the public contract::

        {"source_id": "...", "geometry": {...}, "properties": {...}}

    ``geometry`` is a GeoJSON geometry in WGS84 (lon, lat) or ``None`` when the
    source has no coordinates for the object (e.g. DREAM records).
    """

    model_config = ConfigDict(extra="forbid")

    source_id: str
    geometry: dict[str, Any] | None
    properties: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------- spec


class _SpecModel(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class AttributeSpec(_SpecModel):
    """An attribute to extract. ``name`` may be a dotted JSONPath-like path."""

    name: str
    type: str | None = None
    layer: str | None = None


class LayerSpec(_SpecModel):
    types: list[str] = Field(default_factory=list)
    extent: int = 4096


class GeometrySpec(_SpecModel):
    """How to find geometry in a response. Different sources use different keys."""

    type: str | None = None
    location: str | None = None
    path: str | None = None
    format: str | None = None
    types: list[str] = Field(default_factory=list)
    layers: dict[str, LayerSpec] = Field(default_factory=dict)
    x: str | None = None
    y: str | None = None
    crs: str | None = None
    crs_verified_by: str | None = None
    optional: bool = False
    fallback: str | None = None


class PaginationDetails(_SpecModel):
    cursor_param: str | None = None
    cursor_value: str | None = None
    page_param: str | None = None
    page_size: int | None = None
    page_size_configurable: bool | None = None
    stop_condition: str | None = None


class ClientFilterSpec(_SpecModel):
    """Client-side filter: keep records whose codes start with *value*."""

    type: str = "client_side"
    field: str
    scheme: str | None = None
    match: Literal["prefix", "exact"] = "prefix"
    value: str


class AreaConstraint(_SpecModel):
    type: str = "region"
    name: str


class EndpointSpec(_SpecModel):
    url: str
    method: Literal["GET", "POST"] = "GET"
    access_class: str = "api_rest"
    role: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    params: dict[str, Any] = Field(default_factory=dict)
    expected_format: str | None = None
    expected_count: int | None = None
    pagination: str = "none"
    pagination_details: PaginationDetails | None = None
    geometry: GeometrySpec | None = None
    container: str | None = None
    attributes: list[AttributeSpec] = Field(default_factory=list)
    filter: ClientFilterSpec | None = None
    second_sample_params: dict[str, Any] | None = None

    @field_validator("method", mode="before")
    @classmethod
    def _upper_method(cls, value: Any) -> Any:
        return value.upper() if isinstance(value, str) else value

    @field_validator("attributes", mode="before")
    @classmethod
    def _normalise_attributes(cls, value: Any) -> Any:
        """Accept both ``["id", ...]`` and ``[{"name": "id", "type": ...}, ...]``."""
        if value is None:
            return []
        return [{"name": item} if isinstance(item, str) else item for item in value]

    @model_validator(mode="after")
    def _check_url(self) -> EndpointSpec:
        if not self.url.startswith(("http://", "https://")):
            raise ValueError(f"endpoint url must be absolute http(s): {self.url!r}")
        return self

    @property
    def placeholders(self) -> tuple[str, ...]:
        """Names of ``{placeholders}`` in the URL, in order of appearance."""
        return tuple(dict.fromkeys(_PLACEHOLDER.findall(self.url)))

    @property
    def is_tile_template(self) -> bool:
        return {"z", "x", "y"} <= set(self.placeholders)

    def attributes_for_layer(self, layer: str | None) -> list[AttributeSpec]:
        """Attributes declared for *layer* (or without a layer)."""
        return [a for a in self.attributes if a.layer in (None, layer)]

    @property
    def label(self) -> str:
        """Short human readable identifier used in logs and reports."""
        suffix = f" [{self.role}]" if self.role else ""
        return f"{self.method} {self.url}{suffix}"


class SourceSpec(_SpecModel):
    source_id: str
    accessible: bool = True
    auth: str = "none"
    confidence: float | None = None
    notes: str = ""
    area_constraint: AreaConstraint | None = None
    endpoints: list[EndpointSpec] = Field(default_factory=list)
