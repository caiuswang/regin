"""Tests for the session_id_probe PreToolUse handler.

A bare `regin session-id` Bash command is rewritten to echo the live session
id, so the agent can read its own id off stdout and compose with it.
"""

from hook_manager.core import HookPayload
from hook_manager.handlers import session_id_probe

SID = 'c6ebadea-922b-4e3d-82fd-cfe0ce6c9a6c'


def _p(command, *, session_id=SID):
    return HookPayload.from_stdin_json('PreToolUse', {
        'hook_event_name': 'PreToolUse',
        'session_id': session_id,
        'tool_name': 'Bash',
        'tool_input': {'command': command},
    })


def test_probe_rewritten_to_echo_session_id():
    r = session_id_probe.handle(_p('regin session-id'))
    assert r.permission_decision == 'allow'
    assert r.updated_input['command'] == f"printf '%s\\n' {SID}"


def test_hyphen_and_spacing_variants_match():
    for cmd in ('regin-session-id', '  regin session id  ', 'REGIN SESSION-ID'):
        assert session_id_probe.handle(_p(cmd)) is not None


def test_does_not_touch_real_commands():
    # probe embedded in a larger command is NOT rewritten — agent composes itself
    assert session_id_probe.handle(_p('SID=$(regin session-id)')) is None
    assert session_id_probe.handle(_p('regin session-id --verbose')) is None
    assert session_id_probe.handle(_p('echo regin session-id')) is None


def test_skip_when_no_session_id():
    assert session_id_probe.handle(_p('regin session-id', session_id=None)) is None


def test_registered_as_pretooluse_gate():
    from hook_manager.registry import REGISTRY
    h = next(x for x in REGISTRY if x.name == 'session_id_probe')
    assert h.kind == 'gate' and 'PreToolUse' in h.events
    assert h.matches(_p('regin session-id'))
    assert not h.matches(_p('regin goal feedback "g"'))
