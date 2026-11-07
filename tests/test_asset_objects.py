"""Pins, intent identities and binary data survive every SDK adapter."""
import json
from unittest.mock import MagicMock
from uuid import uuid4

import pandas as pd
import pytest
from test_objects import MAP, OBJ, SPACE, URI, session
from test_transport import _response

from mantis_sdk.exceptions import AuthenticationError
from mantis_sdk.transport import Transport


def test_capture_and_operation_never_send_receipt_contents(client, transport):
    session(client)
    request_id = str(uuid4())
    transport.queue = [{'object': OBJ}, {'object': OBJ}]
    client.objects.capture_cohort(SPACE, MAP, point_ids=[SPACE], columns=['topic'], reviewed=True, request_id=request_id)
    client.objects.run_operation(SPACE, MAP, OBJ, 'group_counts', parameters={'field': 'topic'}, reviewed=True, request_id=request_id)
    assert transport.calls[0]['kwargs']['json']['point_ids'] == [SPACE]
    assert transport.calls[0]['kwargs']['json']['request_id'] == request_id
    assert transport.calls[1]['kwargs']['json']['input'] == {'uri': URI, 'version': 2, 'content_hash': 'a' * 64}


def test_dataframe_download_preserves_exact_point_ids(client, transport):
    session(client)
    raw = json.dumps({'schema': 'mantis.table/v1', 'columns': ['topic'], 'rows': [{'topic': 'A', '_mantis_point_id': SPACE}]}).encode()
    transport.queue = [raw]
    frame = client.objects.dataframe(SPACE, MAP, OBJ)
    assert frame['_mantis_point_id'].tolist() == [SPACE]
    assert frame.attrs['mantis_object']['version'] == 2
    assert transport.calls[0]['kwargs']['response_type'] == 'bytes'


def test_publish_dataframe_normalizes_missing_values_and_links_inputs(client, transport):
    session(client)
    transport.queue = [{'object': OBJ}]
    frame = pd.DataFrame({'metric': [1.0, float('nan')], '_mantis_point_id': [SPACE, MAP]})
    client.objects.publish_dataframe(SPACE, MAP, frame, reviewed=True, inputs=[OBJ])
    body = transport.calls[0]['kwargs']['json']
    assert body['columns'] == ['metric'] and body['rows'] == [{'metric': 1.0}, {'metric': None}]
    assert body['inputs'] == [{'uri': URI, 'version': 2, 'content_hash': 'a' * 64}]


def test_refresh_is_explicit_and_preserves_pin(client, transport):
    session(client)
    transport.queue = [{'object': OBJ}]
    client.objects.refresh(SPACE, MAP, {**OBJ, 'state': 'stale'}, reviewed=True, cascade=True)
    body = transport.calls[0]['kwargs']['json']
    assert body['cascade'] and body['version'] == 2 and body['content_hash'] == 'a' * 64


@pytest.mark.parametrize('arguments', [{}, {'build_artifact_id': 1, 'representation_id': SPACE}])
def test_import_requires_one_origin(client, transport, arguments):
    session(client)
    with pytest.raises(ValueError, match='exactly one'):
        client.objects.import_artifact(SPACE, MAP, reviewed=True, **arguments)
    assert not transport.calls


def test_reusable_workflow_has_real_operation_node_and_separate_runtime_inputs(client, transport):
    session(client)
    workflow = client.objects.operation_workflow('group_counts', parameters={'field': 'topic'})
    assert workflow['nodes'][0]['type'] == 'object_operation'
    assert 'input_text' not in workflow


def test_binary_transport_keeps_typed_error_mapping():
    transport = Transport()
    response = _response(403, {'error': 'Revoked'})
    transport.session.request = MagicMock(return_value=response)
    with pytest.raises(AuthenticationError):
        transport.request('POST', 'http://fixture.test/download', response_type='bytes')
    transport.session.request.assert_called_once()
    assert 'response_type' not in transport.session.request.call_args.kwargs


def test_binary_transport_returns_unmodified_bytes():
    transport = Transport()
    response = _response(200, content_type='application/octet-stream')
    response.content = b'\x00\xffbinary'
    transport.session.request = MagicMock(return_value=response)
    assert transport.request('POST', 'http://fixture.test/download', response_type='bytes') == response.content
