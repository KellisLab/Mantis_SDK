"""Explicit context preparation contracts with synthetic data and recorded HTTP."""
from __future__ import annotations

import copy
import json
import uuid

import pytest

from mantis_sdk import AgentRunError, ConfigurationError, Provider
from mantis_sdk.agents import AgentsResource

CHAT, SPACE, STATE, MAP, BAG, CLUSTER, POINT, SOURCE, SNAPSHOT, OTHER_SPACE = (
    str(uuid.UUID(int=value)) for value in range(1, 11)
)


def _session(client, **kwargs):
    options = {"space_id": SPACE, "space_state_id": STATE, "chat_id": CHAT,
               "user_email": "researcher@example.com", "check_capability": False,
               "auto_space_state": False, **kwargs}
    return client.agents.session(**options)


def _prepare(session, transport, **kwargs):
    response = {"version": 1, "id": SNAPSHOT, "digest": "synthetic-digest", "counts": {"points": 3}}
    transport.queue = [response]
    result = session.prepare_context(**kwargs)
    assert result == response
    assert transport.calls[-1]["method"] == "POST"
    assert transport.calls[-1]["url"].endswith("/api/orchestration/composer/context-snapshots/")
    return transport.calls[-1]["kwargs"]["json"]


def test_whole_space_is_the_default_and_preparation_does_not_open_a_socket(client, transport):
    session = _session(client)
    assert session._ws is None
    assert _prepare(session, transport) == {
        "version": 1, "scope": {"chat_id": CHAT, "space_id": SPACE, "space_state_id": STATE},
        "space_ids": [SPACE],
    }
    assert session._ws is None and session._events == []
    assert session.chat_id == CHAT and session.server_chat_id is None
    assert len(transport.calls) == 1


@pytest.mark.parametrize("spaces", [[], [OTHER_SPACE], [SPACE, OTHER_SPACE]])
def test_explicit_empty_and_cross_space_sources_are_preserved(client, transport, spaces):
    request = _prepare(_session(client), transport, space_ids=spaces)
    assert request["space_ids"] == spaces
    assert request["scope"]["space_id"] == SPACE


def test_membership_selection_omits_implicit_whole_space_sources(client, transport):
    assert _prepare(_session(client), transport, active_map_id=MAP,
                    bag_ids=[BAG], cluster_ids=[CLUSTER], point_ids=[POINT]) == {
        "version": 1, "scope": {"chat_id": CHAT, "space_id": SPACE, "space_state_id": STATE},
        "space_ids": [],
        "selections": [{"map_id": MAP, "bag_ids": [BAG], "cluster_ids": [CLUSTER], "point_ids": [POINT]}],
    }


def test_explicit_whole_space_and_selected_points_can_be_combined(client, transport):
    request = _prepare(_session(client), transport, active_map_id=MAP,
                       point_ids=[POINT], space_ids=[OTHER_SPACE])
    assert request["space_ids"] == [OTHER_SPACE]
    assert request["selections"][0]["point_ids"] == [POINT]


@pytest.mark.parametrize("kwargs,expected", [({}, [SPACE]), ({"space_ids": []}, [])])
def test_active_map_without_membership_filters_preserves_explicit_source_intent(client, transport, kwargs, expected):
    request = _prepare(_session(client), transport, active_map_id=MAP, **kwargs)
    assert request["space_ids"] == expected
    assert request["selections"] == [{"map_id": MAP, "bag_ids": [], "cluster_ids": [], "point_ids": []}]


def test_files_snapshots_and_notebook_context_are_forwarded_without_mutating_inputs(client, transport):
    options = {"space_ids": [], "files": [{"space_id": SPACE, "path": "samples/example.csv"}],
               "source_snapshot_ids": [SOURCE], "open_notebook_path": "analysis.ipynb"}
    original = copy.deepcopy(options)
    request = _prepare(_session(client), transport, **options)
    assert all(request[key] == value for key, value in options.items())
    assert options == original


def test_unscoped_chat_can_prepare_explicit_sources(client, transport):
    session = _session(client, space_id=None, space_state_id=None)
    request = _prepare(session, transport, space_ids=[OTHER_SPACE])
    assert request["scope"] == {"chat_id": CHAT} and request["space_ids"] == [OTHER_SPACE]


