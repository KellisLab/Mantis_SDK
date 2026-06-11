"""notebook create/add_cell/execute flow and output parsing, fully mocked."""
import base64

import pytest

from mantis_sdk import ExecutionError

# a 1x1 png, base64.
_PNG_B64 = base64.b64encode(b"\x89PNG\r\n\x1a\n").decode()


def test_create_notebook_creates_notebook_and_session(client, transport):
    transport.queue = [
        {"success": True, "nid": "nb1"},        # notebook/create
        {"success": True, "session_id": "ses1"},  # sessions/create
    ]
    nb = client.notebooks.create("proj1", name="X", user_id="u1")
    assert nb.nid == "nb1"
    assert nb.session_id == "ses1"
    assert transport.calls[0]["url"].endswith("/api/notebook/create/")
    assert transport.calls[1]["url"].endswith("/api/sessions/create/")


def test_resolve_map_to_project(client, transport):
    transport.queue = [{"success": True, "project_id": "proj9"}]
    assert client.notebooks.resolve_map_to_project("map1") == "proj9"


def _notebook(client, transport):
    transport.queue = [
        {"success": True, "nid": "nb1"},
        {"success": True, "session_id": "ses1"},
    ]
    return client.notebooks.create("proj1", user_id="u1")


def test_cell_execute_polls_until_completed(client, transport):
    nb = _notebook(client, transport)
    transport.queue = [
        {"success": True},                                  # add_cell
        {"success": True, "task_id": "t1", "status": "pending"},  # execute
        {"status": "pending"},                              # status poll 1
        {"status": "completed", "result": {"outputs": [
            {"output_type": "stream", "name": "stdout", "text": "hello\n"},
        ]}},
    ]
    cell = nb.add_cell("print('hello')")
    outputs = cell.execute(poll_interval=0)
    assert outputs[0]["text"] == "hello\n"
    assert cell.text == "hello\n"


def test_cell_execute_eager_inline_result(client, transport):
    nb = _notebook(client, transport)
    transport.queue = [
        {"success": True},  # add_cell
        {"success": True, "status": "completed", "result": {"outputs": [
            {"output_type": "execute_result", "data": {"text/plain": "42", "image/png": _PNG_B64}},
        ]}},
    ]
    cell = nb.add_cell("x")
    cell.execute()
    assert cell.text == "42"
    assert cell.image_png_bytes() == base64.b64decode(_PNG_B64)


def test_cell_execute_raises_on_error_output(client, transport):
    nb = _notebook(client, transport)
    transport.queue = [
        {"success": True},
        {"success": True, "result": {"outputs": [
            {"output_type": "error", "ename": "ValueError", "evalue": "bad", "traceback": ["..."]},
        ]}},
    ]
    cell = nb.add_cell("raise ValueError('bad')")
    with pytest.raises(ExecutionError, match="ValueError"):
        cell.execute()
