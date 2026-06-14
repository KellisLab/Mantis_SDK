"""internal http client: url building + auth, layered on top of Transport.
resource groups and the rich handles all route their rest calls through here."""
from __future__ import annotations

import logging
import time
from typing import Any

from .config import ConfigurationManager
from .transport import Transport

logger = logging.getLogger("mantis_sdk")


class HttpClient:
    """builds fully-qualified urls, attaches auth headers, and delegates to Transport.

    auth resolution (either or both may apply):
      - cookie: a browser session cookie string (canonical for user auth).
      - config.internal_user_id: enables X-Internal-Service backend-to-backend auth.
    """

    def __init__(
        self,
        base_url: str,
        cookie: str | None,
        config: ConfigurationManager,
        transport: Transport | None = None,
    ):
        self.base_url = base_url.strip("/")
        self.cookie = cookie
        self.config = config
        self.transport = transport or Transport(default_timeout=config.request_timeout)

    @staticmethod
    def _trim(segment: str) -> str:
        return segment.strip("/")

    def build_url(self, endpoint: str, *, rm_slash: bool = False) -> str:
        """join host + base_url + endpoint into a normalized url.
        a trailing slash is added by default (django APPEND_SLASH + the proxy preserve it);
        a few endpoints reject the trailing slash, so rm_slash strips it."""
        parts = [self.config.host.rstrip("/")]
        if self.base_url:
            parts.append(self._trim(self.base_url))
        parts.append(self._trim(endpoint))
        url = "/".join(parts)
        if not rm_slash:
            url += "/"
        return url

    def auth_headers(self) -> dict[str, str]:
        """build auth headers from the cookie and/or internal-service config."""
        headers: dict[str, str] = {}
        if self.cookie:
            headers["cookie"] = self.cookie
        if self.config.internal_user_id:
            headers["X-Internal-Service"] = "true"
            headers["X-Internal-User-Id"] = str(self.config.internal_user_id)
        return headers

    def request(
        self,
        method: str,
        endpoint: str,
        *,
        rm_slash: bool = False,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> Any:
        """make an authenticated request to a backend endpoint and return parsed json."""
        url = self.build_url(endpoint, rm_slash=rm_slash)

        merged = self.auth_headers()
        if headers:
            merged.update(headers)

        if method.upper() == "GET":
            # prevent any intermediary caching of GET reads.
            merged.setdefault("Cache-Control", "no-cache")
            params = kwargs.get("params", {}) or {}
            params["_ts"] = str(time.time())
            kwargs["params"] = params

        return self.transport.request(method, url, headers=merged, timeout=timeout, **kwargs)

    def close(self) -> None:
        self.transport.close()
