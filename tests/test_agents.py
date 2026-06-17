"""agent runtime tests — fully mocked, no live websocket / agent infra.

we drive AgentSession against a fake websocket that replays the exact event stream the
live backend produced during recon (typing_indicator → chat_messages → chat_complete /
chat_fail), and stub the capability REST endpoint via the recording transport."""
from __future__ import annotations

import json

import pytest

from mantis_sdk import (
    AgentEvent,
    Provider,
    ProviderUnavailableError,
)
from mantis_sdk.agents import AgentResult, AgentsResource

# --- event normalization (pure, no IO) ---

def test_event_from_wire_text():
    ev = AgentEvent.from_wire({"type": "chat_messages", "content": "hello", "provider": "opencode"})
    assert ev.type == "text" and ev.text == "hello" and ev.provider == "opencode"


def test_event_from_wire_tool_use():
    ev = AgentEvent.from_wire({"type": "tool_use", "tool_name": "search", "provider": "claude_code"})
    assert ev.type == "tool_use" and ev.tool_name == "search"


def test_event_from_wire_terminal():
    assert AgentEvent.from_wire({"type": "chat_complete"}).is_terminal
    assert AgentEvent.from_wire({"type": "chat_fail", "content": "boom"}).is_terminal
    assert not AgentEvent.from_wire({"type": "typing_indicator"}).is_terminal


def test_event_from_wire_unknown_passes_through():
    ev = AgentEvent.from_wire({"type": "some_new_event", "x": 1})
    assert ev.type == "other" and ev.raw["x"] == 1


def test_event_from_wire_ai_final_frame_is_text():
    # the agent runtime streams assistant text as untyped {sender:ai, message, partial}.
    final = AgentEvent.from_wire({"sender": "ai", "message": "done", "partial": False})
    assert final.type == "text" and final.text == "done"


def test_event_from_wire_ai_partial_frame_is_typing():
    # partial snapshots should NOT be counted as text (avoid double-counting).
    partial = AgentEvent.from_wire({"sender": "ai", "message": "do", "partial": True})
    assert partial.type == "typing"


def test_event_from_wire_heartbeat_is_typing():
    assert AgentEvent.from_wire({"type": "heartbeat", "message_id": "x"}).type == "typing"


# --- capability gating (REST via recording transport) ---

def test_opencode_never_calls_capability_check(client, transport):
    # opencode is universal; _assert_available must not hit the network for it.
    client.agents._assert_available("u@e.com", Provider.OpenCode)
    assert transport.calls == []


def test_claude_code_allowed_when_in_providers(client, transport):
    transport.queue = [{"providers": ["opencode", "claude_code"], "default": "opencode"}]
    client.agents._assert_available("u@e.com", Provider.ClaudeCode)  # no raise
    assert transport.calls[0]["url"].endswith("/api/agent_execution/providers/")


def test_claude_code_blocked_when_not_available(client, transport):
    transport.queue = [{"providers": ["opencode"], "default": "opencode"}]
    with pytest.raises(ProviderUnavailableError, match="claude_code"):
        client.agents._assert_available("u@e.com", Provider.ClaudeCode)


def test_capability_check_failure_is_hard_stop(client, transport):
    def boom(method, url, kwargs):
        raise RuntimeError("network down")

    transport.responder = boom
    with pytest.raises(ProviderUnavailableError, match="could not verify"):
        client.agents._assert_available("u@e.com", Provider.ClaudeCode)


def test_set_provider_posts_correct_body(client, transport):
    transport.queue = [{"current": "claude_code", "providers": ["opencode", "claude_code"]}]
    client.agents.set_provider("u@e.com", Provider.ClaudeCode)
    call = transport.calls[0]
    assert call["url"].endswith("/api/agent_execution/providers/set/")
    assert call["kwargs"]["json"] == {"user_email": "u@e.com", "provider": "claude_code"}


def test_session_requires_user_email(client):
    with pytest.raises(Exception, match="user_email"):
        client.agents.session("space1", provider=Provider.OpenCode, check_capability=False)


# --- streaming run against a fake websocket ---

class _FakeWS:
    """minimal async websocket double: records sends, replays a scripted event list on recv."""

    def __init__(self, script):
        self._script = [json.dumps(e) for e in script]
        self.sent = []
        self.closed = False

    async def send(self, data):
        self.sent.append(json.loads(data))

    async def recv(self):
        if not self._script:
            raise AssertionError("recv called after script exhausted")
        return self._script.pop(0)

    async def close(self):
        self.closed = True


def _session(client, provider=Provider.OpenCode, script=None):
    sess = client.agents.session(
        "space1", provider=provider, user_email="u@e.com",
        check_capability=False, timeout=5, auto_space_state=False,
    )
    sess._ws = _FakeWS(script or [])
    return sess


@pytest.mark.asyncio
async def test_ask_streams_events_and_assembles_result(client):
    script = [
        {"type": "typing_indicator", "is_typing": True},
        {"type": "chat_messages", "content": "Hello ", "provider": "opencode"},
        {"type": "chat_messages", "content": "world", "provider": "opencode"},
        {"type": "chat_complete", "content": "", "provider": "opencode"},
    ]
    sess = _session(client, script=script)
    types = []
    async for ev in sess.ask("hi"):
        types.append(ev.type)
    # the message we sent must carry the provider as model_id.
    assert sess._ws.sent[0]["model_id"] == "opencode"
    assert sess._ws.sent[0]["message"] == "hi"
    assert types == ["typing", "text", "text", "complete"]
    result = sess.result()
    assert isinstance(result, AgentResult)
    assert result.text == "Hello world"
    assert result.failed is False


