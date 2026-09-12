"""Delivery v1 contracts with synthetic snapshots and scripted WebSockets."""
from __future__ import annotations

import asyncio
import copy
import json
import uuid
from urllib.parse import parse_qs, urlparse

import pytest
from websockets.exceptions import ConnectionClosed

from mantis_sdk import AgentRunError, ConfigurationError, DeliverySession, Provider, ProviderUnavailableError
from mantis_sdk.agents import AgentsResource

MODEL_ID = "example/analysis-model"
MESSAGE, RUN, SNAPSHOT_ID, CHAT, SPACE, STATE, MAP, CLUSTER, SECOND_RUN = (
    str(uuid.UUID(int=value)) for value in range(1, 10)
)
SNAPSHOT = {"version": 1, "id": SNAPSHOT_ID}


def _frame(sequence, **data):
    return {"runtime_run_id": RUN, "delivery_sequence": sequence,
            "delivery_id": str(uuid.uuid5(uuid.NAMESPACE_OID, str(sequence))), **data}


class FakeWS:
    def __init__(self, session, script=()):
        self.session = session
        self.script = list(script)
        self.sent = []
        self.closed = False

    async def send(self, data):
        self.sent.append(json.loads(data))

    async def recv(self):
        if not self.script:
            await asyncio.Event().wait()
        data = self.script.pop(0)
        if isinstance(data, Exception):
            raise data
        if isinstance(data, str):
            return data
        data = {"message_id": self.session.message_id, "chat_id": self.session.chat_id, **data}
        if data.get("type") == "run.accepted":
            data = {"runtime_run_id": RUN, "context_snapshot": SNAPSHOT, **data}
        if data.get("type") == "runtime_state":
            data = {"runtime_run_id": RUN, "delivery_session_id": self.session.delivery_session_id,
                    "delivery_ack_floor": 0, "run_phase": "active", "terminal_sequence": None, **data}
        return json.dumps(data)

    async def close(self):
        self.closed = True


def _session(client, script=(), auto_accept=True, **kwargs):
    session = client.agents.delivery_session(SPACE, model_id=MODEL_ID, user_email="u@example.com", chat_id=CHAT,
                                    space_state_id=STATE, check_capability=False, timeout=0.02, **kwargs)
    session._ws = FakeWS(session, ([{"type": "run.accepted"}] if auto_accept else []) + list(script))
    return session


async def _ask(session, **kwargs):
    return [event async for event in session.ask("hello", message_id=MESSAGE,
                                                context_snapshot=SNAPSHOT, **kwargs)]


def _acks(session):
    return [frame for frame in session._ws.sent if frame.get("type") == "runtime_delivery_ack"]


async def test_ask_prepares_snapshot_before_sending_and_omits_live_selection(client, transport):
    session = _session(client, [_frame(1, type="chat_complete")])
    transport.queue = [SNAPSHOT]
    events = [event async for event in session.ask("hello", active_map_id=MAP, cluster_ids=[CLUSTER], reasoning_effort="max")]
    sent = session._ws.sent[0]
    assert events[-1].is_terminal
    assert sent["runtime"] == "cartographer" and sent["model_id"] == MODEL_ID and sent["reasoning_effort"] == "max"
    assert sent["context_snapshot"] == SNAPSHOT
    uuid.UUID(sent["message_id"])
    assert not {"bagIds", "clusterIds", "selection", "all_spaces_mode", "composer_mode", "agent_id"} & sent.keys()
    assert len(transport.calls) == 1


async def test_snapshot_failure_sends_nothing(client, transport):
    session = _session(client)
    transport.queue = [{"version": 1}]
    with pytest.raises(AgentRunError, match="snapshot"):
        async for _ in session.ask("hello"):
            pass
    assert session._ws.sent == [] and session.message_id is None


async def test_frozen_snapshot_cannot_be_mixed_with_mutable_sources(client):
    session = _session(client)
    with pytest.raises(ConfigurationError, match="without live"):
        await _ask(session, active_map_id=MAP)
    assert session._ws.sent == []


