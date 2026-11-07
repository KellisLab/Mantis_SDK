"""The SDK carries pins; authoritative content always comes from the server."""
import pytest

from mantis_sdk import ConfigurationError
from mantis_sdk.objects import pinned_input

SPACE = '11111111-1111-4111-8111-111111111111'
MAP = '22222222-2222-4222-8222-222222222222'
URI = 'mantis://object/33333333-3333-4333-8333-333333333333'
OBJ = {'uri': URI, 'version': 2, 'content_hash': 'a' * 64,
       'scope': {'space_id': SPACE, 'map_id': MAP}, 'state': 'current',
       'content': {'text': 'do not copy'}, 'provenance': {'claimed': 'do not copy'}}


def session(client):
    client.http.cookie = 'sessionid=test-session; csrftoken=' + 'a' * 32


def test_input_is_portable_and_never_embeds_copied_content():
    result = pinned_input(SPACE, MAP, [OBJ], message='Compare these originals')
    assert result == {'schema': 'mantis.object-request/v1', 'space_id': SPACE, 'map_id': MAP,
                      'objects': [{'uri': URI, 'version': 2, 'content_hash': 'a' * 64}],
                      'message': 'Compare these originals'}


@pytest.mark.parametrize('change', [{'version': True}, {'version': 0}, {'content_hash': 'forged'},
    {'uri': URI + '/extra'}, {'scope': {'space_id': SPACE, 'map_id': SPACE}}, {'state': 'stale'}])
def test_invalid_pins_never_become_requests(change):
    with pytest.raises(ValueError):
        pinned_input(SPACE, MAP, [OBJ | change])


def test_session_and_csrf_are_required_even_for_internal_client(client, transport):
    with pytest.raises(ConfigurationError, match='sessionid and csrftoken'):
        client.objects.catalog(SPACE, MAP)
    assert not transport.calls


def test_catalog_uses_shared_scope_and_csrf_boundary(client, transport):
    session(client)
    transport.queue = [{'objects': [OBJ], 'truncated': False}]
    assert client.objects.catalog(SPACE, MAP)['objects'] == [OBJ]
    call = transport.calls[0]
    assert call['url'].endswith('/api/knowledge/object-catalog/')
    assert call['kwargs']['params'] == {'space_id': SPACE, 'map_id': MAP}
    assert call['headers']['X-CSRFToken'] == 'a' * 32
    assert call['headers']['Referer'].startswith('http')


def test_resolve_reuse_and_explicit_refresh_preserve_versions(client, transport):
    session(client)
    transport.queue = [{'object': OBJ}, {'receipt': {'objects': [OBJ]}}, {'object': OBJ}]
    assert client.objects.resolve(SPACE, MAP, URI, version=2) == OBJ
    assert client.objects.reuse(SPACE, MAP, OBJ, purpose='workflow')['receipt']['objects'] == [OBJ]
    client.objects.register(SPACE, MAP, kind='claim', binding={'node_id': SPACE}, reviewed=True,
                            uri=URI, version=2)
    assert transport.calls[1]['kwargs']['json'] == {'uri': URI, 'version': 2,
                                                  'content_hash': 'a' * 64, 'purpose': 'workflow'}
    assert transport.calls[2]['kwargs']['json']['version'] == 2


def test_workflow_submission_uses_existing_admitted_route(client, transport):
    session(client)
    request = client.objects.workflow_input(SPACE, MAP, [OBJ])
    transport.queue = [{'execution_id': 'queued'}]
    assert client.objects.run_workflow({'name': 'Original input'}, request)['execution_id'] == 'queued'
    call = transport.calls[0]
    assert call['url'].endswith('/api/orchestration/execute/')
    assert call['kwargs']['json']['input_text'] == request
    assert 'content' not in call['kwargs']['json']['input_text']['objects'][0]


def test_server_receipt_cannot_be_silently_reinterpreted_as_request(client, transport):
    session(client)
    request = pinned_input(SPACE, MAP, [OBJ]) | {'schema': 'mantis.object-input/v1'}
    with pytest.raises(ValueError, match='not a server receipt'):
        client.objects.run_workflow({}, request)
    assert not transport.calls


def test_execution_reads_the_server_authorized_status_route(client, transport):
    session(client)
    transport.queue = [{'status': 'completed', 'result': {'rows': [{'score': 7}]}}]
    assert client.objects.execution(SPACE)['result']['rows'][0]['score'] == 7
    assert transport.calls[0]['method'] == 'GET'
    assert transport.calls[0]['url'].endswith(f'/api/orchestration/executions/{SPACE}/')