def test_captured_server_chat_identity_is_used_without_changing_the_legacy_session(client, transport):
    session = _session(client, chat_id="new")
    session.server_chat_id = CHAT
    request = _prepare(session, transport)
    assert request["scope"]["chat_id"] == CHAT
    assert session.chat_id == "new" and session.server_chat_id == CHAT


@pytest.mark.parametrize("session_options,field", [
    ({"chat_id": "new"}, "chat_id"), ({"chat_id": "arbitrary-name"}, "chat_id"),
    ({"space_id": "invalid"}, "space_id"), ({"space_state_id": "invalid"}, "space_state_id"),
    ({"space_id": None}, "space_id"),
])
def test_invalid_scope_is_rejected_before_http(client, transport, session_options, field):
    session = _session(client, **session_options)
    with pytest.raises(ConfigurationError, match=field):
        session.prepare_context()
    assert transport.calls == []


@pytest.mark.parametrize("field,value", [
    ("active_map_id", "invalid"), ("bag_ids", ["invalid"]), ("cluster_ids", ["invalid"]),
    ("point_ids", ["invalid"]), ("space_ids", ["invalid"]), ("source_snapshot_ids", ["invalid"]),
    ("bag_ids", BAG), ("space_ids", SPACE), ("source_snapshot_ids", SOURCE),
    ("files", ["invalid"]), ("files", {}), ("open_notebook_path", 5),
])
def test_invalid_source_shapes_are_rejected_before_http(client, transport, field, value):
    with pytest.raises(ConfigurationError, match=field):
        _session(client).prepare_context(**{field: value})
    assert transport.calls == []


@pytest.mark.parametrize("field,value", [("bag_ids", BAG), ("cluster_ids", CLUSTER), ("point_ids", POINT)])
def test_membership_filters_require_an_active_map(client, transport, field, value):
    with pytest.raises(ConfigurationError, match="active_map_id"):
        _session(client).prepare_context(**{field: [value]})
    assert transport.calls == []


def test_map_selection_requires_a_bound_space_thread(client, transport):
    with pytest.raises(ConfigurationError, match="space_state_id"):
        _session(client, space_state_id=None).prepare_context(active_map_id=MAP)
    assert transport.calls == []


@pytest.mark.parametrize("response", [
    None, [], {}, {"version": 2, "id": SNAPSHOT}, {"version": True, "id": SNAPSHOT},
    {"version": "1", "id": SNAPSHOT}, {"version": 1}, {"version": 1, "id": "invalid"},
])
def test_invalid_snapshot_responses_fail_without_retry_or_session_mutation(client, transport, response):
    session = _session(client)
    transport.queue = [response]
    with pytest.raises(AgentRunError, match="snapshot"):
        session.prepare_context()
    assert len(transport.calls) == 1
    assert session.chat_id == CHAT and session._events == [] and session._ws is None


def test_server_authorization_error_is_preserved(client, transport):
    from mantis_sdk import AuthenticationError

    error = AuthenticationError("Source access denied", status_code=403)

    def deny(*args):
        raise error

    transport.responder = deny
    with pytest.raises(AuthenticationError) as raised:
        _session(client).prepare_context(space_ids=[OTHER_SPACE])
    assert raised.value is error and len(transport.calls) == 1


async def test_preparation_preserves_legacy_ask_payload_and_default_provider(client, transport):
    class Socket:
        def __init__(self):
            self.sent = []

        async def send(self, payload):
            self.sent.append(json.loads(payload))

        async def recv(self):
            return json.dumps({"type": "chat_complete", "provider": "opencode"})

    session = _session(client)
    socket = session._ws = Socket()
    _prepare(session, transport, space_ids=[])
    events = [event async for event in session.ask("Compare samples", active_map_id=MAP, bag_ids=[BAG])]
    assert socket.sent == [{"message": "Compare samples", "model_id": "opencode",
                            "generate_suggestions": False, "active_map_id": MAP, "bagIds": [BAG]}]
    assert events[-1].type == "complete" and session.provider is Provider.OpenCode
    assert AgentsResource.DEFAULT_PROVIDER is Provider.OpenCode