async def test_text_snapshots_replace_by_segment_and_terminal_never_duplicates_text(client):
    session = _session(client, [
        {"type": "run.accepted"}, {"type": "runtime_state"},
        _frame(1, sender="ai", id="segment-1", message="He", partial=True),
        _frame(2, sender="ai", id="segment-1", message="Hello ", partial=False),
        _frame(3, event="tool_call", id="tool-1", tool_name="commandExecution", arguments={"command": "pwd"}),
        _frame(4, sender="ai", id="segment-2", message="world", partial=False),
        _frame(5, type="chat_complete", content="Hello world", provider="cartographer"),
    ])
    events = await _ask(session)
    assert [event.type for event in events] == ["accepted", "init", "text_update", "text", "tool_use", "text", "complete"]
    assert session.result().text == "Hello world" and session.result().completed
    assert not session.result().failed and session.result().runtime_run_id == RUN
    assert _acks(session)[-1] == {"type": "runtime_delivery_ack", "runtime_run_id": RUN,
                                  "delivery_session_id": session.delivery_session_id, "through_sequence": 5}


async def test_replay_dedupes_native_progress_and_holds_terminal_behind_gap(client):
    progress = _frame(2, event="tool_progress", id="tool", output_delta="working", progress="step 1")
    session = _session(client, [
        _frame(1, event="tool_call", id="tool", tool_name="commandExecution"), progress, progress,
        _frame(5, type="chat_complete"),
        _frame(4, event="tool_output", id="tool", output={"exitCode": 0, "output": "done"}),
        _frame(3, event="context_compaction", id="compact", payload={"status": "completed"}),
    ])
    events = await _ask(session)
    assert [event.type for event in events] == ["accepted", "tool_use", "tool_progress", "context_compaction", "tool_result", "complete"]
    assert [ack["through_sequence"] for ack in _acks(session)] == [1, 2, 2, 2, 2, 5]


async def test_committed_text_or_unsequenced_complete_cannot_claim_success(client):
    session = _session(client, [
        _frame(1, sender="ai", id="segment", message="This is a segment", partial=False),
        {"type": "chat_complete", "runtime_run_id": RUN},
    ])
    with pytest.raises(AgentRunError, match="authoritative terminal"):
        await _ask(session)
    assert session.result().text == "This is a segment" and not session.result().completed
    assert not session.result().failed


async def test_foreign_run_frames_do_not_change_result_or_cursor(client):
    session = _session(client, [
        _frame(1, type="chat_complete", message_id="foreign", provider="claude_code"),
        _frame(1, type="chat_complete", chat_id="foreign", provider="claude_code"),
        _frame(1, type="chat_complete", provider="cartographer"),
    ])
    assert [event.type for event in await _ask(session)] == ["accepted", "complete"]
    assert len(_acks(session)) == 1


async def test_same_message_cannot_change_runtime_or_provider(client):
    session = _session(client, [
        _frame(1, typing=True),
        _frame(2, type="chat_complete", runtime_run_id=SECOND_RUN),
    ])
    with pytest.raises(AgentRunError, match="changed runtime_run_id"):
        await _ask(session)
    session = _session(client, [_frame(1, type="chat_complete", provider="claude_code")])
    with pytest.raises(ProviderUnavailableError, match="Cartographer"):
        await _ask(session)


@pytest.mark.parametrize("wire, field", [("chat_fail", "failed"), ("run_cancelled", "cancelled")])
async def test_failure_and_cancellation_are_authoritative_outcomes(client, wire, field):
    session = _session(client, [_frame(1, type=wire, content="stopped")])
    await _ask(session)
    assert getattr(session.result(), field) and not session.result().completed
    assert _acks(session)[-1]["through_sequence"] == 1


async def test_admission_rejection_retains_error_and_sends_no_ack(client):
    session = _session(client, [{"type": "run.rejected", "reason": "model_unavailable", "message": "Model unavailable"}], auto_accept=False)
    assert [event.type for event in await _ask(session)] == ["rejected"]
    assert session.result().failed and session.result().error == "Model unavailable"
    assert not _acks(session)


