"""url building, trailing-slash handling, and auth header construction."""
from mantis_sdk import ConfigurationManager
from mantis_sdk._http import HttpClient


def _http(base, cookie=None, internal=None):
    cfg = ConfigurationManager()
    cfg.host = "http://localhost:3000"
    cfg.internal_user_id = internal
    return HttpClient(base, cookie, cfg)


def test_proxy_url_has_trailing_slash():
    h = _http("/api/proxy/")
    assert h.build_url("synthesis/landscape") == "http://localhost:3000/api/proxy/synthesis/landscape/"


def test_rm_slash_strips_trailing():
    h = _http("/api/proxy/")
    assert h.build_url("a/b", rm_slash=True) == "http://localhost:3000/api/proxy/a/b"


def test_empty_base_url_direct_host():
    h = _http("")
    assert h.build_url("api/getSpaces") == "http://localhost:3000/api/getSpaces/"


def test_cookie_auth_header():
    h = _http("/api/proxy/", cookie="sessionid=abc")
    assert h.auth_headers() == {"cookie": "sessionid=abc"}


def test_internal_service_auth_header():
    h = _http("/api/proxy/", internal="user-123")
    headers = h.auth_headers()
    assert headers["X-Internal-Service"] == "true"
    assert headers["X-Internal-User-Id"] == "user-123"
