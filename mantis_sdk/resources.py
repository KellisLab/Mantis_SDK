"""resource groups for the rest surface and the rich SpaceHandle returned by create_space.

these wrap HttpClient with a domain-oriented api (client.spaces, client.maps, ...).
the heavy synthesis create+poll logic lives in SpacesResource._poll_until_done."""
from __future__ import annotations

import io
import json
import logging
import time
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import pandas as pd

from ._http import HttpClient
from .enums import AIProvider, DataType, ReducerModels, SpacePrivacy
from .exceptions import FeatureUnavailableError, MantisError, SpaceCreationError

logger = logging.getLogger("mantis_sdk")

# callback invoked on each progress poll: (percent, message, progress_tree).
ProgressCallback = Callable[[int, str | None, Any], None]
# callback to select among legacy umap variations (kept for signature compatibility).
VariationCallback = Callable[[dict], str]


class SpaceHandle(dict):
    """rich handle for a created/opened space.

    subclasses dict so legacy code that does create_space(...)["space_id"] still works,
    while new code can call methods like .open(), .list_maps(), .get_annotations()."""

    def __init__(self, space_id: str, map_id: str | None, client: MantisClientProtocol):
        super().__init__(space_id=space_id, map_id=map_id)
        self.space_id = space_id
        self.map_id = map_id
        self._client = client

    def __repr__(self) -> str:
        return f"SpaceHandle(space_id={self.space_id!r}, map_id={self.map_id!r})"

    # --- maps ---
    def list_maps(self) -> list[dict]:
        """list maps belonging to this space via /api/listMaps/."""
        resp = self._client.http.request("GET", "/api/listMaps", params={"space_id": self.space_id})
        if isinstance(resp, dict):
            return resp.get("maps", resp.get("data", []))
        return resp or []

    # --- annotations ---
    def get_annotations(self) -> list[dict]:
        return self._client.annotations.list(self.map_id or self.space_id)

    # --- points (lazy, paginated) ---
    def iter_points(self, page_size: int = 200) -> Iterator[dict]:
        """yield idea ids for this space's primary map, paging transparently."""
        map_id = self.map_id or self.space_id
        offset = 0
        while True:
            resp = self._client.http.request(
                "GET", "/api/listIdeas",
                params={"map_id": map_id, "limit": page_size, "offset": offset},
            )
            batch = resp.get("ideas", resp) if isinstance(resp, dict) else resp
            if not batch:
                break
            yield from batch
            if len(batch) < page_size:
                break
            offset += page_size

    @property
    def points(self) -> Iterator[dict]:
        return self.iter_points()

    # --- browser ---
    async def aopen(self, colab: bool = False):
        return await self._client.spaces.aopen(self.space_id, colab=colab)

    def delete(self) -> Any:
        return self._client.http.request("DELETE", f"/api/spaces/delete/{self.space_id}")


class MantisClientProtocol:
    """structural type hint for the client passed to resources (avoids a circular import)."""

    http: HttpClient
    spaces: SpacesResource
    annotations: AnnotationsResource
    space_states: SpaceStatesResource


class _BaseResource:
    def __init__(self, client: MantisClientProtocol):
        self._client = client
        self.http = client.http