async def test_resume_reuses_message_snapshot_effort_and_applied_cursor(client, transport):
    first = _frame(1, sender="ai", id="segment", message="partial", partial=True)
    session = _session(client, [first])
    transport.queue = [SNAPSHOT]
    with pytest.raises(AgentRunError, match="authoritative terminal"):
        async for _ in session.ask("hello", message_id=MESSAGE, reasoning_effort="max"):
            pass
    original = session._ws.sent[0]
    delivery_session = session.delivery_session_id
    await session.close()
    session._ws = FakeWS(session, [
        {"type": "runtime_state", "delivery_ack_floor": 1}, first,
        _frame(2, sender="ai", id="segment", message="complete answer", partial=False),
        _frame(3, type="chat_complete"),
    ])
    events = [event async for event in session.resume()]
    assert session._ws.sent[0] == original and session.delivery_session_id == delivery_session
    assert original["reasoning_effort"] == "max" and len(transport.calls) == 1
    assert [event.type for event in events] == ["init", "text", "complete"]
    assert session.result().text == "complete answer" and session.result().completed
    assert _acks(session)[-1]["through_sequence"] == 3


async def test_terminal_handshake_waits_for_unapplied_text_and_terminal_replay(client):
    session = _session(client, [
        {"type": "runtime_state", "run_phase": "terminal", "terminal_sequence": 2,
         "terminal_payload": {"type": "chat_complete"}},
        _frame(1, sender="ai", id="segment", message="replayed", partial=False),
        _frame(2, type="chat_complete"),
    ])
    events = await _ask(session)
    assert [event.type for event in events] == ["accepted", "init", "text", "complete"]
    assert session.result().text == "replayed"


async def test_server_cannot_skip_unapplied_prefix(client):
    session = _session(client, [{"type": "runtime_state", "delivery_ack_floor": 4}])
    with pytest.raises(AgentRunError, match="ACK floor"):
        await _ask(session)
    assert not session.result().completed and not _acks(session)


async def test_unbounded_gap_fails_without_ack(client):
    session = _session(client, [_frame(3000, type="chat_complete")])
    with pytest.raises(AgentRunError, match="delivery gap"):
        await _ask(session)
    assert not _acks(session)


async def test_disconnect_is_incomplete_and_preserves_prepared_request(client):
    session = _session(client, [ConnectionClosed(None, None)])
    with pytest.raises(AgentRunError, match="reconnect"):
        await _ask(session)
    assert session.message_id == MESSAGE and session.context_snapshot == SNAPSHOT
    assert not session.result().completed


async def test_new_ask_cannot_replace_an_unfinished_turn(client):
    session = _session(client)
    with pytest.raises(AgentRunError, match="authoritative terminal"):
        await _ask(session)
    with pytest.raises(AgentRunError, match="previous turn"):
        async for _ in session.ask("replacement", context_snapshot=SNAPSHOT):
            pass
    assert len(session._ws.sent) == 1


async def test_second_turn_resets_result_but_retains_chat_and_transport_identity(client):
    session = _session(client, [_frame(1, sender="ai", id="segment", message="one", partial=False),
                                _frame(2, type="chat_complete")])
    await _ask(session)
    session._ws.script.extend([{"type": "run.accepted"}, _frame(1, sender="ai", id="segment", message="two", partial=False),
                               _frame(2, type="chat_complete")])
    async for _ in session.ask("next", context_snapshot=SNAPSHOT):
        pass
    assert session.result().text == "two" and session.message_id != MESSAGE
    requests = [frame for frame in session._ws.sent if "message" in frame]
    assert len(requests) == 2 and requests[0]["message_id"] != requests[1]["message_id"]


async def test_cancel_is_a_request_not_terminal_confirmation(client):
    session = _session(client, [_frame(1, typing=True)])
    with pytest.raises(AgentRunError):
        await _ask(session)
    await session.cancel()
    assert session._ws.sent[-1] == {"type": "cancel_run", "message_id": MESSAGE, "runtime_run_id": RUN}
    assert not session.result().cancelled
    session._ws.script.append(_frame(2, type="run_cancelled"))
    _ = [event async for event in session.resume()]
    assert session.result().cancelled


async def test_connect_requires_session_cookie_and_never_sends_initialization(client, monkeypatch):
    session = _session(client)
    await session.close()
    with pytest.raises(ConfigurationError, match="session cookie"):
        await session.__aenter__()
    client.http.cookie = "sessionid=test"
    calls = []

    async def connect(url, *, additional_headers=None, **kwargs):
        calls.append((url, additional_headers))
        return FakeWS(session)

    monkeypatch.setattr("websockets.connect", connect)
    async with session:
        assert session._ws.sent == []
    assert calls == [(session._ws_url(), {"Cookie": "sessionid=test"})]


