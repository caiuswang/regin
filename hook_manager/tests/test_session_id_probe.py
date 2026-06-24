"""Tests for the session_id_probe PreToolUse handler.

It does one thing: stamp the live session id into the cache on every Bash call
(read back by the `regin session-id` CLI command). No command rewriting — it
always returns None and never alters the command.
"""

import pytest

from hook_manager.core import HookPayload
from hook_manager.handlers import session_id_probe

SID = 'c6ebadea-922b-4e3d-82fd-cfe0ce6c9a6c'


@pytest.fixture(autouse=True)
def _isolate_cache(tmp_path, monkeypatch):
    """Point the session cache at a temp dir so tests never touch the real one."""
    from lib.settings import settings
    monkeypatch.setattr(settings, 'data_dir', tmp_path, raising=False)
    yield


def _p(command, *, session_id=SID, cwd='/repo'):
    return HookPayload.from_stdin_json('PreToolUse', {
        'hook_event_name': 'PreToolUse',
        'session_id': session_id,
        'cwd': cwd,
        'tool_name': 'Bash',
        'tool_input': {'command': command},
    })


def test_handler_never_rewrites_the_command():
    # Pure recorder: no permission decision, no updated_input — always None.
    assert session_id_probe.handle(_p('regin session-id')) is None
    assert session_id_probe.handle(_p('.venv/bin/python cli/regin.py session-id')) is None
    assert session_id_probe.handle(_p('ls -la')) is None


def test_every_bash_call_stamps_the_cache():
    from lib import session_probe
    assert session_id_probe.handle(_p('ls -la', cwd='/work')) is None
    assert session_probe.resolve(cwd='/work') == SID


def test_full_interpreter_form_resolves_via_cache():
    from lib import session_probe
    # The exact failing case from the bug report: the agent expands the probe
    # to the full interpreter form. The hook stamps; the CLI reads it back.
    session_id_probe.handle(_p('.venv/bin/python cli/regin.py session-id', cwd='/repo'))
    assert session_probe.resolve(cwd='/repo') == SID


def test_nonce_token_is_recorded_and_resolvable():
    from lib import session_probe
    session_id_probe.handle(_p('regin session-id --nonce TK-1', cwd='/elsewhere'))
    assert session_probe.resolve(nonce='TK-1') == SID


def test_skip_when_no_session_id():
    from lib import session_probe
    assert session_id_probe.handle(_p('ls', session_id=None, cwd='/none')) is None
    assert session_probe.resolve(cwd='/none') is None


def test_only_runs_on_bash():
    from hook_manager.registry import REGISTRY
    h = next(x for x in REGISTRY if x.name == 'session_id_probe')
    assert 'PreToolUse' in h.events
    assert h.matches(_p('ls -la'))          # fires on every Bash (cache stamp)
    assert not h.matches(HookPayload.from_stdin_json('PreToolUse', {
        'hook_event_name': 'PreToolUse', 'session_id': SID,
        'tool_name': 'Read', 'tool_input': {}}))
