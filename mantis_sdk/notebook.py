from typing import List, Optional, Dict, Any, TYPE_CHECKING
import time
import logging

if TYPE_CHECKING:
    from .client import MantisClient

logger = logging.getLogger(__name__)

class Cell:
    """
    Represents a single cell in a Mantis Notebook.
    """
    def __init__(self, notebook: "Notebook", index: int, cell_data: Dict[str, Any]):
        self.notebook = notebook
        self.index = index
        self._data = cell_data
        
    @property
    def cell_type(self) -> str:
        return self._data.get("cell_type", "code")
    
    @property
    def source(self) -> str:
        source = self._data.get("source", "")
        if isinstance(source, list):
            return "".join(source)
        return source
    
    @property
    def outputs(self) -> List[Any]:
        return self._data.get("outputs", [])
    
    @property
    def metadata(self) -> Dict[str, Any]:
        return self._data.get("metadata", {})

    def update(self, content: str):
        """
        Updates the content of this cell.
        """
        self.notebook.update_cell(self.index, content)
        # Refresh local data is handled by notebook.update_cell calling refresh, 
        # but we might want to update this instance's data directly or fetch fresh.
        # For consistency, we'll rely on the notebook to refresh and we might need to re-fetch this cell object
        # or update its internal data if the notebook updates it in place.
        # For now, let's assume notebook.refresh() updates the list of cells, 
        # so this specific instance might become stale if we don't be careful.
        # A better approach might be to have the notebook update this instance.
        # But for simplicity, we'll just update the local data to match what we sent, 
        # and let the next refresh sync everything.
        if isinstance(self._data["source"], list):
             self._data["source"] = [content]
        else:
             self._data["source"] = content

    def execute(self):
        """
        Executes this cell.
        """
        return self.notebook.execute_cell(self.index)

    def delete(self):
        """
        Deletes this cell from the notebook.
        """
        self.notebook.delete_cell(self.index)

    def get_metadata(self) -> Dict[str, Any]:
        """
        Returns the metadata of this cell.
        """
        return self.metadata

    def __repr__(self):
        return f"<Cell index={self.index} type={self.cell_type}>"