def test_explicit_factory_preserves_legacy_default_and_separates_model_from_runtime(client):
    session = _session(client)
    assert isinstance(session, DeliverySession) and session.provider is Provider.Cartographer
    assert AgentsResource.DEFAULT_PROVIDER is Provider.OpenCode
    assert session.reasoning_effort == "high"
    first_url = session._ws_url()
    assert first_url == session._ws_url()
    query = parse_qs(urlparse(first_url).query)
    assert query == {"model_id": [MODEL_ID], "runtime_delivery_v": ["1"],
                     "runtime_delivery_session_id": [session.delivery_session_id],
                     "space_id": [SPACE], "space_state_id": [STATE]}
    uuid.UUID(session.delivery_session_id)
    fresh = client.agents.delivery_session(model_id=MODEL_ID, user_email="a+b@example.com", check_capability=False)
    uuid.UUID(fresh.chat_id)
    assert "/new/" not in fresh._ws_url() and "a%2Bb%40example.com" in fresh._ws_url()


@pytest.mark.parametrize("options,field", [
    ({"model_id": ""}, "model_id"), ({"model_id": None}, "model_id"),
    ({"reasoning_effort": "ultra"}, "reasoning_effort"),
    ({"timeout": 0}, "timeout"), ({"timeout": -1}, "timeout"),
    ({"timeout": float("nan")}, "timeout"), ({"timeout": float("inf")}, "timeout"),
    ({"timeout": True}, "timeout"), ({"chat_id": "invalid"}, "chat_id"),
    ({"space_id": "invalid"}, "space_id"), ({"space_state_id": STATE}, "space_id"),
    ({"space_id": SPACE, "auto_space_state": False}, "space_state_id"),
])
def test_invalid_delivery_options_fail_before_network(client, transport, options, field):
    with pytest.raises(ConfigurationError, match=field):
        client.agents.delivery_session(user_email="researcher@example.com", **{"model_id": MODEL_ID, **options})
    assert transport.calls == []


def test_model_and_identity_are_explicit_requirements(client, transport):
    with pytest.raises(TypeError, match="model_id"):
        client.agents.delivery_session(user_email="researcher@example.com")
    with pytest.raises(ConfigurationError, match="user_email"):
        client.agents.delivery_session(model_id=MODEL_ID)
    assert transport.calls == []


def test_capability_checks_and_automatic_space_state_use_existing_rest_contracts(client, transport):
    transport.queue = [{"providers": ["cartographer"]}, [], {"id": STATE, "name": "SDK agent"}]
    session = client.agents.delivery_session(SPACE, model_id=MODEL_ID, user_email="researcher@example.com")
    assert session.space_state_id == STATE and len(transport.calls) == 3
    assert transport.calls[0]["url"].endswith("/api/agent_execution/providers/")
    assert transport.calls[-1]["url"].endswith("/api/space-state/")
    transport.calls.clear()
    transport.queue = [{"providers": ["opencode"]}]
    with pytest.raises(ProviderUnavailableError, match="cartographer"):
        client.agents.delivery_session(model_id=MODEL_ID, user_email="researcher@example.com")
    assert len(transport.calls) == 1


async def test_handshake_without_replayed_terminal_is_not_completion(client):
    session = _session(client, [{"type": "runtime_state", "run_phase": "terminal", "terminal_sequence": 2,
                                 "terminal_payload": {"type": "chat_complete"}}])
    with pytest.raises(AgentRunError, match="authoritative terminal"):
        await _ask(session)
    assert not session.result().completed and not _acks(session)


@pytest.mark.parametrize("reference", [{"version": 1, "id": CHAT}, {"version": True, "id": SNAPSHOT_ID}])
async def test_accepted_snapshot_must_match_the_prepared_identity(client, reference):
    session = _session(client, [{"type": "run.accepted", "context_snapshot": reference}], auto_accept=False)
    with pytest.raises(AgentRunError, match="different context"):
        await _ask(session)
    assert not session.result().completed and not _acks(session)


async def test_duplicate_acceptance_and_terminal_content_do_not_duplicate_result(client):
    session = _session(client, [{"type": "run.accepted"}, {"type": "run.accepted"},
                                _frame(1, sender="ai", id="segment", message="answer", partial=False),
                                _frame(2, type="chat_complete", content="answer")])
    events = await _ask(session)
    assert [event.type for event in events] == ["accepted", "text", "complete"]
    assert session.result().text == "answer"


