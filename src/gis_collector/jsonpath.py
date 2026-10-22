"""A tiny, dependency-free JSONPath subset.

The specifications only use a handful of constructs, so a full JSONPath engine
is unnecessary. Supported syntax:

* optional leading ``$``
* ``.name`` and ``['name']`` member access
* ``[*]`` wildcard over lists
* ``[n]`` / ``[-n]`` list index

Anything after a space (e.g. ``coordinates[0] (longitude, string)`` in a spec
description) is ignored.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Literal

from gis_collector.exceptions import SpecError

_TOKEN = re.compile(
    r"""
    \.?(?P<name>[A-Za-z_][\w\-]*)      # .name or name
    | \[\*\]                            # [*]
    | \[(?P<index>-?\d+)\]              # [0] / [-1]
    | \[['"](?P<quoted>[^'"]+)['"]\]    # ['name']
    """,
    re.VERBOSE,
)


@dataclass(frozen=True, slots=True)
class _Step:
    kind: Literal["key", "wildcard", "index"]
    key: str = ""
    index: int = 0


@lru_cache(maxsize=1024)
def _compile(path: str) -> tuple[_Step, ...]:
    text = path.strip().split(" ")[0]
    if text.startswith("$"):
        text = text[1:]
    steps: list[_Step] = []
    pos = 0
    while pos < len(text):
        match = _TOKEN.match(text, pos)
        if match is None:
            raise SpecError(f"Unsupported JSONPath syntax at position {pos}: {path!r}")
        if match.group("name") is not None:
            steps.append(_Step("key", key=match.group("name")))
        elif match.group("quoted") is not None:
            steps.append(_Step("key", key=match.group("quoted")))
        elif match.group("index") is not None:
            steps.append(_Step("index", index=int(match.group("index"))))
        else:
            steps.append(_Step("wildcard"))
        pos = match.end()
    return tuple(steps)


def has_wildcard(path: str) -> bool:
    """True when the path can yield several matches."""
    return any(step.kind == "wildcard" for step in _compile(path))


def extract(data: Any, path: str) -> list[Any]:
    """Return every value matched by *path* (empty list when nothing matches)."""
    current: list[Any] = [data]
    for step in _compile(path):
        following: list[Any] = []
        for node in current:
            if step.kind == "key":
                if isinstance(node, dict) and step.key in node:
                    following.append(node[step.key])
            elif step.kind == "index":
                if isinstance(node, list) and -len(node) <= step.index < len(node):
                    following.append(node[step.index])
            elif isinstance(node, list):
                following.extend(node)
        current = following
        if not current:
            break
    return current


def extract_one(data: Any, path: str, default: Any = None) -> Any:
    """Return the first match of *path* or *default*."""
    matches = extract(data, path)
    return matches[0] if matches else default


def extract_attribute(data: Any, path: str) -> tuple[bool, Any]:
    """Extract a spec *attribute*.

    Returns ``(found, value)``. Paths with a wildcard produce a list of matches,
    other paths a scalar (the first match).
    """
    matches = extract(data, path)
    if not matches:
        return False, None
    if has_wildcard(path):
        return True, matches
    return True, matches[0]
