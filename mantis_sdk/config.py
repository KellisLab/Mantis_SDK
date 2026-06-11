"""configuration for the mantis sdk: hosts, auth, timeouts, and browser render args."""
from __future__ import annotations

import os

from .render_args import RenderArgs


class ConfigurationManager:
    """holds connection + rendering settings.
    values fall back to MANTIS_* env vars, then sensible localhost defaults."""

    def __init__(self) -> None:
        # http host the rest client talks to (often a next.js proxy origin).
        self.host = os.getenv("MANTIS_HOST", "http://localhost:3000")
        # django backend origin, used to derive the websocket url.
        self.backend_host = os.getenv("MANTIS_BACKEND_HOST", "http://localhost:8000")
        # cookie domain used when seeding the playwright browser context.
        self.domain = os.getenv("MANTIS_DOMAIN", "localhost")
        # request/navigation timeout in milliseconds (playwright + page waits).
        self.timeout = int(os.getenv("MANTIS_TIMEOUT", "60000"))
        # default http request timeout in seconds for the rest transport.
        self.request_timeout = float(os.getenv("MANTIS_REQUEST_TIMEOUT", "60"))

        # browser-side flag the sdk waits on before a space is considered ready.
        self.wait_for = os.getenv("MANTIS_WAIT_FOR", "isLoaded")

        # internal-service auth: set these to authenticate backend-to-backend without
        # a session cookie. when internal_user_id is set the transport sends
        # X-Internal-Service: true and X-Internal-User-Id headers.
        self.internal_user_id: str | None = os.getenv("MANTIS_INTERNAL_USER_ID")

        self.render_args = RenderArgs(
            {
                "headless": True,
                "viewport": {"width": 1920, "height": 1080},
            }
        )

    def update(self, config_dict: dict) -> ConfigurationManager:
        """update multiple configuration values at once; returns self for chaining."""
        for key, value in config_dict.items():
            if hasattr(self, key):
                setattr(self, key, value)
        return self
