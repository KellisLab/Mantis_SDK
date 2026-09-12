"""Additive event contracts, using synthetic envelopes and no live services."""
from __future__ import annotations

import json

import pytest

from mantis_sdk import AgentEvent, AgentResult, Provider
from mantis_sdk.agents import AgentsResource


def test_cartographer_provider_is_additive():
    assert json.dumps(Provider.Cartographer) == '"cartographer"'
    for value in ("opencode", "claude_code", "hermes", "cartographer"):
        assert Provider(value).value == value
    assert AgentsResource.DEFAULT_PROVIDER is Provider.OpenCode


def test_original_positional_constructors_and_independent_defaults():
    raw = {"type": "tool_use"}
    event = AgentEvent("tool_use", "", "search", "opencode", raw)
    result = AgentResult("answer", "opencode", [event], False, None)
    assert event.raw is raw and event.item_id is None
    assert event.payload == {} and event.metadata == {}
    assert result.text == "answer" and not result.failed
    assert not result.completed and not result.cancelled
    assert result.message_id is None and result.runtime_run_id is None
    event.metadata["local"] = True
    event.payload["local"] = True
    other = AgentEvent("other")
    assert other.metadata == {} and other.payload == {}


@pytest.mark.parametrize("key", ["id", "tool_call_id", "tool_use_id"])
def test_native_tool_identity_and_child_metadata_survive_normalization(key):
    raw = {
        "event": "tool_progress", key: "command-1", "output_delta": "Reading sample 2",
        "runtime": "cartographer", "progress": "running",
        "metadata": {"provider_thread_id": "child-1", "agent_name": "Sample Reader"},
    }
    event = AgentEvent.from_wire(raw)
    assert event.type == "tool_progress" and not event.is_terminal
    assert event.item_id == "command-1" and event.text == "Reading sample 2"
    assert event.provider == "cartographer"
    assert event.metadata == raw["metadata"] and event.raw is raw


def test_tool_call_metadata_update_and_final_structured_output():
    call = AgentEvent.from_wire({
        "event": "tool_call", "id": "command-1", "tool_name": "commandExecution",
        "arguments": {"command": "read synthetic.csv"},
    })
    update = AgentEvent.from_wire({
        "event": "tool_call", "id": "command-1", "metadata_update": True,
        "metadata": {"status": "running"},
    })
    output = {"exitCode": 0, "output": "Δ = 2"}
    result = AgentEvent.from_wire({"event": "tool_output", "id": "command-1", "output": output})
    assert call.type == "tool_use" and call.tool_name == "commandExecution"
    assert call.raw["arguments"] == {"command": "read synthetic.csv"}
    assert update.type == "tool_update" and update.item_id == call.item_id == result.item_id
    assert result.type == "tool_result" and json.loads(result.text) == output
    assert "Δ" in result.text and not result.is_terminal


@pytest.mark.parametrize("wire,payload", [
    ("plan_update", {"plan": [{"step": "Compare samples", "status": "inProgress"}]}),
    ("context_compaction", {"status": "completed"}),
    ("subagent_update", {"thread_id": "child-1", "status": "completed"}),
])
def test_native_activity_keeps_structured_payload_and_is_not_run_completion(wire, payload):
    raw = {"event": wire, "id": "item-1", "payload": payload, "future_field": {"version": 2}}
    event = AgentEvent.from_wire(raw)
    assert event.type == wire and event.payload == payload
    assert not event.is_terminal and event.raw is raw


def test_context_tokens_include_flat_counts_without_discarding_payload_extensions():
    payload = {"max_tokens": 100, "unit": "tokens"}
    event = AgentEvent.from_wire({
        "type": "context_tokens", "current_tokens": 40, "max_tokens": 200, "payload": payload,
    })
    assert event.payload == {"current_tokens": 40, "max_tokens": 200, "unit": "tokens"}
    assert payload == {"max_tokens": 100, "unit": "tokens"}


@pytest.mark.parametrize("invalid", [None, [], "unknown", 7])
def test_non_object_payloads_remain_in_raw_without_breaking_normalization(invalid):
    raw = {"event": "subagent_update", "payload": invalid, "metadata": invalid}
    event = AgentEvent.from_wire(raw)
    assert event.payload == {} and event.metadata == {}
    assert event.raw is raw


def test_partial_text_updates_are_explicit_and_do_not_change_legacy_frames():
    raw = {"sender": "ai", "id": "segment-1", "message": "partial", "partial": True}
    legacy = AgentEvent.from_wire(raw)
    update = AgentEvent.from_wire(raw, text_updates=True)
    committed = AgentEvent.from_wire({**raw, "partial": False}, text_updates=True)
    assert legacy.type == "typing" and legacy.text == ""
    assert update.type == "text_update" and update.text == "partial"
    assert committed.type == "text" and committed.text == "partial"
    assert update.item_id == committed.item_id == "segment-1"


@pytest.mark.parametrize("wire,normalized,terminal", [
    ("runtime_state", "init", False), ("run.accepted", "accepted", False),
    ("run.rejected", "rejected", True), ("run_cancelled", "cancelled", True),
    ("chat_complete", "complete", True), ("chat_fail", "fail", True),
])
def test_terminal_envelopes_take_precedence_over_incidental_text_or_typing_fields(wire, normalized, terminal):
    event = AgentEvent.from_wire({"type": wire, "sender": "ai", "message": "status", "typing": False})
    assert event.type == normalized and event.is_terminal is terminal


def test_admission_rejection_and_authoritative_result_fields():
    event = AgentEvent.from_wire({"type": "run.rejected", "reason": "unavailable", "message": "Try again"})
    assert event.text == "Try again"
    result = AgentResult("", "cartographer", [event], failed=True, error=event.text,
                         message_id="message-1", runtime_run_id="run-1")
    assert result.failed and result.error == "Try again" and not result.completed
    complete = AgentResult("answer", "cartographer", [], completed=True,
                           message_id="message-2", runtime_run_id="run-2")
    cancelled = AgentResult("partial", "cartographer", [], cancelled=True)
    assert complete.completed and complete.message_id == "message-2"
    assert complete.runtime_run_id == "run-2" and cancelled.cancelled


def test_failure_reason_fallback_preserves_historical_content_precedence():
    assert AgentEvent.from_wire({"type": "chat_fail", "reason": "unavailable"}).text == "unavailable"
    assert AgentEvent.from_wire({"type": "chat_fail", "message": "Try again", "reason": "unavailable"}).text == "Try again"
    assert AgentEvent.from_wire({"type": "chat_fail", "content": "saved failure", "message": "Try again"}).text == "saved failure"


def test_historical_tool_result_format_and_unknown_envelopes_remain_readable():
    content = {"rows": 2}
    assert AgentEvent.from_wire({"type": "tool_result", "content": content}).text == str(content)
    raw = {"type": "future_event", "typing": False, "payload": {"version": 2}}
    event = AgentEvent.from_wire(raw)
    assert event.type == "other" and event.raw is raw and event.payload == {"version": 2}


def test_native_event_name_takes_precedence_over_transport_type():
    event = AgentEvent.from_wire({"type": "message", "event": "plan_update", "payload": {"plan": []}})
    assert event.type == "plan_update"
