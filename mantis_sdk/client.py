"""the MantisClient: entry point to the rest surface and browser automation.

resource-oriented: client.spaces / client.maps / client.notebooks / client.search /
client.annotations. legacy flat methods (create_space, get_spaces, open_space, ...) are
kept as thin shims that emit DeprecationWarning."""
from __future__ import annotations

import logging
import warnings
from collections.abc import Callable
from typing import Any

import pandas as pd

from ._http import HttpClient
from .agents import AgentsResource
from .config import ConfigurationManager

# re-exported for back-compat: legacy code imports these enums from mantis_sdk.client.
from .enums import AIProvider, DataType, Provider, ReducerModels, SpacePrivacy
from .exceptions import ConfigurationError
from .notebook import NotebooksResource
from .resources import (
    AnnotationsResource,
    MapsResource,
    SearchResource,
    SpaceHandle,
    SpacesResource,
)

logger = logging.getLogger("mantis_sdk")

# enums are re-exported here so legacy `from mantis_sdk.client import DataType` keeps working.
__all__ = [
    "MantisClient",
    "ConfigurationManager",
    "SpaceHandle",
    "DataType",
    "SpacePrivacy",
    "ReducerModels",
    "AIProvider",
    "Provider",
]


class MantisClient:
    """sdk client for the mantis frontend + backend."""

    def __init__(
        self,
        base_url: str,
        cookie: str | None = None,
        config: ConfigurationManager | None = None,
    ):
        self.config = config or ConfigurationManager()
        self.cookie = cookie

        if cookie is None and not self.config.internal_user_id:
            raise ConfigurationError(
                "no auth provided: pass a session cookie, or set internal_user_id on the config "
                "(MANTIS_INTERNAL_USER_ID) for backend-to-backend auth."
            )

        self.http = HttpClient(base_url=base_url, cookie=cookie, config=self.config)

        # resource groups.
        self.spaces = SpacesResource(self)
        self.maps = MapsResource(self)
        self.annotations = AnnotationsResource(self)
        self.search = SearchResource(self)
        self.notebooks = NotebooksResource(self)
        self.agents = AgentsResource(self)

    # --- constructors ---
    @classmethod
    def from_env(cls, base_url: str | None = None) -> MantisClient:
        """build a client from MANTIS_* environment variables.
        uses MANTIS_COOKIE for auth (or MANTIS_INTERNAL_USER_ID via the config)."""
        import os

        config = ConfigurationManager()
        cookie = os.getenv("MANTIS_COOKIE")
        base = base_url or os.getenv("MANTIS_BASE_URL", "/api/proxy/")
        return cls(base, cookie=cookie, config=config)

    # --- diagnostics ---
    def check_compatibility(self) -> dict:
        """smoke-check that the configured backend is reachable and speaks the expected shape.
        returns {sdk_version, reachable, getSpaces_shape_ok}. raises nothing on shape mismatch —
        callers inspect the result."""
        from . import __version__

        result: dict[str, Any] = {"sdk_version": __version__, "reachable": False, "getSpaces_shape_ok": False}
        try:
            spaces = self.spaces.get_all()
            result["reachable"] = True
            # the current backend returns these keys; missing keys signal drift.
            expected = {"public", "private", "shared"}
            result["getSpaces_shape_ok"] = isinstance(spaces, dict) and expected.issubset(spaces.keys())
            if not result["getSpaces_shape_ok"]:
                logger.warning("getSpaces shape looks unexpected; SDK may be out of sync with the backend")
        except Exception as exc:  # noqa: BLE001 — diagnostic, never fatal
            result["error"] = str(exc)
        return result

    # --- lifecycle ---
    def close(self) -> None:
        self.http.close()

    def __enter__(self) -> MantisClient:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    async def __aenter__(self) -> MantisClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        self.close()

    # ------------------------------------------------------------------
    # legacy flat api — thin shims over the resource groups.
    # ------------------------------------------------------------------
    def get_spaces(self) -> dict:
        return self.spaces.get_all()

    def get_space_ids_by_name(self, space_name: str, privacy_levels) -> list[str]:
        return self.spaces.ids_by_name(space_name, privacy_levels)

    def create_space(
        self,
        space_name: str,
        data: pd.DataFrame | str,
        data_types: dict,
        custom_models: list | None = None,
        reducer: ReducerModels | str = ReducerModels.UMAP,
        privacy_level: SpacePrivacy | str = SpacePrivacy.PRIVATE,
        ai_provider: AIProvider | str = AIProvider.OpenAI,
        choose_variation: Callable | None = None,
        on_recieve_id: Callable | None = None,
    ) -> SpaceHandle:
        """legacy signature preserved. choose_variation is ignored (the umap-variation
        selection step was removed from the backend pipeline)."""
        if choose_variation is not None:
            warnings.warn(
                "choose_variation is ignored: the backend no longer has a umap-variation "
                "selection step.",
                DeprecationWarning,
                stacklevel=2,
            )
        return self.spaces.create(
            space_name,
            data,
            data_types,
            custom_models=custom_models,
            reducer=reducer,
            privacy_level=privacy_level,
            ai_provider=ai_provider,
            on_receive_id=on_recieve_id,
        )

    def getClusterQuestions(self, space_id: str, **kwargs) -> Any:
        return self.search.cluster_questions(space_id, **kwargs)

    async def get_annotations(self, space_id: str) -> dict:
        """legacy async signature. now backed by rest; returns {type-ish: ...} compatibility
        is dropped in favor of a clean list — callers should migrate to client.annotations.list()."""
        warnings.warn(
            "get_annotations now returns a list via rest; the old websocket dict shape is gone. "
            "use client.annotations.list(map_id).",
            DeprecationWarning,
            stacklevel=2,
        )
        return {"annotations": self.annotations.list(space_id)}

    async def open_space(self, space_id: str, colab: bool = False):
        return await self.spaces.aopen(space_id, colab=colab)
