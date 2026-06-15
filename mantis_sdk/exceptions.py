"""exception hierarchy for the mantis sdk.
all sdk errors derive from MantisError so callers can catch broadly or precisely."""
from __future__ import annotations

from typing import Any


class MantisError(Exception):
    """base class for every error raised by the sdk."""


class ConfigurationError(MantisError):
    """raised when the client is misconfigured (missing auth, bad host, etc.)."""


class APIStatusError(MantisError):
    """raised when the backend returns a non-2xx response.
    carries the status code and parsed body for precise handling."""

    def __init__(self, message: str, *, status_code: int, body: Any = None, url: str | None = None):
        self.status_code = status_code
        self.body = body
        self.url = url
        super().__init__(message)


class AuthenticationError(APIStatusError):
    """raised on 401/403 — the cookie or internal-service headers are missing or invalid."""


class NotFoundError(APIStatusError):
    """raised on 404 — the resource or route does not exist."""


class RateLimitError(APIStatusError):
    """raised on 429 — the client is being throttled."""


class APIConnectionError(MantisError):
    """raised when the request never reached the server (dns, refused, timeout)."""


class SpaceCreationError(MantisError):
    """raised when the synthesis pipeline reports an error while building a space."""


class FeatureUnavailableError(MantisError):
    """raised when an sdk method targets a backend route that is disabled or not yet wired.
    the message names the route so the user knows what is missing."""


class ExecutionError(MantisError):
    """raised when a notebook cell execution fails or times out."""


class ProviderUnavailableError(MantisError):
    """raised when an agent provider isn't usable for the user (e.g. claude_code without
    bedrock_enabled). prevents the backend's silent fallback to the default provider."""


class AgentRunError(MantisError):
    """raised when an agent run fails mid-stream (the backend emits a chat_fail event)."""
