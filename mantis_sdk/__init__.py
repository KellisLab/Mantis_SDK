"""Mantis SDK — pythonic client for the Mantis frontend + backend."""
from __future__ import annotations

from .agents import AgentEvent, AgentResult, AgentSession
from .client import MantisClient
from .config import ConfigurationManager
from .enums import AIProvider, DataType, Provider, ReducerModels, SpacePrivacy
from .exceptions import (
    AgentRunError,
    APIConnectionError,
    APIStatusError,
    AuthenticationError,
    ConfigurationError,
    ExecutionError,
    FeatureUnavailableError,
    MantisError,
    NotFoundError,
    ProviderUnavailableError,
    RateLimitError,
    SpaceCreationError,
)
from .notebook import Cell, Notebook
from .render_args import RenderArgs
from .resources import SpaceHandle

__version__ = "0.12.0"


def __getattr__(name: str):
    # Space pulls in playwright (the optional [browser] extra). import it lazily so the
    # rest-only install works without playwright present.
    if name == "Space":
        from .space import Space

        return Space
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "MantisClient",
    "ConfigurationManager",
    "RenderArgs",
    "Space",
    "SpaceHandle",
    "Notebook",
    "Cell",
    # agents
    "AgentSession",
    "AgentEvent",
    "AgentResult",
    # enums
    "DataType",
    "SpacePrivacy",
    "ReducerModels",
    "AIProvider",
    "Provider",
    # exceptions
    "MantisError",
    "ConfigurationError",
    "APIStatusError",
    "APIConnectionError",
    "AuthenticationError",
    "NotFoundError",
    "RateLimitError",
    "SpaceCreationError",
    "FeatureUnavailableError",
    "ExecutionError",
    "ProviderUnavailableError",
    "AgentRunError",
    "__version__",
]
