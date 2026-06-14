"""notebook subsystem: drive mantis notebooks (kernels) from python.

flow (verified against mantisnotebook/): resolve a map to its project, create a notebook +
session, add cells, execute them (async task → poll status), and snapshot via checkpoints/dill.

all routes are mounted under /api/ and are csrf-exempt; auth is the same cookie or
internal-service headers the rest of the sdk uses."""
from __future__ import annotations

import base64
import logging
import time
from typing import TYPE_CHECKING, Any

from .exceptions import ExecutionError, MantisError

if TYPE_CHECKING:
    from .resources import MantisClientProtocol

logger = logging.getLogger("mantis_sdk")


class Cell:
    """a single notebook cell. add_cell returns one; call execute() to run it."""

    def __init__(self, notebook: Notebook, index: int, content: str, cell_type: str = "code"):
        self.notebook = notebook
        self.index = index
        self.content = content
        self.cell_type = cell_type
        self.outputs: list[dict] = []

    def __repr__(self) -> str:
        return f"Cell(index={self.index}, type={self.cell_type!r})"

    def execute(self, *, timeout: float = 120.0, poll_interval: float = 1.0) -> list[dict]:
        """execute this cell and block until results are ready. returns jupyter-style outputs."""
        http = self.notebook._http
        resp = http.request(
            "POST", "/api/sessions/execute",
            json={
                "session_id": self.notebook.session_id,
                "project_id": self.notebook.project_id,
                "cell_index": self.index,
            },
        )
        if not resp.get("success", True):
            raise ExecutionError(f"execute failed: {resp.get('error')}")

        # eager mode may return the result inline; otherwise poll by task_id.
        result = resp.get("result")
        if result is None:
            task_id = resp.get("task_id")
            if not task_id:
                raise ExecutionError(f"execute returned neither result nor task_id: {resp}")
            result = self._poll_result(task_id, timeout=timeout, poll_interval=poll_interval)

        self.outputs = result.get("outputs", []) if isinstance(result, dict) else []
        self._raise_on_error()
        return self.outputs

    def _poll_result(self, task_id: str, *, timeout: float, poll_interval: float) -> dict:
        http = self.notebook._http
        deadline = time.monotonic() + timeout
        while True:
            status = http.request(
                "GET", "/api/sessions/execute/status", params={"task_id": task_id}
            )
            if status.get("status") == "completed":
                return status.get("result", {})
            if status.get("status") == "failed" or status.get("error"):
                raise ExecutionError(f"cell execution failed: {status.get('error')}")
            if time.monotonic() > deadline:
                raise ExecutionError(f"cell execution timed out after {timeout}s")
            time.sleep(poll_interval)

    def _raise_on_error(self) -> None:
        for out in self.outputs:
            if out.get("output_type") == "error":
                raise ExecutionError(
                    f"{out.get('ename')}: {out.get('evalue')}\n" + "\n".join(out.get("traceback", []))
                )

    @property
    def text(self) -> str:
        """concatenate stdout streams and text/plain results."""
        parts: list[str] = []
        for out in self.outputs:
            if out.get("output_type") == "stream":
                parts.append(out.get("text", ""))
            elif out.get("output_type") in ("execute_result", "display_data"):
                data = out.get("data", {})
                if "text/plain" in data:
                    parts.append(data["text/plain"])
        return "".join(parts)

    def image_png_bytes(self) -> bytes | None:
        """decode the first image/png output, if any."""
        for out in self.outputs:
            data = out.get("data", {}) if isinstance(out, dict) else {}
            if "image/png" in data:
                return base64.b64decode(data["image/png"])
        return None


