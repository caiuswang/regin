"""Smoke tests for scripts/regin-statusline.

The script must be executable by Claude Code as `statusLine.command`,
so we invoke it via subprocess and feed it the same kind of stdin
JSON Claude Code would send. Three properties matter:

1. The script always prints *something* — returning an empty body
   from the statusline would leave Claude Code's UI blank.
2. A malformed stdin, a missing session_id, or a dead ingest endpoint
   all result in exit 0. A statusline that fails is worse than one
   that silently stales.
3. The POST body carries the context numbers. This one is not a
   smoke test: the script once read `context_window.used_tokens` /
   `.total_tokens`, Claude Code renamed both keys, and because these
   tests fed the script the *same* stale names the drift stayed green
   for months while every real payload silently ingested nothing.
   The capture below is the guard — it fails if the extraction breaks
   again, whatever the endpoint does with the body.
"""

from __future__ import annotations

import json
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


# Verbatim `context_window` block from a Claude Code v2.1.258 statusline
# payload — keep it real, since a hand-written stub is exactly what let
# the field rename through unnoticed.
CONTEXT_WINDOW = {
    'total_input_tokens': 93076,
    'total_output_tokens': 499,
    'context_window_size': 1_000_000,
    'current_usage': {
        'input_tokens': 2,
        'output_tokens': 499,
        'cache_creation_input_tokens': 2515,
        'cache_read_input_tokens': 90559,
    },
    'used_percentage': 18.0,
    'remaining_percentage': 82.0,
}


SCRIPT = Path(__file__).resolve().parent.parent.parent / 'scripts' / 'regin-statusline'


def _run(stdin_text: str, *args: str) -> tuple[int, str, str]:
    """Run the script with the given stdin and args. Returns (rc, stdout, stderr)."""
    # REGIN_URL points at a deliberately-unreachable endpoint so the
    # script's ingest POST fails fast instead of either hitting a real
    # dev server or hanging on a DNS timeout.
    env = {'REGIN_URL': 'http://127.0.0.1:1', 'PATH': '/usr/bin:/bin'}
    proc = subprocess.run(
        ['python3', str(SCRIPT), *args],
        input=stdin_text,
        capture_output=True,
        text=True,
        env=env,
        timeout=5,
    )
    return proc.returncode, proc.stdout, proc.stderr


def test_prints_default_statusline_when_ingest_endpoint_down():
    # Use the test runner's actual home so the home→~ collapsing path
    # gets exercised regardless of who runs the suite.
    home = str(Path.home())
    cwd = str(Path.home() / 'regin')
    payload = {
        'session_id': '32f89830',
        'cwd': cwd,
        'model': {'id': 'claude-opus-4-7[1m]',
                  'display_name': 'Opus 4.7 (1M context)'},
        'context_window': CONTEXT_WINDOW,
    }
    rc, out, _ = _run(json.dumps(payload))
    assert rc == 0
    # Default statusline must still render every piece the user would
    # expect: truncated cwd, model name, ctx badge.
    assert 'Opus 4.7 (1M context)' in out
    assert 'ctx:18%' in out
    # $HOME is expanded to ~ so the line stays narrow.
    assert 'regin' in out
    assert home not in out


def test_ingest_only_mode_prints_nothing():
    payload = {
        'session_id': '32f89830',
        'model': {'id': 'claude-opus-4-7[1m]'},
        'context_window': CONTEXT_WINDOW,
    }
    rc, out, _ = _run(json.dumps(payload), '--ingest-only')
    assert rc == 0
    # --ingest-only means the user has their own renderer — don't let
    # this script clobber it.
    assert out == ''


def test_malformed_stdin_is_silent_noop():
    rc, out, _ = _run('not json at all')
    assert rc == 0
    # Parse failure → short-circuit before render, so stdout is empty.
    assert out == ''


def test_missing_session_id_still_renders_default():
    """Claude Code sometimes invokes the statusline with a partial
    payload during startup. Even if we can't ingest, we must still
    render something the user sees in their terminal."""
    payload = {
        'cwd': '/tmp',
        'model': {'display_name': 'Opus 4.7'},
    }
    rc, out, _ = _run(json.dumps(payload))
    assert rc == 0
    assert 'Opus 4.7' in out


class _Capture(BaseHTTPRequestHandler):
    body: dict | None = None

    def do_POST(self):  # noqa: N802 — BaseHTTPRequestHandler's spelling
        raw = self.rfile.read(int(self.headers.get('Content-Length') or 0))
        type(self).body = json.loads(raw.decode())
        self.send_response(200)
        self.send_header('Content-Length', '0')
        self.end_headers()

    def log_message(self, *_args):
        pass


def _post_body(payload: dict) -> dict | None:
    """Run the script against a throwaway server and return what it POSTed."""
    _Capture.body = None
    server = HTTPServer(('127.0.0.1', 0), _Capture)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        env = {'REGIN_URL': f'http://127.0.0.1:{server.server_port}',
               'PATH': '/usr/bin:/bin'}
        subprocess.run(['python3', str(SCRIPT), '--ingest-only'],
                       input=json.dumps(payload), capture_output=True,
                       text=True, env=env, timeout=5)
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
    return _Capture.body


def test_ingest_posts_the_current_context_window_fields():
    body = _post_body({
        'session_id': '32f89830',
        'model': {'id': 'claude-opus-4-7[1m]'},
        'context_window': CONTEXT_WINDOW,
    })
    assert body == {
        'trace_id': '32f89830',
        'model': 'claude-opus-4-7[1m]',
        'context_used_tokens': 93076,
        'context_window_tokens': 1_000_000,
    }


def test_ingest_still_reads_the_pre_rename_field_names():
    body = _post_body({
        'session_id': '32f89830',
        'context_window': {'used_tokens': 180000, 'total_tokens': 200_000},
    })
    assert body == {
        'trace_id': '32f89830',
        'context_used_tokens': 180000,
        'context_window_tokens': 200_000,
    }


def test_ingest_omits_context_fields_when_the_block_is_unrecognized():
    """A future rename must degrade to model-only, never to a bogus number."""
    body = _post_body({
        'session_id': '32f89830',
        'model': {'id': 'claude-opus-5'},
        'context_window': {'some_future_name': 12345},
    })
    assert body == {'trace_id': '32f89830', 'model': 'claude-opus-5'}