class SpacesResource(_BaseResource):
    """create, open, and manage spaces."""

    POLL_INTERVAL = 1.0

    def get_all(self) -> dict:
        """raw /api/getSpaces payload: {public, featured, private, shared, projects}."""
        return self.http.request("GET", "/api/getSpaces")

    def ids_by_name(self, space_name: str, privacy_levels: list[SpacePrivacy | str]) -> list[str]:
        spaces = self.get_all()
        ids: list[str] = []
        for level in privacy_levels:
            key = str(level)
            for space in spaces.get(key, []):
                if space.get("space_name") == space_name:
                    # current getSpaces returns "id"; tolerate the older "space_id" too.
                    sid = space.get("id") or space.get("space_id")
                    if sid:
                        ids.append(sid)
        return ids

    def create(
        self,
        space_name: str,
        data: pd.DataFrame | str,
        data_types: dict[str, DataType | str],
        *,
        custom_models: list[str | None] | None = None,
        reducer: ReducerModels | str = ReducerModels.UMAP,
        privacy_level: SpacePrivacy | str = SpacePrivacy.PRIVATE,
        ai_provider: AIProvider | str = AIProvider.OpenAI,
        chat_model: str = "gpt-4o-mini",
        embedding_model: str = "text-embedding-3-small",
        on_progress: ProgressCallback | None = None,
        on_receive_id: Callable[[str, str], None] | None = None,
        show_progress: bool = False,
        wait: bool = True,
        stall_timeout: float | None = 600.0,
    ) -> SpaceHandle:
        """create a space from a DataFrame or csv path, then (by default) poll to completion.

        returns a SpaceHandle carrying space_id and map_id. set wait=False to return as soon
        as the pipeline is enqueued. stall_timeout raises if progress stops advancing."""
        buffer, columns, file_extension = self._load_data(data)

        data_types_sanitized = self._sanitize_data_types(columns, data_types)

        # custom_models must align with the number of columns we describe, not just the
        # subset the caller passed in data_types. default to all-None.
        if custom_models is None:
            custom_models = [None] * len(data_types_sanitized)
        if len(custom_models) != len(data_types_sanitized):
            raise MantisError(
                f"custom_models length ({len(custom_models)}) must match the number of "
                f"columns ({len(data_types_sanitized)})"
            )

        space_id = str(uuid.uuid4())
        file_key = f"{space_name}-{space_id}.{file_extension}"

        form_data = {
            "space_id": space_id,
            "space_name": space_name,
            "is_public": str(str(privacy_level) == str(SpacePrivacy.PUBLIC)).lower(),
            "red_model": str(reducer),
            "custom_models": json.dumps(custom_models),
            "data_types": json.dumps(data_types_sanitized),
            "ai_provider": str(ai_provider),
            "file_key": file_key,
            "chat_model": chat_model,
            "embedding_model": embedding_model,
        }
        files = {"file": (f"data.{file_extension}", buffer, f"text/{file_extension}")}

        resp = self.http.request("POST", "/synthesis/landscape", data=form_data, files=files)

        # current backend returns {map_id, space_id, status}; there is no layer_id anymore.
        map_id = resp.get("map_id")
        space_id = resp.get("space_id", space_id)
        if not map_id:
            raise SpaceCreationError(f"create response missing map_id: {resp}")

        if on_receive_id is not None:
            on_receive_id(space_id, map_id)

        if wait:
            self._poll_until_done(
                map_id, on_progress=on_progress, show_progress=show_progress, stall_timeout=stall_timeout
            )

        return SpaceHandle(space_id, map_id, self._client)

    def from_github(
        self,
        repo_url: str,
        space_name: str | None = None,
        *,
        privacy_level: SpacePrivacy | str = SpacePrivacy.PRIVATE,
        on_progress: ProgressCallback | None = None,
        show_progress: bool = False,
        wait: bool = True,
        **extra: Any,
    ) -> SpaceHandle:
        """create a space by analyzing a github repository (synthesis/github/)."""
        space_id = str(uuid.uuid4())
        payload = {
            "space_id": space_id,
            "space_name": space_name or repo_url.rstrip("/").split("/")[-1],
            "repo_url": repo_url,
            "is_public": str(str(privacy_level) == str(SpacePrivacy.PUBLIC)).lower(),
            **extra,
        }
        resp = self.http.request("POST", "/synthesis/github", json=payload)
        map_id = resp.get("map_id")
        space_id = resp.get("space_id", space_id)
        if not map_id:
            raise SpaceCreationError(f"github create response missing map_id: {resp}")
        if wait:
            self._poll_until_done(map_id, on_progress=on_progress, show_progress=show_progress)
        return SpaceHandle(space_id, map_id, self._client)

    # the remaining backend creation pipelines are scaffolded; wire them as needed.
    def from_molecules(self, *args: Any, **kwargs: Any) -> SpaceHandle:
        raise FeatureUnavailableError("from_molecules wraps synthesis/molecules/ — not yet wired in the sdk")

    def from_h5ad(self, *args: Any, **kwargs: Any) -> SpaceHandle:
        raise FeatureUnavailableError("from_h5ad wraps synthesis/h5ad/create/ — not yet wired in the sdk")

    def embed_only(self, *args: Any, **kwargs: Any) -> SpaceHandle:
        raise FeatureUnavailableError("embed_only wraps synthesis/embed-only/ — not yet wired in the sdk")

    # --- helpers ---
    @staticmethod
    def _load_data(data: pd.DataFrame | str):
        file_extension = "csv"
        if isinstance(data, pd.DataFrame):
            buffer = io.BytesIO()
            data.to_csv(buffer, index=False)
            buffer.seek(0)
            return buffer, list(data.columns), file_extension
        if isinstance(data, str):
            file_extension = data.split(".")[-1] or "csv"
            columns = list(pd.read_csv(data, nrows=1).columns)
            return open(data, "rb"), columns, file_extension
        raise MantisError("data must be a pandas DataFrame or a file path string")

    @staticmethod
    def _sanitize_data_types(columns, data_types: dict[str, DataType | str]) -> list[dict]:
        """convert {column -> type} into the per-column boolean dicts the backend expects.
        columns not present in data_types are marked delete=True."""
        sanitized = []
        for column in columns:
            chosen = str(data_types.get(column, DataType.Delete))
            sanitized.append({str(dt): (str(dt) == chosen) for dt in DataType})
        return sanitized

    def _poll_until_done(
        self,
        map_id: str,
        *,
        on_progress: ProgressCallback | None = None,
        show_progress: bool = False,
        stall_timeout: float | None = 600.0,
    ) -> None:
        """poll synthesis/progress/<map_id>/ until completed or errored.

        stall_timeout guards against a pipeline that never advances (e.g. no celery worker):
        if progress doesn't change for that many seconds, raise instead of hanging forever.
        pass None to wait indefinitely."""
        bar = None
        if show_progress:
            try:
                from tqdm import tqdm

                bar = tqdm(total=100, desc="synthesizing space", unit="%")
            except ImportError:
                logger.debug("tqdm not installed; show_progress ignored")

        last = -1
        last_change = time.monotonic()
        try:
            while True:
                progress = self.http.request("GET", f"synthesis/progress/{map_id}")
                if progress.get("error"):
                    raise SpaceCreationError(progress["error"])

                pct = int(progress.get("progress", 0))
                if pct != last:
                    if on_progress is not None:
                        on_progress(pct, progress.get("message"), progress.get("progress_tree"))
                    if bar is not None:
                        bar.update(pct - max(last, 0))
                    last = pct
                    last_change = time.monotonic()

                if progress.get("completed") or pct >= 100:
                    return

                if stall_timeout is not None and (time.monotonic() - last_change) > stall_timeout:
                    raise SpaceCreationError(
                        f"synthesis stalled at {pct}% for over {stall_timeout:.0f}s "
                        f"(map_id={map_id}); is a celery worker running?"
                    )
                time.sleep(self.POLL_INTERVAL)
        finally:
            if bar is not None:
                bar.close()

    # --- browser open (delegates to the playwright Space) ---
    async def aopen(self, space_id: str, colab: bool = False):
        from .space import Space

        return await Space.create(
            space_id,
            _request=self._legacy_request_adapter(),
            cookie=self.http.cookie,
            config=self.http.config,
            colab=colab,
        )

    def _legacy_request_adapter(self):
        """adapt the new HttpClient.request to the (method, endpoint, **kwargs) signature
        the Space browser class expects for its occasional rest calls."""
        def _request(method: str, endpoint: str, rm_slash: bool = False, **kwargs):
            return self.http.request(method, endpoint, rm_slash=rm_slash, **kwargs)

        return _request


