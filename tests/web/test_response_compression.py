"""Compression on the API, and the one response that must not have it.

A trace subtree is unbounded — one prompt's `?deep=1` response measured
5.4MB across 1,943 descendants — and werkzeug ships it byte for byte.
The SSE stream is the counter-case: a compressor that buffers a response
which never ends holds every frame forever.
"""
import gzip
import json

import pytest


@pytest.fixture()
def big_json_route(flask_client):
    """A route returning far more than COMPRESS_MIN_SIZE of JSON."""
    app = flask_client.application
    payload = {'rows': [{'i': i, 'text': 'x' * 200} for i in range(200)]}

    @app.route('/api/__compress_probe')
    def _probe():
        return json.dumps(payload), 200, {'Content-Type': 'application/json'}

    return len(json.dumps(payload))


def test_large_json_is_compressed_when_the_client_asks(flask_client, big_json_route):
    resp = flask_client.get('/api/__compress_probe',
                            headers={'Accept-Encoding': 'gzip'})
    assert resp.status_code == 200
    assert resp.headers['Content-Encoding'] == 'gzip'
    assert len(resp.data) < big_json_route / 4
    assert json.loads(gzip.decompress(resp.data))['rows'][0]['i'] == 0


def test_a_client_that_does_not_ask_still_gets_plain_json(flask_client, big_json_route):
    resp = flask_client.get('/api/__compress_probe',
                            headers={'Accept-Encoding': 'identity'})
    assert 'Content-Encoding' not in resp.headers
    assert len(resp.data) == big_json_route


def test_the_event_stream_is_never_compressed(flask_client):
    """`text/event-stream` is outside Flask-Compress's mimetype set, so the
    hub keeps pushing frames as they are produced."""
    ticket = flask_client.post('/api/auth/stream-ticket').get_json()['ticket']
    resp = flask_client.get(f'/api/notifications/stream?ticket={ticket}',
                            headers={'Accept-Encoding': 'gzip, br'},
                            buffered=False)
    try:
        assert resp.status_code == 200
        assert resp.mimetype == 'text/event-stream'
        assert 'Content-Encoding' not in resp.headers
    finally:
        resp.close()
