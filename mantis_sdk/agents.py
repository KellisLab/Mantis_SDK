"""agent runtime: run claude_code / opencode agents scoped to a space, streaming results.

verified against the live backend (orchestration/composer + agent_execution):
  - transport: websocket ws/chat/<chat_id>/<url-encoded-email>/default/?model_id=<provider>
    (the email-in-path route resolves identity so claude_code capability gating works;
    the bare ws/chat/?space_id= route leaves user_email=None and can't gate claude_code).
  - provider is selected per run via `model_id` (per-message overrides connect-time).
  - opencode is universally available; claude_code requires UserCapabilities.bedrock_enabled.
  - wire events: typing_indicator, chat_messages (assistant text), tool_use, tool_result,
    thinking, agent_session_init, then terminal chat_complete / chat_fail. events echo
    `provider`, so we can assert the run actually used the requested runtime.
  - capability REST: GET /api/agent_execution/providers/?user_email=, POST .../providers/set/.

opencode is the safe default. claude_code is opt-in and capability-checked up front so we
raise ProviderUnavailableError instead of letting the backend silently fall back."""
from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from .enums import Provider
from .exceptions import AgentRunError, ConfigurationError, ProviderUnavailableError

if TYPE_CHECKING:
    from .resources import MantisClientProtocol

logger = logging.getLogger("mantis_sdk")

# terminal wire events that end a run.
_TERMINAL = frozenset({"chat_complete", "chat_fail"})
# wire event types the sdk understands; anything else is passed through as type="other".
_TEXT_TYPES = frozenset({"chat_messages"})


@dataclass
class AgentEvent:
    """a single normalized event from an agent run.

    type is one of: text, tool_use, tool_result, thinking, init, typing, complete, fail, other.
    raw holds the original wire dict for anything not surfaced as a field."""

    type: str
    text: str = ""
    tool_name: str | None = None
    provider: str | None = None
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_wire(cls, data: dict) -> AgentEvent:
        wire = data.get("type", "")
        provider = data.get("provider")
        if wire in _TEXT_TYPES:
            return cls("text", text=data.get("content", "") or "", provider=provider, raw=data)
        if wire == "tool_use":
            return cls("tool_use", tool_name=data.get("tool_name") or data.get("name"),
                       provider=provider, raw=data)
        if wire == "tool_result":
            return cls("tool_result", text=str(data.get("content", "") or ""),
                       tool_name=data.get("tool_name"), provider=provider, raw=data)
        if wire == "thinking":
            return cls("thinking", text=data.get("content", "") or "", provider=provider, raw=data)
        if wire == "agent_session_init":
            return cls("init", provider=provider, raw=data)
        if wire == "typing_indicator":
            return cls("typing", provider=provider, raw=data)
        if wire == "chat_complete":
            return cls("complete", text=data.get("content", "") or "", provider=provider, raw=data)
        if wire == "chat_fail":
            return cls("fail", text=data.get("content", "") or "", provider=provider, raw=data)
        return cls("other", provider=provider, raw=data)

    @property
    def is_terminal(self) -> bool:
        return self.type in ("complete", "fail")


@dataclass
class AgentResult:
    """the assembled outcome of a run: full assistant text + the events that produced it."""

    text: str
    provider: str
    events: list[AgentEvent]
    failed: bool = False
    error: str | None = None


class AgentSession:
    """an async, streaming agent run scoped to a space and a provider.

    usage:
        async with client.agents.session(space_id, provider=Provider.OpenCode,
                                         user_email=email) as run:
            async for ev in run.ask("..."):
                ...
            result = run.result()
    """

    def __init__(self, resource: AgentsResource, *, user_email: str, provider: Provider,
                 space_id: str | None, chat_id: str, timeout: float):
        self._resource = resource
        self.user_email = user_email
        self.provider = Provider(provider)
        self.space_id = space_id
        self.chat_id = chat_id
        self.timeout = timeout
        self._ws = None
        self._events: list[AgentEvent] = []

    def _ws_url(self) -> str:
        cfg = self._resource.http.config
        host = cfg.backend_host or cfg.host
        ws_base = host.replace("https://", "wss://").replace("http://", "ws://").rstrip("/")
        url = f"{ws_base}/ws/chat/{self.chat_id}/{quote(self.user_email, safe='')}/default/?model_id={self.provider.value}"
        if self.space_id:
            url += f"&space_id={self.space_id}"
        return url

    async def __aenter__(self) -> AgentSession:
        import websockets

        cookie = self._resource.http.cookie
        headers = {"Cookie": cookie} if cookie else None
        self._ws = await websockets.connect(
            self._ws_url(), additional_headers=headers, max_size=None, open_timeout=self.timeout
        )
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    async def close(self) -> None:
        if self._ws is not None:
            await self._ws.close()
            self._ws = None

    async def ask(self, message: str, *, active_map_id: str | None = None,
                  bag_ids: list[str] | None = None, cluster_ids: list[str] | None = None,
                  generate_suggestions: bool = False) -> AsyncIterator[AgentEvent]:
        """send a message and yield normalized events until the run terminates.

        model_id is sent on the message so the provider scoping is explicit per run; the
        backend echoes `provider` on events and we assert it matches what we asked for."""
        import asyncio

        if self._ws is None:
            raise ConfigurationError("AgentSession must be used as an async context manager")

        payload: dict[str, Any] = {
            "message": message,
            "model_id": self.provider.value,
            "generate_suggestions": generate_suggestions,
        }
        if active_map_id:
            payload["active_map_id"] = active_map_id
        if bag_ids:
            payload["bagIds"] = bag_ids
        if cluster_ids:
            payload["clusterIds"] = cluster_ids

        await self._ws.send(json.dumps(payload))

        deadline = time.monotonic() + self.timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AgentRunError(f"agent run timed out after {self.timeout}s")
            try:
                raw = await asyncio.wait_for(self._ws.recv(), timeout=remaining)
            except asyncio.TimeoutError as exc:
                raise AgentRunError(f"agent run timed out after {self.timeout}s") from exc

            try:
                data = json.loads(raw)
            except (ValueError, TypeError):
                continue
            if not isinstance(data, dict):
                continue

            event = AgentEvent.from_wire(data)
            # assert the backend didn't silently route to a different runtime.
            if event.provider and event.provider != self.provider.value:
                raise ProviderUnavailableError(
                    f"requested provider {self.provider.value!r} but the run reported "
                    f"{event.provider!r}; the backend may have fallen back."
                )
            self._events.append(event)
            yield event
            if event.is_terminal:
                return

    def result(self) -> AgentResult:
        """assemble the full assistant text + outcome from the events seen so far."""
        text = "".join(e.text for e in self._events if e.type in ("text", "complete"))
        failed = any(e.type == "fail" for e in self._events)
        error = next((e.text for e in self._events if e.type == "fail"), None)
        return AgentResult(text=text, provider=self.provider.value, events=list(self._events),
                           failed=failed, error=error)


