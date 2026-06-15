"""typed string enums for the sdk.
these subclass str so they serialize to json transparently while staying type-safe."""
from __future__ import annotations

from enum import Enum


class StrEnum(str, Enum):
    """3.10-safe stand-in for enum.StrEnum.
    str members compare/serialize as their value."""

    def __str__(self) -> str:
        return str(self.value)


class SpacePrivacy(StrEnum):
    PUBLIC = "public"
    PRIVATE = "private"
    SHARED = "shared"


class DataType(StrEnum):
    # values MUST match the backend serializer keys exactly (snake_case).
    # note: CustomModel is "custom_model" — the old sdk sent "customModel" which DRF
    # silently dropped, so custom models never actually applied.
    Title = "title"
    Semantic = "semantic"
    Numeric = "numeric"
    Categoric = "categoric"
    Date = "date"
    Links = "links"
    CustomModel = "custom_model"
    Image = "image"
    Geospatial = "geospatial"
    Coordinate1 = "coordinate1"
    Coordinate2 = "coordinate2"
    Connection = "connection"
    Vector = "vector"
    Delete = "delete"


# ordered list of every column type the backend serializer accepts; used to build the
# per-column boolean dicts. keep in sync with synthesis/serializing/serializers.py.
# attached after class creation so the enum metaclass doesn't treat it as a member.
DataType.All = list(DataType)  # type: ignore[attr-defined]


class AIProvider(StrEnum):
    OpenAI = "openai"
    HuggingFace = "huggingface"


class ReducerModels(StrEnum):
    UMAP = "UMAP"
    PCA = "PCA+UMAP"
    TSNE = "t-SNE"


class Provider(StrEnum):
    # agent-execution runtime providers, sent to the backend as `model_id` per run.
    # opencode is universally available; claude_code requires UserCapabilities.bedrock_enabled.
    OpenCode = "opencode"
    ClaudeCode = "claude_code"