@pytest.mark.parametrize("pending", [False, True])
async def test_replayed_sequence_cannot_change_content(client, pending):
    sequence = 2 if pending else 1
    original = _frame(sequence, event="tool_progress", id="tool", output_delta="first")
    session = _session(client, [original, {**original, "output_delta": "changed"}])
    with pytest.raises(AgentRunError, match="changed a previously delivered sequence"):
        await _ask(session)
    assert _acks(session)[-1]["through_sequence"] == (0 if pending else 1)


async def test_delivery_identity_cannot_be_reassigned_to_another_sequence(client):
    first = _frame(1, event="tool_progress", id="tool", output_delta="first")
    session = _session(client, [first, _frame(2, type="chat_complete", delivery_id=first["delivery_id"])])
    with pytest.raises(AgentRunError, match="reused a delivery identity"):
        await _ask(session)
    assert not session.result().completed and _acks(session)[-1]["through_sequence"] == 1


@pytest.mark.parametrize("wire,reason", [("run_cancelled", None), ("chat_fail", "cancelled"), ("chat_fail", "CANCELED")])
async def test_authoritative_cancellation_discards_only_the_server_named_prefix(client, wire, reason):
    session = _session(client, [
        _frame(1, sender="ai", id="segment", message="applied", partial=True),
        _frame(3, sender="ai", id="segment", message="queued", partial=True),
        _frame(5000, type=wire, reason=reason, discard_through_sequence=4999),
    ])
    events = await _ask(session)
    assert [event.type for event in events] == ["accepted", "text_update", "cancelled"]
    assert session.result().cancelled and not session.result().failed
    assert not session.result().completed and session.result().text == "applied"
    assert _acks(session)[-1]["through_sequence"] == 5000


@pytest.mark.parametrize("discarded", [True, -1, 1, 3])
async def test_invalid_cancellation_discard_marker_never_advances_ack(client, discarded):
    session = _session(client, [_frame(3, type="run_cancelled", discard_through_sequence=discarded)])
    with pytest.raises(AgentRunError, match="invalid cancellation discard"):
        await _ask(session)
    assert not session.result().cancelled and not _acks(session)


async def test_non_cancellation_cannot_discard_missing_output(client):
    session = _session(client, [_frame(3, type="chat_complete", discard_through_sequence=2)])
    with pytest.raises(AgentRunError, match="authoritative terminal"):
        await _ask(session)
    assert not session.result().completed and _acks(session)[-1]["through_sequence"] == 0


async def test_cancel_rejection_is_not_a_run_terminal(client):
    session = _session(client, [_frame(1, type="run_cancel_pending"),
                                _frame(2, type="run_cancel_rejected"), _frame(3, type="chat_complete")])
    await _ask(session)
    assert session.result().completed and not session.result().cancelled


async def test_rejection_after_admission_does_not_report_the_running_turn_as_failed(client):
    session = _session(client, [{"type": "run.accepted"}])
    with pytest.raises(AgentRunError, match="authoritative terminal"):
        await _ask(session)
    session._ws = FakeWS(session, [{"type": "run.rejected", "reason": "context_forbidden"}])
    with pytest.raises(AgentRunError, match="rejected resuming"):
        _ = [event async for event in session.resume()]
    result = session.result()
    assert not result.failed and not result.completed and not result.cancelled and result.runtime_run_id == RUN


async def test_rejected_admission_cannot_be_retried_by_resume(client):
    session = _session(client, [{"type": "run.rejected", "reason": "unavailable"}], auto_accept=False)
    await _ask(session)
    sent = copy.deepcopy(session._ws.sent)
    with pytest.raises(AgentRunError, match="rejected before admission"):
        _ = [event async for event in session.resume()]
    assert session._ws.sent == sent


