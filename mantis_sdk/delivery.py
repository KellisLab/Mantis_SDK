"""Explicit frozen-context turns with ordered, acknowledged event delivery."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import math
import time
import uuid
from collections import OrderedDict
from collections.abc import AsyncIterator
from contextlib import aclosing
from typing import Any
from urllib.parse import quote, urlencode

from .agents import AgentEvent, AgentResult, AgentSession, AgentsResource, _context_uuid, _text
from .enums import Provider
from .exceptions import AgentRunError, ConfigurationError, ProviderUnavailableError

_EFFORTS = frozenset({"low", "high", "max"})
_MAX_REPLAY_GAP = 2048


def _uuid(value: str | None, name: str) -> str:
    return str(uuid.uuid4()) if value is None else _context_uuid(value, name)


def _is_cancellation(data: dict) -> bool:
    return data.get("type") == "run_cancelled" or (
        data.get("type") == "chat_fail" and str(data.get("reason") or "").lower() in {"cancelled", "canceled"}
    )


def _fingerprint(data: dict) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


class DeliverySession(AgentSession):
    """A Cartographer chat with stable request identity and replay-safe delivery.

    ``ask`` prepares frozen context before sending. ``resume`` resends the exact
    previous request after an interrupted stream. Keep this object to retain its
    in-memory text, event and ACK state across reconnects.
    """

    def __init__(self, resource: AgentsResource, *, user_email: str, provider: Provider,
                 space_id: str | None, chat_id: str, timeout: float,
                 model_id: str, space_state_id: str | None = None, reasoning_effort: str = "high"):
        self.model_id = model_id
        self._resource = resource
        self.user_email = user_email
        self.provider = provider
        self.space_id = space_id
        self.space_state_id = space_state_id
        self.chat_id = chat_id
        self.server_chat_id = chat_id
        self._delivery_session_id = str(uuid.uuid4())
        self.timeout = timeout
        self.reasoning_effort = reasoning_effort
        self._message_id: str | None = None
        self._runtime_run_id: str | None = None
        self._context_snapshot: dict | None = None
        self._ws: Any = None
        self._events: list[AgentEvent] = []
        self._segments: dict[str, str] = {}
        self._payload: dict | None = None
        self._terminal: AgentEvent | None = None
        self._streaming = False
        self._preparing = False
        self._watermark = 0
        self._pending_frames: dict[int, dict] = {}
        self._delivery_ids: OrderedDict[str, int] = OrderedDict()
        self._seen_sequences: OrderedDict[int, str] = OrderedDict()
        self._used_message_ids: set[str] = set()
        self._accepted = False
        self._before_accept: list[dict] = []

    @property
    def message_id(self) -> str | None:
        return self._message_id

    @property
    def runtime_run_id(self) -> str | None:
        return self._runtime_run_id

    @property
    def context_snapshot(self) -> dict | None:
        return dict(self._context_snapshot) if self._context_snapshot is not None else None

    @property
    def delivery_session_id(self) -> str:
        return self._delivery_session_id

    def _ws_url(self) -> str:
        cfg = self._resource.http.config
        host = cfg.backend_host or cfg.host
        ws_base = host.replace("https://", "wss://").replace("http://", "ws://").rstrip("/")
        model_id = self._payload["model_id"] if self._payload is not None else self.model_id
        query = {"model_id": model_id, "runtime_delivery_v": "1",
                 "runtime_delivery_session_id": self.delivery_session_id}
        if self.space_id:
            query["space_id"] = self.space_id
        if self.space_state_id:
            query["space_state_id"] = self.space_state_id
        return f"{ws_base}/ws/chat/{self.chat_id}/{quote(self.user_email, safe='')}/default/?{urlencode(query)}"

    async def _connect(self) -> None:
        import inspect

        import websockets

        cookie = self._resource.http.cookie
        if not cookie:
            raise ConfigurationError("Composer sessions require a signed-in session cookie; internal-service headers do not authenticate this WebSocket")
        # Select the supported keyword before connecting, without retrying an
        # unrelated TypeError as a second handshake.
        header_arg = "additional_headers" if "additional_headers" in inspect.signature(websockets.connect).parameters else "extra_headers"
        options: dict[str, Any] = {header_arg: {"Cookie": cookie}, "max_size": None, "open_timeout": self.timeout}
        self._ws = await websockets.connect(self._ws_url(), **options)

    async def __aenter__(self) -> DeliverySession:
        if self._ws is None:
            await self._connect()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    async def close(self) -> None:
        """Close the transport. Closing a socket does not cancel a server turn."""
        if self._ws is not None:
            await self._ws.close()
            self._ws = None

    async def ask(self, message: str, *, active_map_id: str | None = None,
                  bag_ids: list[str] | None = None, cluster_ids: list[str] | None = None,
                  point_ids: list[str] | None = None, space_ids: list[str] | None = None,
                  files: list[dict] | None = None, source_snapshot_ids: list[str] | None = None,
                  open_notebook_path: str | None = None, context_snapshot: dict | None = None,
                  message_id: str | None = None, reasoning_effort: str | None = None,
                  generate_suggestions: bool = False) -> AsyncIterator[AgentEvent]:
        """Start one turn and yield events until an authoritative terminal.

        Reuse ``resume()`` after an interruption, preserving the original UUID,
        model, effort and snapshot. A final text segment alone never ends a turn.
        """
        if self._ws is None:
            raise ConfigurationError("DeliverySession must be used as an async context manager")
        if self._preparing or self._streaming or (self._payload is not None and self._terminal is None):
            raise AgentRunError("The previous turn is unfinished; resume it or cancel it before asking again")
        if not isinstance(message, str) or not message.strip():
            raise ConfigurationError("message must be nonempty text")
        effort = reasoning_effort if reasoning_effort is not None else self.reasoning_effort
        if not isinstance(effort, str) or effort not in _EFFORTS:
            raise ConfigurationError("reasoning_effort must be low, high, or max")
        next_message_id = _uuid(message_id, "message_id")
        if next_message_id in self._used_message_ids:
            raise ConfigurationError("message_id was already used; resume the existing turn or use a new UUID")
        if context_snapshot is not None:
            if any(value is not None for value in (active_map_id, bag_ids, cluster_ids, point_ids, space_ids,
                                                  files, source_snapshot_ids, open_notebook_path)):
                raise ConfigurationError("Pass context_snapshot without live source selections")
            if (not isinstance(context_snapshot, dict) or type(context_snapshot.get("version")) is not int
                    or context_snapshot["version"] != 1 or not context_snapshot.get("id")):
                raise ConfigurationError("context_snapshot must contain version=1 and a UUID id")
            snapshot_id = _uuid(context_snapshot["id"], "context_snapshot.id")
        else:
            self._preparing = True
            try:
                sources = copy.deepcopy({"active_map_id": active_map_id, "bag_ids": bag_ids,
                                         "cluster_ids": cluster_ids, "point_ids": point_ids,
                                         "space_ids": space_ids, "files": files,
                                         "source_snapshot_ids": source_snapshot_ids,
                                         "open_notebook_path": open_notebook_path})
                snapshot = await asyncio.to_thread(self.prepare_context, **sources)
            finally:
                self._preparing = False
            snapshot_id = snapshot["id"]
        self._message_id = next_message_id
        self._used_message_ids.add(next_message_id)
        self._runtime_run_id = None
        self._context_snapshot = {"version": 1, "id": snapshot_id}
        self._events = []
        self._segments = {}
        self._terminal = None
        self._watermark = 0
        self._pending_frames = {}
        self._delivery_ids.clear()
        self._seen_sequences.clear()
        self._accepted = False
        self._before_accept = []
        self._payload = {"message": message, "message_id": self.message_id, "runtime": self.provider.value,
                         "model_id": self.model_id, "reasoning_effort": effort,
                         "context_snapshot": dict(self.context_snapshot), "generate_suggestions": generate_suggestions}
        async with aclosing(self._stream()) as events:
            async for event in events:
                yield event

    async def resume(self) -> AsyncIterator[AgentEvent]:
        """Resume the latest turn with its exact saved request and ACK cursor.

        Reconnect by entering this same session object again after ``close()``.
        Already applied events remain deduplicated even if an ACK was lost.
        """
        if self._payload is None:
            raise AgentRunError("There is no prepared turn to resume")
        if self._terminal is not None and self.runtime_run_id is None:
            raise AgentRunError("This request was rejected before admission; ask again with a new message ID")
        async with aclosing(self._stream()) as events:
            async for event in events:
                yield event

    async def cancel(self) -> None:
        """Request cancellation; continue consuming until ``cancelled`` or another terminal."""
        if self._ws is None or not self.message_id or self._terminal is not None:
            raise AgentRunError("There is no connected active turn to cancel")
        payload = {"type": "cancel_run", "message_id": self.message_id}
        if self.runtime_run_id:
            payload["runtime_run_id"] = self.runtime_run_id
        await self._ws.send(json.dumps(payload))

    async def _stream(self) -> AsyncIterator[AgentEvent]:
        from websockets.exceptions import ConnectionClosed

        if self._streaming:
            raise AgentRunError("Only one consumer may stream a session at a time")
        if self._ws is None:
            raise ConfigurationError("Reconnect by entering this DeliverySession before resuming")
        self._streaming = True
        try:
            await self._ws.send(json.dumps(self._payload))
            repairing_ack = self._terminal is not None
            deadline = time.monotonic() + self.timeout
            while self._terminal is None or repairing_ack:
                try:
                    raw = await asyncio.wait_for(self._ws.recv(), timeout=max(0, deadline - time.monotonic()))
                except asyncio.TimeoutError as exc:
                    if repairing_ack:
                        raise AgentRunError("Composer became idle before acknowledgment synchronization; reconnect and resume this session.") from exc
                    raise AgentRunError("Composer became idle before an authoritative terminal; the turn may still be running. Use resume() with this session.") from exc
                try:
                    data = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                if not isinstance(data, dict):
                    continue
                if data.get("type") == "error":
                    raise AgentRunError(_text(data.get("message") or data.get("code") or "Composer connection failed"))
                if data.get("message_id") != self.message_id:
                    continue
                if data.get("chat_id") and data["chat_id"] != self.chat_id:
                    continue
                deadline = time.monotonic() + self.timeout
                if repairing_ack and data.get("type") not in {"run.accepted", "run.rejected", "runtime_state"}:
                    continue
                for event in await self._receive(data):
                    yield event
                if (repairing_ack and data.get("type") == "runtime_state"
                        and data.get("delivery_session_id") == self.delivery_session_id):
                    # A matching, validated handshake proves this connection
                    # has claimed the retained cursor before its ACK is resent.
                    await self._ack()
                    return
        except ConnectionClosed as exc:
            raise AgentRunError("Composer disconnected during delivery; reconnect this session and call resume()") from exc
        finally:
            self._streaming = False

    def _adopt_run(self, data: dict) -> None:
        run_id = data.get("runtime_run_id")
        if not isinstance(run_id, str) or not run_id:
            raise AgentRunError("Composer omitted runtime_run_id")
        try:
            run_id = str(uuid.UUID(run_id))
        except ValueError as exc:
            raise AgentRunError("Composer supplied an invalid runtime_run_id") from exc
        if self.runtime_run_id and run_id != self.runtime_run_id:
            raise AgentRunError("Composer changed runtime_run_id for the same message")
        provider = data.get("provider") or data.get("runtime")
        if provider and provider != Provider.Cartographer.value:
            raise ProviderUnavailableError(f"Requested Cartographer but the run reported {provider!r}")
        self._runtime_run_id = run_id

    def _apply(self, data: dict) -> AgentEvent:
        event = AgentEvent.from_wire(copy.deepcopy(data), text_updates=True)
        if _is_cancellation(data):
            event.type = "cancelled"
        self._events.append(event)
        if event.type in {"text", "text_update"}:
            if event.item_id:
                self._segments[event.item_id] = event.text
            else:
                self._segments[f"segment-{len(self._events)}"] = event.text
        if event.is_terminal:
            self._terminal = event
        return copy.deepcopy(event)

    async def _ack(self) -> None:
        await self._ws.send(json.dumps({"type": "runtime_delivery_ack", "runtime_run_id": self.runtime_run_id,
                                       "delivery_session_id": self.delivery_session_id,
                                       "through_sequence": self._watermark}))

    async def _receive(self, data: dict) -> list[AgentEvent]:
        wire = data.get("type")
        if wire == "run.rejected":
            if self.runtime_run_id is not None:
                raise AgentRunError("Composer rejected resuming an admitted turn: "
                                    + _text(data.get("message") or data.get("reason")))
            return [self._apply(data)]
        if wire == "run.accepted":
            snapshot = data.get("context_snapshot")
            if (not isinstance(snapshot, dict) or type(snapshot.get("version")) is not int
                    or snapshot != self.context_snapshot):
                raise AgentRunError("Composer accepted a different context snapshot")
            self._adopt_run(data)
            if self._accepted:
                return []
            self._accepted = True
            events = [self._apply(data)]
            buffered, self._before_accept = self._before_accept, []
            for frame in buffered:
                events.extend(await self._receive(frame))
                if self._terminal is not None:
                    break
            return events
        if not self._accepted:
            if wire == "runtime_state":
                if data.get("delivery_session_id") != self.delivery_session_id:
                    return []
            elif (type(data.get("delivery_sequence")) is not int or data["delivery_sequence"] <= 0
                    or not isinstance(data.get("delivery_id"), str) or not data["delivery_id"]):
                return []
            if len(self._before_accept) >= _MAX_REPLAY_GAP:
                raise AgentRunError("Composer exceeded the pre-acceptance buffer; reconnect and resume")
            self._before_accept.append(data)
            return []
        if wire == "runtime_state":
            if data.get("delivery_session_id") != self.delivery_session_id:
                return []
            self._adopt_run(data)
            floor = data.get("delivery_ack_floor")
            if type(floor) is not int or floor < 0 or floor > self._watermark:
                raise AgentRunError("The server ACK floor exceeds this session's applied state; resume with the original session object")
            # A handshake never substitutes for unapplied replay frames.
            return [] if self._terminal is not None else [self._apply(data)]
        sequence = data.get("delivery_sequence")
        delivery_id = data.get("delivery_id")
        # Historical and unsequenced frames cannot prove progress or completion.
        if type(sequence) is not int or sequence <= 0 or not isinstance(delivery_id, str) or not delivery_id:
            return []
        self._adopt_run(data)
        if sequence <= self._watermark:
            if sequence in self._seen_sequences and self._seen_sequences[sequence] != _fingerprint(data):
                raise AgentRunError("Composer changed a previously delivered sequence")
            await self._ack()
            return []
        discarded = data.get("discard_through_sequence")
        if _is_cancellation(data) and discarded is not None:
            if type(discarded) is not int or discarded != sequence - 1:
                raise AgentRunError("Composer supplied an invalid cancellation discard marker")
            self._watermark = discarded
            self._pending_frames = {key: frame for key, frame in self._pending_frames.items() if key > discarded}
        if sequence > self._watermark + _MAX_REPLAY_GAP or len(self._pending_frames) >= _MAX_REPLAY_GAP:
            raise AgentRunError("Composer delivery gap exceeded the replay window; reconnect and resume")
        previous = self._pending_frames.get(sequence)
        if previous is not None and previous != data:
            raise AgentRunError("Composer changed a previously delivered sequence")
        self._pending_frames[sequence] = data
        events = []
        while self._watermark + 1 in self._pending_frames:
            frame = self._pending_frames.pop(self._watermark + 1)
            identity = frame["delivery_id"]
            if identity in self._delivery_ids:
                raise AgentRunError("Composer reused a delivery identity for a different sequence")
            events.append(self._apply(frame))
            self._delivery_ids[identity] = frame["delivery_sequence"]
            self._seen_sequences[frame["delivery_sequence"]] = _fingerprint(frame)
            if len(self._delivery_ids) > _MAX_REPLAY_GAP:
                self._delivery_ids.popitem(last=False)
                self._seen_sequences.popitem(last=False)
            self._watermark += 1
            if self._terminal is not None:
                break
        # State is applied before acknowledgement. Lost ACKs are replay-safe.
        await self._ack()
        return events

    def result(self) -> AgentResult:
        """Return the latest turn, with ``completed=False`` until server confirmation."""
        terminal = self._terminal
        failed = bool(terminal and terminal.type in {"fail", "rejected"})
        return AgentResult(text="".join(self._segments.values()), provider=self.provider.value,
                           events=copy.deepcopy(self._events), failed=failed, error=terminal.text if terminal and failed else None,
                           completed=bool(terminal and terminal.type == "complete"),
                           cancelled=bool(terminal and terminal.type == "cancelled"),
                           message_id=self.message_id, runtime_run_id=self.runtime_run_id)


def create_delivery_session(resource: AgentsResource, *, model_id: str, space_id: str | None,
                            user_email: str | None, chat_id: str | None, timeout: float,
                            check_capability: bool, space_state_id: str | None,
                            auto_space_state: bool, reasoning_effort: str) -> DeliverySession:
    if not isinstance(model_id, str) or not model_id.strip():
        raise ConfigurationError("model_id must name a model supported by the deployment")
    if not isinstance(reasoning_effort, str) or reasoning_effort not in _EFFORTS:
        raise ConfigurationError("reasoning_effort must be low, high, or max")
    if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout) or timeout <= 0):
        raise ConfigurationError("timeout must be positive and finite")
    user_email = user_email or getattr(resource.http.config, "user_email", None)
    if not isinstance(user_email, str) or not user_email.strip():
        raise ConfigurationError("delivery_session requires user_email matching the signed-in session cookie")
    canonical_chat_id = _uuid(None if chat_id == "new" else chat_id, "chat_id")
    canonical_space_id = _context_uuid(space_id, "space_id") if space_id is not None else None
    if space_state_id is not None and canonical_space_id is None:
        raise ConfigurationError("space_state_id requires its matching space_id")
    canonical_state_id = _context_uuid(space_state_id, "space_state_id") if space_state_id is not None else None
    if canonical_space_id and canonical_state_id is None and not auto_space_state:
        raise ConfigurationError("Supply space_state_id when disabling auto_space_state for a scoped session")
    if check_capability:
        resource._assert_available(user_email, Provider.Cartographer)
    if canonical_space_id and canonical_state_id is None:
        canonical_state_id = _context_uuid(resource._client.space_states.create(canonical_space_id, name="SDK agent"),
                                           "space_state_id")
    return DeliverySession(resource, user_email=user_email, provider=Provider.Cartographer,
                           model_id=model_id, space_id=canonical_space_id, chat_id=canonical_chat_id,
                           timeout=timeout, space_state_id=canonical_state_id, reasoning_effort=reasoning_effort)