class AgentsResource:
    """run agents and manage per-user provider capabilities. exposed as client.agents."""

    DEFAULT_PROVIDER = Provider.OpenCode

    def __init__(self, client: MantisClientProtocol):
        self._client = client
        self.http = client.http

    # --- capabilities (REST) ---
    # routes are mounted under api/ (backend/urls.py: path('api/', include('agent_execution.urls'))),
    # so the proxy-relative path is /api/agent_execution/...
    def providers(self, user_email: str) -> dict:
        """list the providers available to a user. shape: {providers, default, current}."""
        return self.http.request(
            "GET", "/api/agent_execution/providers", params={"user_email": user_email}
        )

    def set_provider(self, user_email: str, provider: Provider | str) -> dict:
        """set the user's default provider (persisted in UserCapabilities)."""
        return self.http.request(
            "POST", "/api/agent_execution/providers/set",
            json={"user_email": user_email, "provider": str(provider)},
        )

    def _assert_available(self, user_email: str, provider: Provider) -> None:
        """raise ProviderUnavailableError up front if the user can't use this provider.
        opencode is always available, so we only check the gated claude_code path."""
        if provider == Provider.OpenCode:
            return
        try:
            info = self.providers(user_email)
        except Exception as exc:  # noqa: BLE001 — treat a failed check as a hard stop, with context
            raise ProviderUnavailableError(
                f"could not verify provider availability for {user_email!r}: {exc}"
            ) from exc
        available = set(info.get("providers", [])) if isinstance(info, dict) else set()
        if provider.value not in available:
            raise ProviderUnavailableError(
                f"provider {provider.value!r} is not available for {user_email!r} "
                f"(available: {sorted(available)}). claude_code requires bedrock_enabled "
                f"on the user's capabilities."
            )

    # --- runs ---
    def session(self, space_id: str | None = None, *,
                provider: Provider | str = DEFAULT_PROVIDER,
                user_email: str | None = None,
                chat_id: str | None = None,
                timeout: float = 180.0,
                check_capability: bool = True) -> AgentSession:
        """open a streaming agent session. opencode by default; claude_code is checked up front.

        user_email is required (identity for capability gating); falls back to MANTIS_USER_EMAIL
        via the config's internal_user_id is NOT valid here — agents key on email, not user_id."""
        provider = Provider(provider)
        user_email = user_email or getattr(self.http.config, "user_email", None)
        if not user_email:
            raise ConfigurationError(
                "agents.session requires user_email (the agent runtime keys capability + identity "
                "on email, not user_id)."
            )
        if check_capability:
            self._assert_available(user_email, provider)
        return AgentSession(
            self, user_email=user_email, provider=provider, space_id=space_id,
            chat_id=chat_id or f"sdk-{uuid.uuid4()}", timeout=timeout,
        )

    def run_sync(self, message: str, space_id: str | None = None, *,
                 provider: Provider | str = DEFAULT_PROVIDER,
                 user_email: str | None = None,
                 timeout: float = 180.0,
                 on_event=None, **ask_kwargs) -> AgentResult:
        """synchronous convenience: run one message to completion and return the result.
        on_event(ev) is called for each streamed event if provided."""
        import asyncio

        async def _run() -> AgentResult:
            async with self.session(space_id, provider=provider, user_email=user_email,
                                    timeout=timeout) as run:
                async for ev in run.ask(message, **ask_kwargs):
                    if on_event is not None:
                        on_event(ev)
                return run.result()

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(_run())
        raise ConfigurationError(
            "run_sync cannot be called from a running event loop; use `async with "
            "client.agents.session(...)` instead."
        )
