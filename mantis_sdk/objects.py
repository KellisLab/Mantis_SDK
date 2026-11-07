"""Version-pinned semantic objects shared with Composer, notebooks and workflows.

These endpoints currently require a browser session and its CSRF cookie. Internal
service credentials alone do not substitute for the backend's session boundary.
"""
from __future__ import annotations

import re
from http.cookies import SimpleCookie
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from .exceptions import ConfigurationError

if TYPE_CHECKING:
    from .client import MantisClient


def _id(value: str) -> str:
    return str(UUID(str(value)))


def pinned_input(space_id: str, map_id: str, objects: list[dict], *, message: str = "") -> dict:
    """Construct the same request accepted by workflow Run and executeWorkflow.

    Content and claimed provenance are deliberately discarded. Resolution is a
    server operation, so this helper makes no claim that references are current.
    """
    if not isinstance(objects, list) or not 1 <= len(objects) <= 10:
        raise ValueError("provide 1–10 resolved object receipts")
    refs = []
    for obj in objects:
        if not isinstance(obj, dict):
            raise ValueError("each object must be a resolved receipt")
        uri = obj.get("uri", "")
        if not isinstance(uri, str) or not uri.startswith("mantis://object/"):
            raise ValueError("object URI must start with mantis://object/")
        uri = "mantis://object/" + _id(uri[len("mantis://object/"):])
        version = obj.get("version")
        digest = obj.get("content_hash", "")
        if type(version) is not int or version < 1 or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("each object needs a positive version and SHA-256 content_hash")
        scope = obj.get("scope")
        if scope is not None and scope != {"space_id": _id(space_id), "map_id": _id(map_id)}:
            raise ValueError("objects must belong to the requested Space and map")
        if obj.get("state", "current") != "current":
            raise ValueError("resolve and review a current object before reuse")
        refs.append({"uri": uri, "version": version, "content_hash": digest})
    if not isinstance(message, str) or len(message) > 8000:
        raise ValueError("message must be text of at most 8,000 characters")
    return {"schema": "mantis.object-request/v1", "space_id": _id(space_id),
            "map_id": _id(map_id), "objects": refs, "message": message}


