"""transport maps http status codes to typed exceptions and retries idempotent calls."""
import logging
from unittest.mock import MagicMock

import pytest
import requests

from mantis_sdk.exceptions import (
    APIConnectionError,
    APIStatusError,
    AuthenticationError,
    NotFoundError,
    RateLimitError,
)
from mantis_sdk.transport import Transport, _redact


def _response(status: int, json_body=None, text="", content_type="application/json"):
    r = MagicMock(spec=requests.Response)
    r.status_code = status
    r.headers = {"Content-Type": content_type}
    r.text = text or ("" if json_body is None else "{}")
    r.reason = "reason"
    r.json.return_value = json_body
    return r


@pytest.mark.parametrize(
    "status,exc",
    [(401, AuthenticationError), (403, AuthenticationError),
     (404, NotFoundError), (429, RateLimitError), (500, APIStatusError)],
)
def test_status_maps_to_typed_exception(status, exc):
    t = Transport()
    t.session.request = MagicMock(return_value=_response(status, {"error": "boom"}))
    with pytest.raises(exc) as ei:
        t.request("GET", "http://x/y")
    assert ei.value.status_code == status
    assert ei.value.body == {"error": "boom"}


def test_2xx_parses_json():
    t = Transport()
    t.session.request = MagicMock(return_value=_response(200, {"ok": True}))
    assert t.request("GET", "http://x/y") == {"ok": True}


def test_connection_error_wrapped():
    t = Transport()
    t.session.request = MagicMock(side_effect=requests.exceptions.ConnectionError("refused"))
    with pytest.raises(APIConnectionError):
        t.request("GET", "http://x/y")


def test_redact_masks_auth_headers():
    redacted = _redact({
        "cookie": "secret",
        "X-Internal-Service-Token": "service-secret",
        "X-Internal-User-Id": "u",
        "Accept": "json",
    })
    assert redacted["cookie"] == "<redacted>"
    assert redacted["X-Internal-Service-Token"] == "<redacted>"
    assert redacted["X-Internal-User-Id"] == "<redacted>"
    assert redacted["Accept"] == "json"


def test_debug_log_redacts_internal_service_token(caplog):
    transport = Transport()
    transport.session.request = MagicMock(return_value=_response(200, {"ok": True}))
    caplog.set_level(logging.DEBUG, logger="mantis_sdk")

    transport.request(
        "GET",
        "http://x/y",
        headers={"X-Internal-Service-Token": "service-secret"},
    )

    assert "service-secret" not in caplog.text
    assert "<redacted>" in caplog.text
