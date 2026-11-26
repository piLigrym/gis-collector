"""Attribute projection: spec attributes → ``Feature.properties``."""

from __future__ import annotations

import logging
from collections.abc import Collection, Mapping
from typing import Any

from gis_collector.jsonpath import extract_attribute
from gis_collector.models import AttributeSpec

logger = logging.getLogger(__name__)


def coerce(value: Any, type_name: str | None) -> Any:
    """Best-effort conversion to the type declared in the spec (never raises)."""
    if value is None or type_name is None:
        return value
    try:
        if type_name == "number":
            if isinstance(value, bool):
                return value
            if isinstance(value, int | float):
                return value
            if isinstance(value, str) and value.strip():
                number = float(value.replace(",", "."))
                return int(number) if number.is_integer() and "." not in value else number
        elif type_name == "string":
            return value if isinstance(value, str) else str(value)
        elif type_name == "boolean":
            if isinstance(value, str):
                return value.strip().lower() in {"true", "1", "yes"}
            return bool(value)
    except (ValueError, TypeError):
        logger.debug("Cannot coerce %r to %s", value, type_name)
    return value


class AttributeProjector:
    """Extracts declared attributes from a raw item.

    * Attributes missing in the item are omitted (all of them are optional in practice).
    * Names listed in *exclude* (those that already went into ``geometry``) are skipped.
    * When *include_undeclared* is true, remaining top-level keys of a mapping item
      are copied as well (used for vector-tile properties, which are flat).
    """

    def __init__(
        self,
        attributes: list[AttributeSpec],
        *,
        exclude: Collection[str] = (),
        include_undeclared: bool = False,
    ) -> None:
        self._attributes = [a for a in attributes if a.name not in set(exclude)]
        self._exclude = set(exclude)
        self._include_undeclared = include_undeclared

    def project(self, item: Mapping[str, Any]) -> dict[str, Any]:
        properties: dict[str, Any] = {}
        if self._include_undeclared:
            properties.update({k: v for k, v in item.items() if k not in self._exclude})
        for attribute in self._attributes:
            found, value = extract_attribute(item, attribute.name)
            if found:
                properties[attribute.name] = coerce(value, attribute.type)
        return properties