@pytest.mark.parametrize("terminal", [False, True])
async def test_ack_loss_preserves_applied_state_and_resume_repairs_it(client, terminal):
    first = _frame(1, type="chat_complete") if terminal else _frame(1, sender="ai", id="segment", message="answer", partial=False)
    session = _session(client)

    class LostAck(FakeWS):
        async def send(self, data):
            if json.loads(data).get("type") == "runtime_delivery_ack":
                raise ConnectionClosed(None, None)
            await super().send(data)

    session._ws = LostAck(session, [{"type": "run.accepted"}, first])
    with pytest.raises(AgentRunError, match="disconnected"):
        await _ask(session)
    assert session.result().completed is terminal
    previous_count = len(session.result().events)
    session._ws = FakeWS(session, [{"type": "runtime_state"}] if terminal else [first, _frame(2, type="chat_complete")])
    _ = [event async for event in session.resume()]
    assert session.result().completed
    assert len(session.result().events) == previous_count + (0 if terminal else 1)
    assert _acks(session)[-1]["through_sequence"] == (1 if terminal else 2)


async def test_resume_pins_snapshot_model_effort_and_message_after_caller_changes(client):
    supplied = {**SNAPSHOT, "digest": "synthetic-digest"}
    session = _session(client)
    with pytest.raises(AgentRunError):
        _ = [event async for event in session.ask("hello", message_id=MESSAGE, context_snapshot=supplied,
                                                 reasoning_effort="max")]
    original = copy.deepcopy(session._ws.sent[0])
    supplied["id"] = CHAT
    session.context_snapshot["id"] = CHAT
    session.model_id = "example/changed-model"
    session.reasoning_effort = "low"
    session._ws = FakeWS(session, [_frame(1, type="chat_complete")])
    _ = [event async for event in session.resume()]
    assert session._ws.sent[0] == original
    assert session.context_snapshot == SNAPSHOT
    assert parse_qs(urlparse(session._ws_url()).query)["model_id"] == [MODEL_ID]


async def test_yielded_and_result_events_cannot_mutate_authoritative_outcome(client):
    session = _session(client, [_frame(1, type="chat_complete")])
    events = await _ask(session)
    events[-1].type = "fail"
    result = session.result()
    result.events[-1].type = "cancelled"
    assert session.result().completed and not session.result().failed and not session.result().cancelled


async def test_closing_iterator_releases_consumer_without_cancelling_server_turn(client):
    session = _session(client, [_frame(1, typing=True)])
    stream = session.ask("hello", message_id=MESSAGE, context_snapshot=SNAPSHOT)
    await anext(stream)
    with pytest.raises(AgentRunError, match="Only one consumer"):
        _ = [event async for event in session.resume()]
    await stream.aclose()
    assert not session._streaming and not any(frame.get("type") == "cancel_run" for frame in session._ws.sent)
    session._ws.script.append(_frame(2, type="chat_complete"))
    _ = [event async for event in session.resume()]
    assert session.result().completed


async def test_finished_message_ids_cannot_be_reused_for_a_later_turn(client):
    session = _session(client, [_frame(1, type="chat_complete")])
    await _ask(session)
    session._ws.script.extend([{"type": "run.accepted"}, _frame(1, type="chat_complete")])
    _ = [event async for event in session.ask("second", context_snapshot=SNAPSHOT)]
    with pytest.raises(ConfigurationError, match="already used"):
        _ = [event async for event in session.ask("third", message_id=MESSAGE, context_snapshot=SNAPSHOT)]


async def test_completed_child_activity_keeps_structure_without_completing_the_analysis(client):
    activity = _frame(1, event="subagent_update", id="child-1",
                      payload={"status": "completed"}, metadata={"agent_name": "Sample Reader"})
    session = _session(client, [activity, activity, _frame(2, event="plan_update", payload={"plan": []})])
    with pytest.raises(AgentRunError, match="authoritative terminal"):
        await _ask(session)
    result = session.result()
    assert [event.type for event in result.events] == ["accepted", "subagent_update", "plan_update"]
    assert result.events[1].metadata["agent_name"] == "Sample Reader"
    assert result.events[1].payload["status"] == "completed"
    assert not result.completed and not result.failed and not result.cancelled


async def test_websocket_legacy_header_keyword_is_selected_without_retry(client, monkeypatch):
    session = _session(client)
    await session.close()
    client.http.cookie = "sessionid=test"
    calls = []

    async def connect(url, *, extra_headers=None, **kwargs):
        calls.append(extra_headers)
        return FakeWS(session)

    monkeypatch.setattr("websockets.connect", connect)
    async with session:
        assert session._ws.sent == []
    assert calls == [{"Cookie": "sessionid=test"}]


