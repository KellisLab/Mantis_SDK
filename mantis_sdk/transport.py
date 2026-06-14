"""http transport for the sdk: a persistent requests.Session with retry/backoff,
per-call timeouts, typed-exception mapping, and cookie-redacted debug logging."""
from __future__ import annotations

import logging
import time
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .exceptions import (
    APIConnectionError,
    APIStatusError,
    AuthenticationError,
    NotFoundError,
    RateLimitError,
)

logger = logging.getLogger("mantis_sdk")

# methods safe to retry automatically (no side effects).
_IDEMPOTENT = frozenset({"GET", "HEAD", "OPTIONS"})


def _redact(headers: dict[str, str]) -> dict[str, str]:
    """copy headers with auth-bearing values masked, for safe logging."""
    redacted = dict(headers)
    for key in list(redacted):
        if key.lower() in {"cookie", "x-internal-user-id", "x-notebook-auth", "x-csrftoken"}:
            redacted[key] = "<redacted>"
    return redacted


class Transport:
    """owns a requests.Session and turns raw responses into parsed json or typed errors."""

    def __init__(
        self,
        *,
        default_timeout: float = 60.0,
        max_retries: int = 3,
        backoff_factor: float = 0.5,
    ):
        self.default_timeout = default_timeout
        self.session = requests.Session()

        # retry idempotent calls on transient server/throttle errors with exponential backoff.
        retry = Retry(
            total=max_retries,
            backoff_factor=backoff_factor,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=_IDEMPOTENT,
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> Any:
        """perform a request and return parsed json (or text/None when not json).
        raises a typed MantisError subclass on failure."""
        headers = headers or {}
        timeout = timeout if timeout is not None else self.default_timeout

        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("→ %s %s headers=%s", method, url, _redact(headers))

        started = time.monotonic()
        try:
            response = self.session.request(method, url, headers=headers, timeout=timeout, **kwargs)
        except requests.exceptions.RequestException as exc:
            raise APIConnectionError(f"request to {url} failed: {exc}") from exc

        elapsed = (time.monotonic() - started) * 1000
        logger.debug("← %s %s %d (%.0fms)", method, url, response.status_code, elapsed)

        return self._handle(response, url)

    def _handle(self, response: requests.Response, url: str) -> Any:
        """raise typed errors for non-2xx; otherwise parse the body."""
        status = response.status_code

        if 200 <= status < 300:
            return self._parse(response)

        body = self._parse(response, swallow=True)
        message = f"{status} from {url}: {self._summarize(body, response)}"

        if status in (401, 403):
            raise AuthenticationError(message, status_code=status, body=body, url=url)
        if status == 404:
            raise NotFoundError(message, status_code=status, body=body, url=url)
        if status == 429:
            raise RateLimitError(message, status_code=status, body=body, url=url)
        raise APIStatusError(message, status_code=status, body=body, url=url)

    @staticmethod
    def _parse(response: requests.Response, *, swallow: bool = False) -> Any:
        """parse json, falling back to text. when swallow, never raise."""
        content_type = response.headers.get("Content-Type", "")
        try:
            if "application/json" in content_type:
                return response.json()
            # some endpoints omit the header but still return json; try anyway.
            text = response.text
            if text and text[:1] in "{[":
                try:
                    return response.json()
                except ValueError:
                    return text
            return text
        except Exception:
            if swallow:
                return None
            raise

    @staticmethod
    def _summarize(body: Any, response: requests.Response) -> str:
        """short human-readable error detail for the exception message."""
        if isinstance(body, dict):
            return str(body.get("error") or body.get("detail") or body)[:300]
        if isinstance(body, str) and body:
            return body[:300]
        return response.reason or "<no body>"

    def close(self) -> None:
        self.session.close()
