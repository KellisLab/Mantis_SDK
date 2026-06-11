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
    transport.queue = [{
        "private": [{"space_id": "s1", "space_name": "Foo"}, {"space_id": "s2", "space_name": "Bar"}],
        "public": [{"space_id": "s3", "space_name": "Foo"}],
    }]
    ids = client.spaces.ids_by_name("Foo", ["private"])
    assert ids == ["s1"]