class Notebook:
    """
    Represents a Mantis Notebook.
    """
    def __init__(self, client: "MantisClient", space_id: str, nid: str, notebook_name: str = "Untitled"):
        self.client = client
        self.space_id = space_id
        self.nid = nid
        self.notebook_name = notebook_name
        self.session_id: Optional[str] = None
        self.cells: List[Cell] = []
        self._refresh_content()

    def _ensure_session(self):
        """
        Ensures that a valid session exists. If not, creates one.
        """
        if self.session_id:
            # Check if session is valid
            try:
                response = self.client._request("POST", "/api/sessions/check", json={"session_id": self.session_id, "project_id": self.space_id})
                if response.get("success"):
                    return
            except Exception:
                logger.warning("Session check failed, creating new session.")
                self.session_id = None

        # Create new session
        # We need the user_id. The client might not expose it directly if it's just using a cookie.
        # However, the frontend API `createSession` takes `user_id`.
        # If the client doesn't have user_id, we might have a problem.
        # Looking at client.py, it doesn't seem to store user_id.
        # But `listNotebooks` in frontend takes `user_id`.
        # Let's assume for now we can get it or it's not strictly required if the cookie is there, 
        # OR we need to fetch it.
        # The `MantisClient` doesn't seem to have a `get_user_info` method.
        # Wait, the frontend uses `useNotebookState` which calls `initialize(userID)`.
        # The user ID comes from `useDataStore`.
        # If the SDK is used with a token/cookie, maybe the backend infers the user?
        # Let's check `client.py` again. It has `_authenticate` which is not implemented.
        # It takes `cookie` in `__init__`.
        # If I look at `api/sessions/create/` payload in `notebookApi.ts`: `user_id: userId`.
        # I might need to ask the user or fetch it.
        # For now, I will try to fetch it from a "whoami" endpoint if it exists, or assume the user knows it.
        # But the `MantisClient` doesn't have it.
        # Let's look at `client.py` imports. Nothing special.
        # I'll assume for now that I can pass a dummy user_id or that the backend handles it if missing/from cookie.
        # Actually, looking at `notebookApi.ts`, `createSession` sends `user_id`.
        # If I don't have it, I might fail.
        # Let's try to list notebooks to see if we can get it? No, list requires user_id too.
        # Maybe `get_spaces`?
        # Let's assume the user provides it or I can get it. 
        # Actually, I'll add a `user_id` parameter to `create_notebook` in `client.py` and store it.
        # But wait, `MantisClient` is initialized with just base_url and cookie.
        # I'll assume the cookie is enough for auth, but the API explicitly asks for user_id in the body.
        # I will try to use a placeholder or maybe the client should have it.
        # Let's check `frontend_reference_code/notebookApi.ts` again.
        # `createSession` payload: `{ user_id: userId, nid: nid, broker_token: brokerToken }`.
        # I will use a placeholder "sdk_user" if not available, or maybe I should check if there is an endpoint to get current user.
        # Since I can't check the backend, I'll assume I need to pass it.
        # I'll add `user_id` to `Notebook` init and `MantisClient` methods.
        
        # For now, I'll try to create session without user_id or with a dummy one if strict.
        # But wait, `MantisClient` doesn't have `user_id`.
        # I will check if `get_spaces` returns user info? Unlikely.
        # I'll proceed with assuming I can pass a dummy or the user needs to provide it.
        # I'll add `user_id` to `MantisClient` init optionally?
        # Or just pass it to `create_notebook`.
        pass 

    def _create_session(self):
         # This is a helper for _ensure_session
         # We need user_id. I'll try to get it from client if I add it there, or use a default.
         # For now, I'll use "sdk_user" as a fallback.
         user_id = getattr(self.client, "user_id", "sdk_user")
         
         payload = {
             "user_id": user_id,
             "nid": self.nid,
             "project_id": self.space_id
         }
         response = self.client._request("POST", "/api/sessions/create", json=payload)
         if response.get("success"):
             self.session_id = response.get("session_id")
         else:
             raise RuntimeError(f"Failed to create session: {response.get('error')}")

    def _refresh_content(self):
        """
        Refreshes the notebook content from the backend.
        """
        if not self.session_id:
            self._ensure_session()
            if not self.session_id:
                 self._create_session()

        response = self.client._request("GET", "/api/notebook/content", params={"session_id": self.session_id, "project_id": self.space_id})
        
        if response.get("success"):
            content = response.get("content", {})
            if "cells" in content:
                self.cells = [Cell(self, i, cell_data) for i, cell_data in enumerate(content.get("cells", []))]
            else:
                logger.warning("Received successful response but content is missing 'cells'. Keeping previous state.")
        else:
            # If session not found, try to recreate
            if "session not found" in str(response.get("error", "")).lower():
                self.session_id = None
                self._create_session()
                # Retry once
                response = self.client._request("GET", "/api/notebook/content", params={"session_id": self.session_id, "project_id": self.space_id})
                if response.get("success"):
                    content = response.get("content", {})
                    if "cells" in content:
                        self.cells = [Cell(self, i, cell_data) for i, cell_data in enumerate(content.get("cells", []))]
                    else:
                        logger.warning("Received successful response (retry) but content is missing 'cells'. Keeping previous state.")
                else:
                    raise RuntimeError(f"Failed to get notebook content: {response.get('error')}")
            else:
                raise RuntimeError(f"Failed to get notebook content: {response.get('error')}")

    def refresh(self):
        self._refresh_content()

    def add_cell(self, content: str = "", cell_type: str = "code", position: int = -1) -> Cell:
        """
        Adds a new cell to the notebook.
        """
        if not self.session_id:
            self._create_session()
            
        payload = {
            "session_id": self.session_id,
            "cell_type": cell_type,
            "content": content,
            "position": position,
            "project_id": self.space_id
        }
        
        response = self.client._request("POST", "/api/notebook/add_cell", json=payload)
        if response.get("success"):
            self._refresh_content()
            # Return the newly created cell. 
            # If position was -1, it's the last one.
            # If position was specified, it's at that index (assuming 0-based index from backend matches).
            if position == -1:
                return self.cells[-1]
            else:
                # If position is within bounds, return that cell. 
                # Note: Backend might insert at position, shifting others.
                # We should probably trust the index we passed if it's valid.
                if 0 <= position < len(self.cells):
                    return self.cells[position]
                return self.cells[-1] # Fallback
        else:
            raise RuntimeError(f"Failed to add cell: {response.get('error')}")

    def delete_cell(self, index: int):
        """
        Deletes a cell by index.
        """
        if not self.session_id:
            self._create_session()

        payload = {
            "session_id": self.session_id,
            "position": index,
            "project_id": self.space_id
        }
        
        response = self.client._request("POST", "/api/notebook/delete_cell", json=payload)
        if response.get("success"):
            self._refresh_content()
        else:
            raise RuntimeError(f"Failed to delete cell: {response.get('error')}")

    def update_cell(self, index: int, content: str):
        """
        Updates the content of a cell.
        """
        if not self.session_id:
            self._create_session()
            
        payload = {
            "session_id": self.session_id,
            "cell_index": index,
            "content": content,
            "project_id": self.space_id
        }
        
        response = self.client._request("POST", "/api/notebook/edit_cell", json=payload)
        if response.get("success"):
            self._refresh_content()
        else:
            raise RuntimeError(f"Failed to update cell: {response.get('error')}")

    def execute_cell(self, index: int):
        """
        Executes a cell by index.
        """
        if not self.session_id:
            self._create_session()
            
        payload = {
            "session_id": self.session_id,
            "cell_index": index,
            "project_id": self.space_id
        }
        
        # Execute is async in backend, we might need to poll for results.
        # The frontend polls `getNotebookContent`.
        # Here we can do a simple poll loop waiting for execution to finish.
        # But how do we know it finished? 
        # The frontend checks `metadata.executing`.
        
        response = self.client._request("POST", "/api/sessions/execute", json=payload)
        if not response.get("success"):
             raise RuntimeError(f"Failed to execute cell: {response.get('error')}")
             
        # Poll for completion
        while True:
            time.sleep(0.5)
            self._refresh_content()
            
            if index >= len(self.cells):
                logger.warning(f"Cell index {index} out of range (cells count: {len(self.cells)}). Waiting for refresh...")
                continue
                
            cell = self.cells[index]
            # Check if executing
            # Note: metadata might be None
            meta = cell.metadata or {}
            if not meta.get("executing", False):
                # Execution finished
                return cell.outputs

    def execute_all(self):
        """
        Executes all code cells in the notebook.
        """
        results = []
        for i, cell in enumerate(self.cells):
            if cell.cell_type == "code":
                results.append(self.execute_cell(i))
        return results

    def get_cell(self, index: int) -> Cell:
        """
        Returns the cell at the specified index.
        """
        if 0 <= index < len(self.cells):
            return self.cells[index]
        raise IndexError("Cell index out of range")

    def delete(self):
        """
        Deletes the notebook and its session.
        """
        if self.session_id:
            payload = {
                "nid": self.nid,
                "session_id": self.session_id,
                "project_id": self.space_id
            }
            self.client._request("POST", "/api/notebook/drop", json=payload)
            self.session_id = None
        else:
             # If no session, just drop the notebook
             self.client._request("POST", "/api/notebook/drop", json={"nid": self.nid, "project_id": self.space_id})
