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


# --- visibility contract (backend rejects legacy is_public since #1958) ---

def _posted_form(transport):
    kwargs = transport.calls[0]["kwargs"]
    return kwargs.get("data") or kwargs.get("json")


def test_create_space_sends_visibility_not_is_public(client, transport):
    transport.queue = [{"map_id": "m1", "space_id": "s1"}]
    client.spaces.create("t", _df(), {"A": DataType.Title, "B": DataType.Semantic}, wait=False)
    form = _posted_form(transport)
    assert form is not None
    assert "is_public" not in form, "legacy is_public field is hard-rejected by the backend"
    assert form.get("visibility") == "private"  # SpacePrivacy.PRIVATE default


def test_create_space_public_privacy_maps_to_unlisted(client, transport):
    # 'public' is not user-settable on the backend; PUBLIC maps to 'unlisted'
    # (link-shareable) so the request is accepted instead of 400-ing.
    transport.queue = [{"map_id": "m1", "space_id": "s1"}]
    client.spaces.create(
        "t", _df(), {"A": DataType.Title, "B": DataType.Semantic},
        privacy_level="public", wait=False,
    )
    form = _posted_form(transport)
    assert form.get("visibility") == "unlisted"


def test_create_space_explicit_visibility_wins(client, transport):
    transport.queue = [{"map_id": "m1", "space_id": "s1"}]
    client.spaces.create(
        "t", _df(), {"A": DataType.Title, "B": DataType.Semantic},
        privacy_level="public", visibility="private", wait=False,
    )
    form = _posted_form(transport)
    assert form.get("visibility") == "private"


def test_from_github_sends_visibility_not_is_public(client, transport):
    transport.queue = [
        {"map_id": "m1", "space_id": "s1"},
        {"progress": 100, "completed": True, "error": None},
    ]
    client.spaces.from_github("https://github.com/o/r")
    payload = _posted_form(transport)
    assert "is_public" not in payload
    assert payload.get("visibility") == "private"


# --- pipeline config dicts (backend CreateMapSerializer requires all four since #1780) ---

def test_create_space_sends_all_four_configs(client, transport):
    import json as _json

    transport.queue = [{"map_id": "m1", "space_id": "s1"}]
    client.spaces.create("t", _df(), {"A": DataType.Title, "B": DataType.Semantic}, wait=False)
    form = _posted_form(transport)
    for key in ("embedding_config", "reduction_config", "clustering_config", "labeling_config"):
        assert key in form, f"{key} missing — backend 400s with 'This configuration object is required.'"
        parsed = _json.loads(form[key])
        assert isinstance(parsed, dict), f"{key} must be a JSON object"


def test_create_space_config_overrides_accepted(client, transport):
    import json as _json

    transport.queue = [{"map_id": "m1", "space_id": "s1"}]
    custom = {"reduction_method": "UMAP", "start_dimension": 128, "end_dimension": 2}
    client.spaces.create(
        "t", _df(), {"A": DataType.Title, "B": DataType.Semantic},
        reduction_config=custom, wait=False,
    )
    form = _posted_form(transport)
    assert _json.loads(form["reduction_config"])["start_dimension"] == 128
