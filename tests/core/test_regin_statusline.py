"""Smoke tests for scripts/regin-statusline.

The script must be executable by Claude Code as `statusLine.command`,
so we invoke it via subprocess and feed it the same kind of stdin
JSON Claude Code would send. Two properties matter:

1. The script always prints *something* — returning an empty body
   from the statusline would leave Claude Code's UI blank.
2. A malformed stdin, a missing session_id, or a dead ingest endpoint
   all result in exit 0. A statusline that fails is worse than one
   that silently stales.

We don't assert on the POST body shape here — that contract is
covered by `tests/test_blueprint_trace_extra.py::test_session_status_*`
against the real endpoint.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


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
        'context_window': {'used_tokens': 180000,
                           'total_tokens': 1_000_000,
                           'used_percentage': 18.0},
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
        'context_window': {'used_tokens': 180000, 'total_tokens': 1_000_000},
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
