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
from typing import TYPE_CHECKING, Any, TypedDict
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
_CONTEXT_SNAPSHOT_ENDPOINT = "/api/orchestration/composer/context-snapshots"


def _context_uuid(value: Any, name: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ConfigurationError(f"{name} must be a UUID for context preparation") from exc


def _context_ids(values: list[str] | None, name: str) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, list):
        raise ConfigurationError(f"{name} must be a list of UUIDs")
    return [_context_uuid(value, name) for value in values]


class _EventFields(TypedDict):
    provider: str | None
    raw: dict
    item_id: str | None
    payload: dict
    metadata: dict


def _text(value: Any) -> str:
    if value is None:
        return ""
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


@dataclass
class AgentEvent:
    """A normalized event; ``raw`` preserves the complete server envelope.

    Native plan, compaction, subagent and context events retain structured
    payloads. Tool progress is provisional; only ``tool_result`` is final.
    With ``from_wire(..., text_updates=True)``, ``text_update`` replaces the
    current text for ``item_id`` and ``text`` commits that segment. Historical
    partial frames remain ``typing`` by default to avoid double-counting text.
    """

    type: str
    text: str = ""
    tool_name: str | None = None
    provider: str | None = None
    raw: dict = field(default_factory=dict)
    item_id: str | None = None
    payload: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)

    @classmethod
    def from_wire(cls, data: dict, *, text_updates: bool = False) -> AgentEvent:
        wire = data.get("event") or data.get("type", "")
        payload, metadata = data.get("payload"), data.get("metadata")
        common: _EventFields = {
            "provider": data.get("provider") or data.get("runtime"),
            "raw": data,
            "item_id": data.get("id") or data.get("tool_call_id") or data.get("tool_use_id"),
            "payload": payload if isinstance(payload, dict) else {},
            "metadata": metadata if isinstance(metadata, dict) else {},
        }
        # the agent runtime streams assistant text as untyped {sender:"ai", message, partial}
        # frames (no "type" key). surface only the FINAL frame (partial=false) as text so we
        # don't double-count the streaming snapshots.
        if not wire and data.get("sender") == "ai" and "message" in data:
            if data.get("partial"):
                if text_updates:
                    return cls("text_update", text=_text(data["message"]), **common)
                return cls("typing", **common)
            return cls("text", text=data.get("message", "") or "", **common)
        if wire in _TEXT_TYPES:
            return cls("text", text=data.get("content", "") or "", **common)
        if wire == "tool_call":
            return cls("tool_update" if data.get("metadata_update") else "tool_use",
                       tool_name=data.get("tool_name") or data.get("name"), **common)
        if wire == "tool_output":
            return cls("tool_result", text=_text(data.get("output", data.get("content"))),
                       tool_name=data.get("tool_name"), **common)
        if wire == "tool_progress":
            return cls("tool_progress", text=_text(data.get("output_delta")),
                       tool_name=data.get("tool_name"), **common)
        if wire in {"plan_update", "context_compaction", "subagent_update", "context_tokens"}:
            if wire == "context_tokens":
                common["payload"] = {**common["payload"], **{
                    key: data[key] for key in ("current_tokens", "max_tokens") if key in data
                }}
            return cls(wire, **common)
        if wire == "tool_use":
            return cls("tool_use", tool_name=data.get("tool_name") or data.get("name"),
                       **common)
        if wire == "tool_result":
            return cls("tool_result", text=str(data.get("content", "") or ""),
                       tool_name=data.get("tool_name"), **common)
        if wire == "thinking":
            return cls("thinking", text=data.get("content", "") or "", **common)
        if wire in {"agent_session_init", "runtime_state"}:
            return cls("init", **common)
        if wire in ("typing_indicator", "heartbeat") or (not wire and "typing" in data):
            # heartbeats keep the socket alive during a long claude_code/opencode run.
            return cls("typing", **common)
        if wire == "run.accepted":
            return cls("accepted", **common)
        if wire == "run.rejected":
            return cls("rejected", text=_text(data.get("message") or data.get("reason")), **common)
        if wire == "chat_complete":
            return cls("complete", text=data.get("content", "") or "", **common)
        if wire == "chat_fail":
            return cls("fail", text=_text(data.get("content") or data.get("message") or data.get("reason")),
                       **common)
        if wire == "run_cancelled":
            return cls("cancelled", **common)
        return cls("other", **common)

    @property
    def is_terminal(self) -> bool:
        return self.type in {"complete", "fail", "cancelled", "rejected"}


