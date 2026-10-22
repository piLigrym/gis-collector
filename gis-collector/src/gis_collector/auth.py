"""Authentication: turns a spec's ``auth`` value into request headers."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from typing import Protocol

from gis_collector.models import SourceSpec
from gis_collector.settings import Settings

logger = logging.getLogger(__name__)


class AuthProvider(Protocol):
    def headers_for(self, source: SourceSpec) -> Mapping[str, str]: ...


class SettingsAuthProvider:
    """Supports ``auth: none`` and ``auth: bearer``.

    Bearer tokens are looked up in ``Settings.tokens[source_id]`` and then in the
    environment (``GIS_COLLECTOR_TOKEN_<SOURCE_ID>`` or the variable named in
    ``Settings.token_env_vars``). A missing token is *not* an error: e.g. NASA CMR
    metadata search is public, a token is needed only to download granule files.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._warned: set[str] = set()

    def headers_for(self, source: SourceSpec) -> Mapping[str, str]:
        scheme = source.auth.lower()
        if scheme in ("none", ""):
            return {}
        if scheme == "bearer":
            token = self._find_token(source.source_id)
            if token:
                return {"Authorization": f"Bearer {token}"}
            self._warn_once(
                source.source_id, "no bearer token configured; continuing unauthenticated"
            )
            return {}
        self._warn_once(source.source_id, f"unsupported auth scheme {source.auth!r}; ignored")
        return {}

    def _find_token(self, source_id: str) -> str | None:
        token = self._settings.tokens.get(source_id)
        if token:
            return token
        names = [f"GIS_COLLECTOR_TOKEN_{source_id.upper()}"]
        custom = self._settings.token_env_vars.get(source_id)
        if custom:
            names.append(custom)
        for name in names:
            value = os.environ.get(name)
            if value:
                return value
        return None

    def _warn_once(self, source_id: str, message: str) -> None:
        if source_id not in self._warned:
            self._warned.add(source_id)
            logger.warning("[%s] %s", source_id, message)
