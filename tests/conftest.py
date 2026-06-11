"""shared test fixtures. all tests are fully mocked — no live server, no browser."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from mantis_sdk import ConfigurationManager, MantisClient


class RecordingTransport:
    """stand-in for Transport: records calls and returns scripted responses.

    set .responder to a callable(method, url, kwargs) -> json, or push onto .queue."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.queue: list[Any] = []
        self.responder: Callable[[str, str, dict], Any] | None = None
        self.default_timeout = 60.0

    def request(self, method: str, url: str, *, headers=None, timeout=None, **kwargs) -> Any:
        self.calls.append({"method": method, "url": url, "headers": headers or {}, "kwargs": kwargs})
        if self.responder is not None:
            return self.responder(method, url, kwargs)
        if self.queue:
            return self.queue.pop(0)
        return {}

    def close(self) -> None:
        pass


@pytest.fixture
def transport() -> RecordingTransport:
    return RecordingTransport()


@pytest.fixture
def client(transport: RecordingTransport) -> MantisClient:
    config = ConfigurationManager()
    config.internal_user_id = "11111111-1111-1111-1111-111111111111"
    c = MantisClient("/api/proxy/", cookie=None, config=config)
    # swap in the recording transport so nothing touches the network.
    c.http.transport = transport
    return c
