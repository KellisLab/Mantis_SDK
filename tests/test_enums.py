"""enum values must match the backend serializer keys and serialize as plain strings."""
import json

from mantis_sdk import AIProvider, DataType, ReducerModels, SpacePrivacy


def test_enums_serialize_as_strings():
    assert json.dumps({"r": ReducerModels.UMAP}) == '{"r": "UMAP"}'
    assert json.dumps({"p": SpacePrivacy.PRIVATE}) == '{"p": "private"}'
    assert json.dumps({"a": AIProvider.OpenAI}) == '{"a": "openai"}'


def test_custom_model_is_snake_case():
    # the old sdk used "customModel" which DRF silently dropped.
    assert DataType.CustomModel.value == "custom_model"


def test_datatype_all_matches_backend_serializer():
    # ALL_DATA_TYPES from synthesis/serializing/serializers.py + delete.
    expected = {
        "title", "semantic", "numeric", "categoric", "date", "links",
        "custom_model", "image", "geospatial", "coordinate1", "coordinate2",
        "connection", "vector", "delete",
    }
    assert {str(d) for d in DataType} == expected