class Notebook:
    """a notebook bound to a backend session. created via client.notebooks.create()."""

    def __init__(self, resource: NotebooksResource, *, nid: str, session_id: str,
                 project_id: str, user_id: str, name: str):
        self._resource = resource
        self._http = resource.http
        self.nid = nid
        self.session_id = session_id
        self.project_id = project_id
        self.user_id = user_id
        self.name = name
        self.cells: list[Cell] = []

    def __repr__(self) -> str:
        return f"Notebook(nid={self.nid!r}, session_id={self.session_id!r}, cells={len(self.cells)})"

    def add_cell(self, content: str, cell_type: str = "code") -> Cell:
        """append a cell to the notebook and return a handle to it."""
        resp = self._http.request(
            "POST", "/api/notebook/add_cell",
            json={
                "session_id": self.session_id,
                "project_id": self.project_id,
                "content": content,
                "cell_type": cell_type,
                "position": -1,
            },
        )
        if not resp.get("success", True):
            raise MantisError(f"add_cell failed: {resp.get('error')}")
        cell = Cell(self, index=len(self.cells), content=content, cell_type=cell_type)
        self.cells.append(cell)
        return cell

    def get_content(self) -> dict:
        """fetch the current notebook json from the backend."""
        return self._http.request(
            "GET", "/api/notebook/content",
            params={"session_id": self.session_id, "project_id": self.project_id},
        )

    # --- checkpoints (kernel state snapshots) ---
    def checkpoint(self, name: str | None = None) -> Any:
        return self._http.request(
            "POST", "/api/sessions/checkpoint/save",
            json={"session_id": self.session_id, "name": name or "checkpoint"},
        )

    def list_checkpoints(self) -> Any:
        return self._http.request(
            "GET", "/api/sessions/checkpoint/list", params={"session_id": self.session_id}
        )

    def load_checkpoint(self, checkpoint_id: str) -> Any:
        return self._http.request(
            "POST", "/api/sessions/checkpoint/load",
            json={"session_id": self.session_id, "checkpoint_id": checkpoint_id},
        )

    def delete_checkpoint(self, checkpoint_id: str) -> Any:
        return self._http.request(
            "POST", "/api/sessions/checkpoint/delete",
            json={"session_id": self.session_id, "checkpoint_id": checkpoint_id},
        )

    def export(self) -> bytes:
        """download a dill snapshot of the kernel session."""
        return self._http.request(
            "GET", f"/api/sessions/download/{self.session_id}"
        )


class NotebooksResource:
    """create and resolve notebooks. exposed as client.notebooks."""

    def __init__(self, client: MantisClientProtocol):
        self._client = client
        self.http = client.http

    def resolve_map_to_project(self, map_id: str) -> str:
        """map a map_id to its owning project (notebook) id."""
        resp = self.http.request(
            "POST", "/api/notebook/resolve_map_to_project", json={"map_id": map_id}
        )
        if not resp.get("success", True) or "project_id" not in resp:
            raise MantisError(f"resolve_map_to_project failed: {resp.get('error', resp)}")
        return resp["project_id"]

    def create(
        self,
        project_id: str,
        name: str = "Untitled",
        user_id: str | None = None,
        *,
        nid: str | None = None,
    ) -> Notebook:
        """create a notebook and an attached session ready for add_cell/execute.

        user_id defaults to the configured internal_user_id; one of the two is required
        because the backend binds notebooks/sessions to a user."""
        user_id = user_id or self.http.config.internal_user_id
        if not user_id:
            raise MantisError("user_id is required (or set internal_user_id on the config)")

        # 1. create the notebook record.
        created = self.http.request(
            "POST", "/api/notebook/create",
            json={"user_id": user_id, "project_id": project_id, "notebook_name": name, **({"nid": nid} if nid else {})},
        )
        if not created.get("success", True) or "nid" not in created:
            raise MantisError(f"notebook create failed: {created.get('error', created)}")
        nid = created["nid"]

        # 2. open a session against that notebook (required before add_cell/execute).
        session = self.http.request(
            "POST", "/api/sessions/create",
            json={"user_id": user_id, "project_id": project_id, "nid": nid},
        )
        if not session.get("success", True) or "session_id" not in session:
            raise MantisError(f"session create failed: {session.get('error', session)}")

        return Notebook(
            self,
            nid=nid,
            session_id=session["session_id"],
            project_id=project_id,
            user_id=user_id,
            name=name,
        )

    def from_map(self, map_id: str, name: str = "Untitled", user_id: str | None = None) -> Notebook:
        """convenience: resolve a map to its project, then create a notebook there."""
        project_id = self.resolve_map_to_project(map_id)
        return self.create(project_id, name=name, user_id=user_id)
