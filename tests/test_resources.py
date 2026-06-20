"""annotations, getSpaces, maps, and feature-unavailable guards."""
import pytest

from mantis_sdk import FeatureUnavailableError


def test_get_spaces_passthrough(client, transport):
    transport.queue = [{"public": [], "featured": [], "private": [], "shared": [], "projects": []}]
    spaces = client.spaces.get_all()
    assert set(spaces) >= {"public", "private", "shared"}


def test_annotations_list_unwraps(client, transport):
    transport.queue = [{"ok": True, "annotations": [{"id": "a1"}]}]
    out = client.annotations.list("map1")
    assert out == [{"id": "a1"}]
    assert transport.calls[0]["url"].endswith("/api/getAnnotations/")
    assert transport.calls[0]["kwargs"]["params"]["map_id"] == "map1"


def test_cluster_questions_disabled(client):
    with pytest.raises(FeatureUnavailableError):
        client.search.cluster_questions("space1")


def test_from_molecules_scaffold_raises(client):
    with pytest.raises(FeatureUnavailableError):
        client.spaces.from_molecules()


def test_check_compatibility_reports_shape(client, transport):
    transport.queue = [{"public": [], "private": [], "shared": []}]
    result = client.check_compatibility()
    assert result["reachable"] is True
    assert result["getSpaces_shape_ok"] is True


def test_ids_by_name_filters_by_privacy(client, transport):
    # current getSpaces returns "id"; assert that key drives the lookup.
    transport.queue = [{
        "private": [{"id": "s1", "space_name": "Foo"}, {"id": "s2", "space_name": "Bar"}],
        "public": [{"id": "s3", "space_name": "Foo"}],
    }]
    ids = client.spaces.ids_by_name("Foo", ["private"])
    assert ids == ["s1"]


def test_ids_by_name_tolerates_legacy_space_id_key(client, transport):
    transport.queue = [{"private": [{"space_id": "s9", "space_name": "Foo"}]}]
    assert client.spaces.ids_by_name("Foo", ["private"]) == ["s9"]


# --- aliases ---

def test_alias_resolve_returns_space_id(client, transport):
    transport.queue = [{"project_id": "sp-9"}]
    assert client.aliases.resolve("m4m") == "sp-9"
    assert transport.calls[0]["url"].endswith("/api/getSpaceFromAlias/")
    assert transport.calls[0]["kwargs"]["params"]["alias"] == "m4m"


def test_alias_resolve_missing_returns_none(client, transport):
    # backend 400s when the alias isn't found → resolve() swallows to None.
    from mantis_sdk.exceptions import APIStatusError

    def boom(method, url, kwargs):
        raise APIStatusError("not found", status_code=400, body={"project_id": None})

    transport.responder = boom
    assert client.aliases.resolve("nope") is None


def test_alias_set_posts_body(client, transport):
    transport.queue = [{"ok": True}]
    client.aliases.set("sp-1", "m4m")
    call = transport.calls[0]
    assert call["url"].endswith("/api/setSpaceAlias/")
    assert call["kwargs"]["json"] == {"space_id": "sp-1", "alias": "m4m"}


def test_resolve_or_create_reuses_existing(client, transport):
    transport.queue = [{"project_id": "existing-space"}]
    space_id, created = client.aliases.resolve_or_create_space("m4m")
    assert (space_id, created) == ("existing-space", False)


def test_resolve_or_create_mints_deterministic(client, transport):
    # alias not found → deterministic uuid5, created=True, and stable across calls.
    from mantis_sdk.exceptions import APIStatusError

    def notfound(method, url, kwargs):
        raise APIStatusError("nf", status_code=400, body={})

    transport.responder = notfound
    a, created_a = client.aliases.resolve_or_create_space("m4m")
    b, _ = client.aliases.resolve_or_create_space("m4m")
    assert created_a is True
    assert a == b  # deterministic — same alias always yields the same space id


def test_create_passes_explicit_space_and_map_id(client, transport):
    import pandas as pd

    from mantis_sdk import DataType

    transport.queue = [{"map_id": "m-fixed", "space_id": "s-fixed"}]
    df = pd.DataFrame({"A": ["x", "y"], "B": ["p", "q"]})
    client.spaces.create("t", df, {"A": DataType.Title, "B": DataType.Semantic},
                         space_id="s-fixed", map_id="m-fixed", map_name="My Map", wait=False)
    form = transport.calls[0]["kwargs"]["data"]
    assert form["space_id"] == "s-fixed"
    assert form["map_id"] == "m-fixed"
    assert form["map_name"] == "My Map"


def test_create_defaults_map_name_to_space_name(client, transport):
    # without an explicit map_name the backend names the map "Untitled Map"; we default it to
    # the space name so a single-map space gets a sensible label.
    import pandas as pd

    from mantis_sdk import DataType

    transport.queue = [{"map_id": "m1", "space_id": "s1"}]
    df = pd.DataFrame({"A": ["x"], "B": ["p"]})
    client.spaces.create("My Space", df, {"A": DataType.Title, "B": DataType.Semantic}, wait=False)
    assert transport.calls[0]["kwargs"]["data"]["map_name"] == "My Space"
