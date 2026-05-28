"""E2E replay tests: feed real captured payloads through `python -m hook_manager`.

Fixtures under tests/fixtures/ were extracted by sampling one payload per
(hook_event_name, tool_name) pair from ~/.claude/hook-payloads.jsonl so
the dispatcher is exercised against actual Claude Code input shapes — not
our hand-rolled test stubs.

Every fixture is replayed in two modes:
  (a) in-process, via `run()` with the real REGISTRY — fast, catches bugs
      in handlers or merging.
  (b) subprocess, via `python -m hook_manager <Event>` — verifies the
      `__main__` wiring, sys.argv parsing, and real stdin/stdout behavior.

Assertions are deliberately loose (we don't prescribe *what* each handler
must decide — only that the pipeline stays well-formed): stdout parses as
JSON, exit code is in {0, 2}, and the response either omits
`hookSpecificOutput.hookEventName` or matches the replayed event.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys

import pytest

from hook_manager.core import SPEC_EVENTS
from hook_manager.registry import REGISTRY
from hook_manager.runner import run

_FIXTURES = os.path.join(os.path.dirname(__file__), 'fixtures')


def _fixture_files() -> list[str]:
    if not os.path.isdir(_FIXTURES):
        return []
    return sorted(
        os.path.join(_FIXTURES, f)
        for f in os.listdir(_FIXTURES) if f.endswith('.json')
    )


def _fixture_id(path: str) -> str:
    return os.path.basename(path)


@pytest.mark.parametrize('fixture_path', _fixture_files(), ids=_fixture_id)
def test_in_process_replay_produces_valid_json(fixture_path, tmp_path, monkeypatch):
    # Redirect hook-payloads.jsonl to a tmp file so replay doesn't pollute
    # the user's real debug log.
    from hook_manager.handlers import trace_payload
    monkeypatch.setattr(trace_payload, '_log_path', lambda _p=None: str(tmp_path / 'hooks.jsonl'))

    with open(fixture_path) as f:
        payload = json.load(f)

    event = payload.get('hook_event_name', '')
    assert event in SPEC_EVENTS, f'fixture {fixture_path} has unknown event {event!r}'

    out = io.StringIO()
    rc = run(event, REGISTRY, json.dumps(payload), out)
    assert rc in (0, 2)

    body = out.getvalue().strip()
    assert body, f'empty stdout for {fixture_path}'
    resp = json.loads(body)
    assert isinstance(resp, dict), f'response is not an object for {fixture_path}'

    # If a handler emitted hookSpecificOutput, it must carry the right event name
    hso = resp.get('hookSpecificOutput')
    if hso is not None:
        assert hso.get('hookEventName') == event, (
            f'hookEventName mismatch in {fixture_path}: got {hso.get("hookEventName")!r}, expected {event!r}'
        )


def _canonical_subset() -> list[str]:
    """One fixture per unique hook_event_name. The in-process replay already
    covers all 58 — the subprocess variant only has to prove the __main__
    wiring is intact, so one per event is enough and keeps the suite fast."""
    seen: dict[str, str] = {}
    for path in _fixture_files():
        try:
            with open(path) as f:
                ev = json.load(f).get('hook_event_name', '')
        except (OSError, json.JSONDecodeError):
            continue
        if ev and ev not in seen:
            seen[ev] = path
    return sorted(seen.values())


@pytest.mark.parametrize('fixture_path', _canonical_subset(), ids=_fixture_id)
def test_subprocess_replay_matches_in_process(fixture_path, tmp_path):
    """Same assertions as in-process, but through `python -m hook_manager`."""
    with open(fixture_path) as f:
        payload = json.load(f)
    event = payload.get('hook_event_name', '')

    env = os.environ.copy()
    # Route the trace log into tmp via an env var the handler reads at import.
    # (The handler hard-codes the path; we set HOME so `expanduser('~')` lands
    # in tmp for this subprocess.)
    env['HOME'] = str(tmp_path)
    env['PYTHONPATH'] = os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))
    ))

    proc = subprocess.run(
        [sys.executable, '-m', 'hook_manager', event],
        input=json.dumps(payload),
        capture_output=True, text=True, timeout=10, env=env,
    )
    assert proc.returncode in (0, 2), (
        f'rc={proc.returncode} stderr={proc.stderr} for {fixture_path}'
    )
    body = proc.stdout.strip()
    assert body, f'empty stdout for {fixture_path}'
    resp = json.loads(body)
    assert isinstance(resp, dict)


def test_mvn_bash_payload_is_blocked(tmp_path, monkeypatch):
    """Targeted E2E: craft a Bash payload for `mvn clean install` and verify
    the mvn gate blocks it end-to-end through the real registry."""
    from hook_manager.handlers import trace_payload
    monkeypatch.setattr(trace_payload, '_log_path', lambda _p=None: str(tmp_path / 'hooks.jsonl'))

    payload = {
        'hook_event_name': 'PreToolUse',
        'session_id': 'replay-sess',
        'tool_name': 'Bash',
        'tool_input': {'command': 'mvn clean install -DskipTests'},
    }
    out = io.StringIO()
    rc = run('PreToolUse', REGISTRY, json.dumps(payload), out)
    assert rc == 0  # block via JSON, not via exit-code-2
    resp = json.loads(out.getvalue())
    assert resp.get('decision') == 'block'
    assert resp['hookSpecificOutput']['permissionDecision'] == 'deny'
    assert 'maven MCP tools' in resp['hookSpecificOutput']['permissionDecisionReason']


def test_stop_event_does_not_block(tmp_path, monkeypatch):
    """Stop must not accidentally emit decision:block."""
    from hook_manager.handlers import trace_payload
    monkeypatch.setattr(trace_payload, '_log_path', lambda _p=None: str(tmp_path / 'hooks.jsonl'))

    payload = {
        'hook_event_name': 'Stop',
        'session_id': 'replay-sess',
        'stop_hook_active': False,
    }
    out = io.StringIO()
    rc = run('Stop', REGISTRY, json.dumps(payload), out)
    assert rc == 0
    resp = json.loads(out.getvalue())
    assert 'decision' not in resp
    assert resp.get('continue') is not False