class ObjectsResource:
    """The existing Knowledge object API, with explicit session/CSRF protection."""

    def __init__(self, client: MantisClient):
        self._client = client

    def _headers(self) -> dict[str, str]:
        cookies = SimpleCookie()
        cookies.load(self._client.http.cookie or "")
        csrf = cookies.get("csrftoken")
        if not cookies.get("sessionid") or csrf is None:
            raise ConfigurationError(
                "semantic objects require a Django sessionid and csrftoken in the session cookie; "
                "internal-service credentials alone are not supported by these endpoints"
            )
        # Django's secure-request CSRF check also requires a same-origin Referer.
        return {"X-CSRFToken": csrf.value, "Referer": self._client.http.build_url("/")}

    def _request(self, action: str, space_id: str, map_id: str, body: dict) -> dict[str, Any]:
        return self._client.http.request(
            "POST", f"/api/knowledge/{action}", headers=self._headers(),
            params={"space_id": _id(space_id), "map_id": _id(map_id)}, json=body,
        )

    def catalog(self, space_id: str, map_id: str, *, limit: int = 25) -> dict:
        """List bounded objects, including unavailable/stale states and truncation."""
        return self._request("object-catalog", space_id, map_id, {"limit": limit})

    def resolve(self, space_id: str, map_id: str, uri: str, *, version: int | None = None) -> dict:
        body: dict[str, Any] = {"uri": uri}
        if version is not None:
            body["version"] = version
        return self._request("object-resolve", space_id, map_id, body)["object"]

    def register(self, space_id: str, map_id: str, *, kind: str, binding: dict,
                 reviewed: bool, uri: str | None = None, version: int | None = None) -> dict:
        """Publish or explicitly refresh an original after reviewing its scope."""
        body = {"kind": kind, "binding": binding, "reviewed": reviewed}
        if uri is not None:
            body.update(uri=uri, version=version)
        return self._request("object-register", space_id, map_id, body)["object"]

    def reuse(self, space_id: str, map_id: str, obj: dict, *, purpose: str = "discussion") -> dict:
        ref = pinned_input(space_id, map_id, [obj])["objects"][0]
        return self._request("object-reuse", space_id, map_id, {**ref, "purpose": purpose})

    @staticmethod
    def workflow_input(space_id: str, map_id: str, objects: list[dict], *, message: str = "") -> dict:
        return pinned_input(space_id, map_id, objects, message=message)

    def run_workflow(self, workflow: dict, object_input: dict, *, saved_workflow_id: str | None = None,
                     payload: dict | None = None) -> dict:
        """Submit through the same authenticated asynchronous admission as the UI."""
        if object_input.get('schema') != 'mantis.object-request/v1':
            raise ValueError('submit a pinned object request, not a server receipt')
        request = pinned_input(object_input["space_id"], object_input["map_id"],
                               object_input["objects"], message=object_input.get("message", ""))
        body = {"workflow_json": workflow, "input_text": request,
                "chat_id": str(uuid4()), "thread_id": str(uuid4()), "payload": payload}
        if saved_workflow_id is not None:
            body["saved_workflow_id"] = _id(saved_workflow_id)
        return self._client.http.request("POST", "/api/orchestration/execute", json=body, headers=self._headers())

    def execution(self, execution_id: str) -> dict:
        """Read an owned execution; the server rechecks protected input access."""
        return self._client.http.request(
            "GET", f"/api/orchestration/executions/{_id(execution_id)}", headers=self._headers(),
        )


    def options(self, space_id: str, map_id: str) -> dict:
        """Typed operation schemas, available columns and explicit materialization budgets."""
        return self._request("asset-options", space_id, map_id, {})

    def capture_cohort(self, space_id: str, map_id: str, *, columns: list[str], reviewed: bool,
                       point_ids: list[str] | None = None, all_points: bool = False,
                       name: str = "Selected cohort", request_id: str | None = None) -> dict:
        body = {"columns": columns, "reviewed": reviewed, "all_points": all_points, "name": name,
                "request_id": _id(request_id) if request_id else str(uuid4())}
        if point_ids is not None:
            body["point_ids"] = [_id(value) for value in point_ids]
        return self._request("asset-capture", space_id, map_id, body)["object"]

    def run_operation(self, space_id: str, map_id: str, obj: dict, operation: str, *,
                      parameters: dict | None = None, reviewed: bool, request_id: str | None = None,
                      name: str = "Analysis result") -> dict:
        ref = pinned_input(space_id, map_id, [obj])["objects"][0]
        return self._request("asset-execute", space_id, map_id, {"input": ref, "operation": operation,
            "operation_version": "1", "parameters": parameters or {}, "reviewed": reviewed, "name": name,
            "request_id": _id(request_id) if request_id else str(uuid4())})["object"]

    def read_table(self, space_id: str, map_id: str, obj: dict, *, allow_stale: bool = False,
                   offset: int = 0, limit: int = 100) -> dict:
        ref = {key: obj[key] for key in ("uri", "version", "content_hash")}
        return self._request("asset-read", space_id, map_id, {**ref, "allow_stale": allow_stale,
                             "offset": offset, "limit": limit})

    def download(self, space_id: str, map_id: str, obj: dict, *, part: str | None = None,
                 allow_stale: bool = False) -> bytes:
        ref = {key: obj[key] for key in ("uri", "version", "content_hash")}
        body = {**ref, "allow_stale": allow_stale}
        if part is not None:
            body["part"] = part
        return self._client.http.request("POST", "/api/knowledge/asset-download", headers=self._headers(),
            params={"space_id": _id(space_id), "map_id": _id(map_id)}, json=body, response_type="bytes")

    def dataframe(self, space_id: str, map_id: str, obj: dict, *, allow_stale: bool = False):
        import json

        import pandas as pd
        data = json.loads(self.download(space_id, map_id, obj, allow_stale=allow_stale))
        if data.get("schema") != "mantis.table/v1":
            raise ValueError("object is not a supported table")
        columns = list(data["columns"])
        columns += [key for key in ("_mantis_point_id", "_mantis_row_id") if any(key in row for row in data["rows"])]
        frame = pd.DataFrame(data["rows"], columns=columns)
        frame.attrs["mantis_object"] = {key: obj[key] for key in ("uri", "version", "content_hash")}
        return frame

    def publish_table(self, space_id: str, map_id: str, *, columns: list[str], rows: list[dict],
                      reviewed: bool, name: str = "Published table", inputs: list[dict] | None = None,
                      target: dict | None = None, request_id: str | None = None) -> dict:
        refs = pinned_input(space_id, map_id, inputs)["objects"] if inputs else []
        body = {"columns": columns, "rows": rows, "name": name, "inputs": refs, "reviewed": reviewed,
                "request_id": _id(request_id) if request_id else str(uuid4())}
        if target:
            body.update({key: target[key] for key in ("uri", "version", "content_hash")})
        return self._request("asset-publish", space_id, map_id, body)["object"]

    def publish_dataframe(self, space_id: str, map_id: str, frame, **kwargs) -> dict:
        import json
        keys = [key for key in frame.columns if not str(key).startswith("_mantis_")]
        selected = frame.loc[:, keys].copy()
        selected.columns = [str(key) for key in keys]
        if len(selected.columns) != len(set(selected.columns)):
            raise ValueError("use unique string column names")
        rows = json.loads(selected.to_json(orient="records", date_format="iso"))
        return self.publish_table(space_id, map_id, columns=list(selected.columns), rows=rows, **kwargs)

    def lineage(self, space_id: str, map_id: str, obj: dict) -> dict:
        return self._request("asset-lineage", space_id, map_id,
                             {key: obj[key] for key in ("uri", "version", "content_hash")})

    def refresh(self, space_id: str, map_id: str, obj: dict, *, reviewed: bool, cascade: bool = False,
                request_id: str | None = None) -> dict:
        return self._request("asset-refresh", space_id, map_id, {
            **{key: obj[key] for key in ("uri", "version", "content_hash")}, "reviewed": reviewed, "cascade": cascade,
            "request_id": _id(request_id) if request_id else str(uuid4())})["object"]

    def import_artifact(self, space_id: str, map_id: str, *, reviewed: bool,
                        build_artifact_id: int | None = None, representation_id: str | None = None,
                        name: str = "Build artifact", request_id: str | None = None) -> dict:
        if (build_artifact_id is None) == (representation_id is None):
            raise ValueError("choose exactly one build artifact or representation")
        return self._request("asset-import", space_id, map_id, {
            "reviewed": reviewed, "build_artifact_id": build_artifact_id, "representation_id": representation_id,
            "name": name, "request_id": _id(request_id) if request_id else str(uuid4())})["object"]

    def representation(self, space_id: str, map_id: str, obj: dict, *, allow_stale: bool = False):
        import io
        import json

        import numpy as np
        manifest = json.loads(self.download(space_id, map_id, obj, allow_stale=allow_stale))
        chunks, size = [], 0
        for part in manifest["chunks"]:
            raw = self.download(space_id, map_id, obj, part=part["digest"], allow_stale=allow_stale)
            size += len(raw)
            if size > 32 * 1024 * 1024:
                raise ValueError("matrix exceeds the 32 MB materialization budget")
            array = np.load(io.BytesIO(raw), allow_pickle=False)
            if array.ndim != 2 or array.shape[1] != manifest["dims"] or not np.isfinite(array).all():
                raise ValueError("invalid matrix chunk")
            chunks.append(array)
        matrix = np.concatenate(chunks) if chunks else np.empty((0, manifest["dims"]), dtype="float32")
        if list(matrix.shape) != [manifest["count"], manifest["dims"]]:
            raise ValueError("matrix shape differs from manifest")
        return matrix

    @staticmethod
    def operation_workflow(operation: str, *, parameters: dict | None = None, name: str = "Registered analysis") -> dict:
        """A reusable Jarvis workflow: supply a reviewed object request separately."""
        return {"name": name, "nodes": [{"id": "analyze", "type": "object_operation", "properties": {
            "operation": operation, "operation_version": "1", "parameters": parameters or {}, "name": name}}],
            "connections": []}

    def representation_items(self, space_id: str, map_id: str, obj: dict, *, allow_stale: bool = False):
        """The exact item/ordinal order corresponding to representation matrix rows (data extra)."""
        import io
        import json

        import pandas as pd
        manifest = json.loads(self.download(space_id, map_id, obj, allow_stale=allow_stale))
        raw = self.download(space_id, map_id, obj, part=manifest['items_digest'], allow_stale=allow_stale)
        frame = pd.read_parquet(io.BytesIO(raw))
        if len(frame) != manifest['count'] or 'item_id' not in frame or 'ordinal' not in frame:
            raise ValueError('representation items differ from the matrix manifest')
        if frame['ordinal'].tolist() != list(range(len(frame))):
            raise ValueError('representation item order is not contiguous')
        return frame
