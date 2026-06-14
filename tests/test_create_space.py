"""create_space: data_types sanitization, the progress poll loop, and error handling."""
import pandas as pd
import pytest

from mantis_sdk import DataType, SpaceCreationError
from mantis_sdk.resources import SpacesResource


def test_sanitize_data_types_marks_unlisted_as_delete():
    columns = ["A", "B", "C"]
    types = {"A": DataType.Title, "B": DataType.Semantic}
    out = SpacesResource._sanitize_data_types(columns, types)
    assert len(out) == 3
    assert out[0]["title"] is True and out[0]["semantic"] is False
    assert out[1]["semantic"] is True
    # C was not listed → delete.
    assert out[2]["delete"] is True
    # every dict carries the full key set the serializer expects.
    assert set(out[0]) == {str(d) for d in DataType}


def _df():
    return pd.DataFrame({"A": ["x", "y"], "B": ["p", "q"]})


def test_create_space_polls_to_completion(client, transport):
    responses = [
        {"map_id": "m1", "space_id": "s1", "status": "processing"},  # POST landscape
        {"progress": 40, "message": "embedding", "completed": False, "error": None},
        {"progress": 100, "message": "done", "completed": True, "error": None},
    ]
    transport.queue = list(responses)

    progress_seen = []
    handle = client.spaces.create(
        "t", _df(), {"A": DataType.Title, "B": DataType.Semantic},
        on_progress=lambda p, m, t: progress_seen.append(p),
    )

    assert handle.space_id == "s1"
    assert handle.map_id == "m1"
    assert handle["space_id"] == "s1"  # dict back-compat.
    assert 40 in progress_seen and 100 in progress_seen
    # first call is the multipart POST to landscape.
    assert client.spaces.create and transport.calls[0]["method"] == "POST"
    assert transport.calls[0]["url"].endswith("/synthesis/landscape/")


def test_create_space_raises_on_pipeline_error(client, transport):
    transport.queue = [
        {"map_id": "m1", "space_id": "s1"},
        {"progress": 10, "error": "embedding failed"},
    ]
    with pytest.raises(SpaceCreationError, match="embedding failed"):
        client.spaces.create("t", _df(), {"A": DataType.Title, "B": DataType.Semantic})


def test_create_space_custom_models_length_validated(client, transport):
    transport.queue = [{"map_id": "m1", "space_id": "s1"}]
    with pytest.raises(Exception):
        client.spaces.create(
            "t", _df(), {"A": DataType.Title, "B": DataType.Semantic},
            custom_models=["only-one"],  # but there are 2 columns.
            wait=False,
        )


def test_create_space_stalls_out_instead_of_hanging(client, transport):
    # progress never advances past 0 → stall_timeout should raise rather than loop forever.
    def responder(method, url, kwargs):
        if url.endswith("/synthesis/landscape/"):
            return {"map_id": "m1", "space_id": "s1"}
        return {"progress": 0, "completed": False, "error": None}

    transport.responder = responder
    client.spaces.POLL_INTERVAL = 0  # don't actually sleep in the test.
    with pytest.raises(SpaceCreationError, match="stalled"):
        client.spaces.create(
            "t", _df(), {"A": DataType.Title, "B": DataType.Semantic}, stall_timeout=0.01,
        )


def test_create_space_no_wait_skips_polling(client, transport):
    transport.queue = [{"map_id": "m1", "space_id": "s1"}]
    handle = client.spaces.create(
        "t", _df(), {"A": DataType.Title, "B": DataType.Semantic}, wait=False,
    )
    assert handle.map_id == "m1"
    assert len(transport.calls) == 1  # only the POST, no progress polls.