class MapsResource(_BaseResource):
    """read-side access to maps and their ideas/points."""

    def list(self, space_id: str) -> list[dict]:
        resp = self.http.request("GET", "/api/listMaps", params={"space_id": space_id})
        if isinstance(resp, dict):
            return resp.get("maps", resp.get("data", []))
        return resp or []

    def list_idea_ids(self, map_id: str) -> list[str]:
        resp = self.http.request("GET", "/api/listIdeas", params={"map_id": map_id})
        if isinstance(resp, dict):
            return resp.get("ideas", resp.get("ids", []))
        return resp or []

    def get_ideas(self, ids: list[str]) -> Any:
        return self.http.request("GET", "/api/getIdeas", params={"ids": ",".join(ids)})


class SpaceStatesResource(_BaseResource):
    """create/list space-states — a per-user live view of a space (selection, bags, active map).

    agents need a space-state id so their MCP tools know which space/map to act on; the backend
    only sets the X-Space-State-ID header when ws/chat is given a space_state_id. the browser
    mints one when you open a space; a headless sdk session mints one here (same cookie-auth
    endpoint the frontend uses)."""

    def create(self, space_id: str, name: str = "SDK") -> str:
        """create a space-state for a space and return its id."""
        resp = self.http.request(
            "POST", "/api/space-state", json={"space_id": space_id, "name": name}
        )
        if not isinstance(resp, dict) or "id" not in resp:
            raise MantisError(f"space-state create returned no id: {resp}")
        return resp["id"]

    def list(self, space_id: str) -> list[dict]:
        resp = self.http.request("GET", "/api/space-state", params={"space_id": space_id})
        return resp if isinstance(resp, list) else (resp or [])


class AnnotationsResource(_BaseResource):
    """text annotations are now served over rest (the old ws/space/ socket was removed)."""

    def list(self, map_id: str) -> list[dict]:
        resp = self.http.request("GET", "/api/getAnnotations", params={"map_id": map_id})
        if isinstance(resp, dict):
            return resp.get("annotations", [])
        return resp or []

    def create(self, map_id: str, payload: dict) -> Any:
        body = {"map_id": map_id, **payload}
        return self.http.request("POST", "/api/createAnnotation", json=body)


class SearchResource(_BaseResource):
    """server-side cluster-question generation. the route is currently disabled backend-side."""

    def cluster_questions(
        self,
        space_id: str,
        *,
        depth: int = 3,
        breadth: int = 3,
        sample_size: int = 8,
        max_questions: int = 0,
        context: str = "",
        output_format: str = "json",
    ) -> Any:
        raise FeatureUnavailableError(
            "GET /api/getClusterQuestionTrees/ is disabled on the backend "
            "(conduit/urls.py). re-enable the route to use cluster_questions()."
        )