@pytest.mark.asyncio
async def test_ask_raises_on_provider_mismatch(client):
    # backend silently routed to a different runtime → we refuse to pretend it worked.
    script = [{"type": "chat_messages", "content": "x", "provider": "claude_code"}]
    sess = _session(client, provider=Provider.OpenCode, script=script)
    with pytest.raises(ProviderUnavailableError, match="fallen back"):
        async for _ in sess.ask("hi"):
            pass


@pytest.mark.asyncio
async def test_ask_surfaces_chat_fail(client):
    script = [
        {"type": "chat_messages", "content": "partial", "provider": "opencode"},
        {"type": "chat_fail", "content": "Incorrect API key", "provider": "opencode"},
    ]
    sess = _session(client, script=script)
    async for _ in sess.ask("hi"):
        pass
    result = sess.result()
    assert result.failed is True
    assert "Incorrect API key" in result.error


@pytest.mark.asyncio
async def test_ask_sends_map_and_bag_scoping(client):
    script = [{"type": "chat_complete", "provider": "opencode"}]
    sess = _session(client, script=script)
    async for _ in sess.ask("hi", active_map_id="m1", bag_ids=["b1"], cluster_ids=["c1"]):
        pass
    sent = sess._ws.sent[0]
    assert sent["active_map_id"] == "m1"
    assert sent["bagIds"] == ["b1"]
    assert sent["clusterIds"] == ["c1"]


def test_ws_url_uses_email_path_and_provider(client):
    sess = client.agents.session(
        "space1", provider=Provider.ClaudeCode, user_email="a+b@e.com",
        check_capability=False, auto_space_state=False,
    )
    url = sess._ws_url()
    # email is url-encoded in the path; provider is the model_id; space is a query param.
    assert "/ws/chat/" in url and "/default/" in url
    assert "a%2Bb%40e.com" in url
    assert "model_id=claude_code" in url
    assert "space_id=space1" in url


def test_default_provider_is_opencode():
    assert AgentsResource.DEFAULT_PROVIDER == Provider.OpenCode


def test_session_auto_creates_space_state(client, transport):
    # scoping to a space should mint a space-state and thread it onto the ws url.
    # create() is get-or-create: it lists first (none exist), then POSTs.
    transport.queue = [[], {"id": "ss-123", "name": "SDK agent"}]
    sess = client.agents.session("space1", provider=Provider.OpenCode, user_email="u@e.com",
                                 check_capability=False)
    assert sess.space_state_id == "ss-123"
    assert "space_state_id=ss-123" in sess._ws_url()
    assert "space_id=space1" in sess._ws_url()
    # it hit the cookie-auth space-state endpoint
    assert transport.calls[-1]["url"].endswith("/api/space-state/")


def test_space_state_create_reuses_existing(client, transport):
    # get-or-create: if a state with the same name exists, reuse it (no POST).
    transport.queue = [[{"id": "ss-old", "name": "SDK agent"}]]
    sid = client.space_states.create("space1", name="SDK agent")
    assert sid == "ss-old"
    assert len(transport.calls) == 1  # only the list GET, no create POST


def test_session_skips_space_state_when_disabled(client, transport):
    sess = client.agents.session("space1", provider=Provider.OpenCode, user_email="u@e.com",
                                 check_capability=False, auto_space_state=False)
    assert sess.space_state_id is None
    assert "space_state_id" not in sess._ws_url()
    assert transport.calls == []  # no mint call


def test_session_reuses_explicit_space_state(client, transport):
    sess = client.agents.session("space1", provider=Provider.OpenCode, user_email="u@e.com",
                                 check_capability=False, space_state_id="ss-explicit")
    assert sess.space_state_id == "ss-explicit"
    assert transport.calls == []  # no mint call — caller supplied one


def test_all_spaces_does_not_mint_space_state(client, transport):
    # all_spaces has no single space to scope, so no space-state is created.
    sess = client.agents.session(provider=Provider.OpenCode, user_email="u@e.com",
                                 check_capability=False, all_spaces=True)
    assert sess.space_state_id is None
    assert transport.calls == []


@pytest.mark.asyncio
async def test_all_spaces_initialization_handshake(client):
    # all_spaces=True must send agent_initialization{all_spaces_mode:true} and wait for the ack.
    sess = client.agents.session(
        provider=Provider.OpenCode, user_email="u@e.com", check_capability=False,
        all_spaces=True, timeout=5,
    )
    sess._ws = _FakeWS([{"type": "agent_initialized", "success": True}])
    await sess._initialize()
    assert sess._ws.sent[0]["type"] == "agent_initialization"
    assert sess._ws.sent[0]["all_spaces_mode"] is True


@pytest.mark.asyncio
async def test_initialization_failure_raises(client):
    from mantis_sdk.exceptions import AgentRunError

    sess = client.agents.session(
        provider=Provider.OpenCode, user_email="u@e.com", check_capability=False,
        all_spaces=True, timeout=5,
    )
    sess._ws = _FakeWS([{"type": "agent_initialized", "success": False, "error": "no spaces"}])
    with pytest.raises(AgentRunError, match="initialization failed"):
        await sess._initialize()