async def test_connect_typeerror_does_not_trigger_a_second_handshake(client, monkeypatch):
    session = _session(client)
    await session.close()
    client.http.cookie = "sessionid=test"
    calls = []

    async def connect(url, *, additional_headers=None, **kwargs):
        calls.append(url)
        raise TypeError("unexpected transport failure")

    monkeypatch.setattr("websockets.connect", connect)
    with pytest.raises(TypeError, match="unexpected transport failure"):
        await session.__aenter__()
    assert len(calls) == 1


async def test_early_sequenced_terminal_cannot_replace_verified_snapshot_acceptance(client):
    session = _session(client, [_frame(1, type="chat_complete")], auto_accept=False)
    with pytest.raises(AgentRunError, match="authoritative terminal"):
        await _ask(session)
    assert not session.result().completed and session.result().events == []
    assert session.runtime_run_id is None and not _acks(session)


async def test_early_terminal_with_foreign_accepted_snapshot_never_applies_or_acks(client):
    session = _session(client, [_frame(1, type="chat_complete"),
                                {"type": "run.accepted", "context_snapshot": {"version": 1, "id": CHAT}}],
                       auto_accept=False)
    with pytest.raises(AgentRunError, match="different context snapshot"):
        await _ask(session)
    assert not session.result().completed and session.result().events == []
    assert session.runtime_run_id is None and not _acks(session)


async def test_early_frames_apply_after_acceptance_and_only_in_sequence(client):
    session = _session(client, [_frame(2, type="chat_complete"), {"type": "run.accepted"},
                                _frame(1, sender="ai", id="segment", message="verified", partial=False)],
                       auto_accept=False)
    events = await _ask(session)
    assert [event.type for event in events] == ["accepted", "text", "complete"]
    assert session.result().text == "verified" and session.result().completed
    assert [ack["through_sequence"] for ack in _acks(session)] == [0, 2]


async def test_initial_state_and_replay_wait_for_matching_acceptance(client):
    session = _session(client, [{"type": "runtime_state"}, _frame(1, type="chat_complete"),
                                {"type": "run.accepted"}], auto_accept=False)
    events = await _ask(session)
    assert [event.type for event in events] == ["accepted", "init", "complete"]
    assert session.result().completed and _acks(session)[-1]["through_sequence"] == 1


async def test_pre_acceptance_buffer_is_bounded(client, monkeypatch):
    monkeypatch.setattr("mantis_sdk.delivery._MAX_REPLAY_GAP", 2)
    session = _session(client, [_frame(1, typing=True), _frame(2, typing=True), _frame(3, typing=True)], auto_accept=False)
    with pytest.raises(AgentRunError, match="pre-acceptance buffer"):
        await _ask(session)
    assert session.result().events == [] and not _acks(session)


async def test_terminal_ack_repair_waits_for_current_connection_cursor_claim(client):
    session = _session(client, [_frame(1, type="chat_complete")])
    await _ask(session)

    class ClaimedSocket(FakeWS):
        claimed = False

        async def recv(self):
            raw = await super().recv()
            frame = json.loads(raw)
            if frame.get("type") == "runtime_state" and frame.get("delivery_session_id") == session.delivery_session_id:
                self.claimed = True
            return raw

        async def send(self, data):
            if json.loads(data).get("type") == "runtime_delivery_ack":
                assert self.claimed, "ACK sent before this connection received its cursor handshake"
            await super().send(data)

    session._ws = ClaimedSocket(session, [_frame(1, type="chat_complete"),
                                         {"type": "run.accepted"},
                                         {"type": "runtime_state", "delivery_session_id": "foreign"},
                                         {"type": "runtime_state"}])
    assert [event async for event in session.resume()] == []
    assert _acks(session)[-1]["through_sequence"] == 1 and session.result().completed


async def test_terminal_ack_repair_without_current_handshake_keeps_confirmed_outcome(client):
    session = _session(client, [_frame(1, type="chat_complete")])
    await _ask(session)
    session._ws = FakeWS(session, [{"type": "run.accepted"}])
    with pytest.raises(AgentRunError, match="acknowledgment synchronization"):
        _ = [event async for event in session.resume()]
    assert session.result().completed and not _acks(session)