@dataclass
class AgentResult:
    """The assembled text and events, with optional delivery-aware outcome fields.

    Legacy sessions populate ``failed`` and ``error``. Delivery-aware callers
    can additionally supply confirmed terminal flags and stable run identities.
    """

    text: str
    provider: str
    events: list[AgentEvent]
    failed: bool = False
    error: str | None = None
    completed: bool = False
    cancelled: bool = False
    message_id: str | None = None
    runtime_run_id: str | None = None


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
                 space_id: str | None, chat_id: str, timeout: float,
                 all_spaces: bool = False, mode: str | None = None,
                 space_state_id: str | None = None):
        self._resource = resource
        self.user_email = user_email
        self.provider = Provider(provider)
        self.space_id = space_id
        # the composer only sets the agent's X-Space-State-ID (which its MCP tools need to know
        # which space/map to act on) when ws/chat is given a space_state_id. without it the agent
        # can talk but can't inspect the space.
        self.space_state_id = space_state_id
        self.chat_id = chat_id
        self.timeout = timeout
        # all_spaces lets one agent reason across every accessible map at once (the backend's
        # initialize_all_spaces_agent path) — not possible in the single-space UI.
        self.all_spaces = all_spaces
        self.mode = mode
        self._ws = None
        self._events: list[AgentEvent] = []
        self.server_chat_id: str | None = None

    def _ws_url(self) -> str:
        cfg = self._resource.http.config
        host = cfg.backend_host or cfg.host
        ws_base = host.replace("https://", "wss://").replace("http://", "ws://").rstrip("/")
        url = f"{ws_base}/ws/chat/{self.chat_id}/{quote(self.user_email, safe='')}/default/?model_id={self.provider.value}"
        if self.space_id:
            url += f"&space_id={self.space_id}"
        if self.space_state_id:
            url += f"&space_state_id={self.space_state_id}"
        return url

    async def __aenter__(self) -> AgentSession:
        import websockets

        cookie = self._resource.http.cookie
        headers = {"Cookie": cookie} if cookie else None
        # the header kwarg was renamed extra_headers → additional_headers in websockets 14.
        # try the new name, fall back to the old so we work across the pinned range (>=10.4).
        try:
            self._ws = await websockets.connect(
                self._ws_url(), additional_headers=headers, max_size=None, open_timeout=self.timeout,
            )
        except TypeError:
            self._ws = await websockets.connect(
                self._ws_url(), extra_headers=headers, max_size=None, open_timeout=self.timeout,
            )
        # initialize the composer agent when an explicit mode or all_spaces is requested.
        # the backend routes all_spaces_mode → initialize_all_spaces_agent (cross-map search).
        if self.all_spaces or self.mode:
            await self._initialize()
        return self

    async def _initialize(self, ack_timeout: float = 30.0) -> None:
        """send agent_initialization; wait (best-effort) for the agent_initialized ack.

        the backend delivers the ack via a channel-layer group broadcast, which may or may not
        loop back to a headless single socket depending on the deployment. so we wait up to
        ack_timeout for it, but if it doesn't arrive we proceed anyway (the backend still
        processed the init) rather than failing the whole run. a `success=false` ack IS fatal."""
        import asyncio

        init: dict[str, Any] = {"type": "agent_initialization", "all_spaces_mode": self.all_spaces}
        if self.mode:
            init["mode"] = self.mode
        await self._ws.send(json.dumps(init))
        deadline = time.monotonic() + ack_timeout
        while time.monotonic() < deadline:
            try:
                raw = await asyncio.wait_for(self._ws.recv(), timeout=deadline - time.monotonic())
            except asyncio.TimeoutError:
                logger.debug("agent_initialized ack not received in %.0fs; proceeding", ack_timeout)
                return
            try:
                data = json.loads(raw)
            except (ValueError, TypeError):
                continue
            if isinstance(data, dict) and data.get("type") == "agent_initialized":
                if not data.get("success", True):
                    raise AgentRunError(f"agent initialization failed: {data.get('error')}")
                return

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    async def close(self) -> None:
        if self._ws is not None:
            import asyncio
            # allow Agno time to persist the session before the WS disconnects
            await asyncio.sleep(2)
            await self._ws.close()
            self._ws = None

    def prepare_context(self, *, active_map_id: str | None = None,
                        bag_ids: list[str] | None = None, cluster_ids: list[str] | None = None,
                        point_ids: list[str] | None = None, space_ids: list[str] | None = None,
                        files: list[dict] | None = None, source_snapshot_ids: list[str] | None = None,
                        open_notebook_path: str | None = None) -> dict:
        """Freeze selected sources through the context snapshot REST API.

        Supply a stable UUID ``chat_id`` when creating the session, or prepare
        context after a run has supplied ``server_chat_id``. No socket is opened
        and no turn is sent. Existing ``ask`` behavior is unchanged.

        With no membership filters, the scoped Space is included by default.
        ``space_ids=[]`` explicitly omits whole-Space sources. Point, collection
        and cluster selections require an active map and a bound Space thread.
        Server authorization determines access to every source.
        """
        scope = {"chat_id": _context_uuid(self.server_chat_id or self.chat_id, "chat_id")}
        if self.space_id:
            scope["space_id"] = _context_uuid(self.space_id, "space_id")
        if self.space_state_id:
            if not self.space_id:
                raise ConfigurationError("space_state_id requires its matching space_id")
            scope["space_state_id"] = _context_uuid(self.space_state_id, "space_state_id")

        bags = _context_ids(bag_ids, "bag_ids")
        clusters = _context_ids(cluster_ids, "cluster_ids")
        points = _context_ids(point_ids, "point_ids")
        selected = bool(bags or clusters or points)
        if selected and not active_map_id:
            raise ConfigurationError("bag_ids, cluster_ids and point_ids require active_map_id")
        body: dict[str, Any] = {"version": 1, "scope": scope}
        body["space_ids"] = (_context_ids(space_ids, "space_ids") if space_ids is not None
                             else [scope["space_id"]] if self.space_id and not selected else [])
        if active_map_id is not None:
            if not self.space_state_id:
                raise ConfigurationError("active_map_id requires a bound space_state_id")
            body["selections"] = [{"map_id": _context_uuid(active_map_id, "active_map_id"),
                                   "bag_ids": bags, "cluster_ids": clusters, "point_ids": points}]
        if files is not None:
            if not isinstance(files, list) or any(not isinstance(spec, dict) for spec in files):
                raise ConfigurationError("files must be a list of attachment objects")
            body["files"] = files
        if source_snapshot_ids is not None:
            body["source_snapshot_ids"] = _context_ids(source_snapshot_ids, "source_snapshot_ids")
        if open_notebook_path is not None:
            if not isinstance(open_notebook_path, str):
                raise ConfigurationError("open_notebook_path must be a string")
            body["open_notebook_path"] = open_notebook_path

        result = self._resource.http.request("POST", _CONTEXT_SNAPSHOT_ENDPOINT, json=body)
        if not isinstance(result, dict) or type(result.get("version")) is not int or result["version"] != 1:
            raise AgentRunError("Context preparation returned an invalid version 1 snapshot")
        try:
            snapshot_id = _context_uuid(result.get("id"), "context_snapshot.id")
        except ConfigurationError as exc:
            raise AgentRunError("Context preparation returned an invalid snapshot UUID") from exc
        return {**result, "id": snapshot_id}

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

        # idle timeout: a long claude_code/opencode run can take minutes, but the backend sends
        # heartbeats, so we bound on SILENCE (no event for `self.timeout`s), not total duration.
        # final-answer grace: the backend sends the committed final text (a non-partial ai frame)
        # and *then* a chat_complete envelope — but that envelope is delivered over a kafka→socket
        # bridge that occasionally drops it, leaving us blocked on recv() for the full idle
        # timeout AFTER the answer already arrived. so once we've seen the final text, we wait
        # only `final_grace`s for a terminal event, then finish cleanly.
        saw_final_text = False
        final_grace = getattr(self, "final_grace", 8.0)
        while True:
            wait = final_grace if saw_final_text else self.timeout
            try:
                raw = await asyncio.wait_for(self._ws.recv(), timeout=wait)
            except asyncio.TimeoutError as exc:
                if saw_final_text:
                    return  # answer already delivered; the terminal envelope just didn't arrive
                raise AgentRunError(
                    f"agent run idle for {self.timeout}s with no event (backend sends heartbeats; "
                    f"a longer idle timeout or a stuck run?)"
                ) from exc

            try:
                data = json.loads(raw)
            except (ValueError, TypeError):
                continue
            if not isinstance(data, dict):
                continue

            # capture the real Agno session_id from the first message that carries it
            if "chat_id" in data and data["chat_id"]:
                self.server_chat_id = data["chat_id"]

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
            # a committed final answer frame ({sender:ai, partial:false}) → start the grace clock.
            if event.type == "text" and data.get("sender") == "ai" and data.get("partial") is False:
                saw_final_text = True

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
                check_capability: bool = True,
                all_spaces: bool = False,
                mode: str | None = None,
                space_state_id: str | None = None,
                auto_space_state: bool = True) -> AgentSession:
        """open a streaming agent session. opencode by default; claude_code is checked up front.

        set all_spaces=True to let one agent reason across every accessible map at once (the
        backend's all-spaces composer; not possible in the single-space UI). `mode` selects a
        composer mode (e.g. "Orchestrator", "Ask") and triggers initialization on connect.

        when scoped to a space_id, the agent's MCP tools need a space-state id to know which
        space/map to act on. by default (auto_space_state=True) we mint one via
        client.space_states.create() — the same cookie-auth endpoint the browser uses — and
        thread it onto the connect. pass space_state_id to reuse an existing one, or
        auto_space_state=False to skip (the agent can talk but not inspect the space).

        user_email is required (identity for capability gating); falls back to config.user_email.
        agents key on email, not user_id."""
        provider = Provider(provider)
        user_email = user_email or getattr(self.http.config, "user_email", None)
        if not user_email:
            raise ConfigurationError(
                "agents.session requires user_email (the agent runtime keys capability + identity "
                "on email, not user_id)."
            )
        if check_capability:
            self._assert_available(user_email, provider)
        if space_id and not space_state_id and not all_spaces and auto_space_state:
            space_state_id = self._client.space_states.create(space_id, name="SDK agent")
        return AgentSession(
            self, user_email=user_email, provider=provider, space_id=space_id,
            chat_id=chat_id or "new", timeout=timeout,
            all_spaces=all_spaces, mode=mode, space_state_id=space_state_id,
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
