"""Tests for hook_manager.runner.run — end-to-end stdin→stdout."""

import io
import json

from hook_manager.core import Handler, HookPayload, HookResponse
from hook_manager.runner import run


def _payload(event='PreToolUse', **kw) -> str:
    data = {'hook_event_name': event, 'session_id': 'sess', **kw}
    return json.dumps(data)


def _drain(out: io.StringIO) -> dict:
    text = out.getvalue().strip()
    return json.loads(text) if text else {}


# ── Empty / invalid input ─────────────────────────────────────────────

def test_empty_stdin_emits_default_response():
    # No handlers + empty stdin → dispatch runs on an empty payload.
    # PreToolUse suppressOutput is blocked (harness rejects it), so the
    # default response for this event is a bare {}.
    out = io.StringIO()
    rc = run('PreToolUse', [], '', out)
    assert rc == 0
    assert _drain(out) == {}


def test_malformed_json_stdin_emits_bare_object():
    out = io.StringIO()
    rc = run('PreToolUse', [], 'not json', out)
    assert rc == 0
    assert out.getvalue().strip() == '{}'


# ── Dispatch ──────────────────────────────────────────────────────────

def _always_fn(tag: str):
    def f(p: HookPayload) -> HookResponse:
        return HookResponse(additional_context=tag)
    return f


def test_handlers_run_in_priority_order():
    a = Handler(name='a', events=['PreToolUse'], kind='enrich',
                priority=20, fn=_always_fn('A'))
    b = Handler(name='b', events=['PreToolUse'], kind='enrich',
                priority=10, fn=_always_fn('B'))
    c = Handler(name='c', events=['PreToolUse'], kind='enrich',
                priority=30, fn=_always_fn('C'))

    out = io.StringIO()
    run('PreToolUse', [a, b, c], _payload(), out)
    resp = _drain(out)
    # B (priority=10) should come first, then A (20), then C (30)
    ctx = resp['hookSpecificOutput']['additionalContext']
    assert ctx == 'B\n---\nA\n---\nC'


def test_priority_overrides_take_precedence_over_registry_defaults(tmp_path, monkeypatch):
    """Persisted overrides flip the sort key — `a` (default 20, override 5)
    now runs before `b` (default 10, no override).

    The runner resolves a provider from the payload and passes its id as
    `agent_type` to `priority_overrides()`. We stub `build_provider` so the
    override read lands in our tmp file rather than the real
    ~/.claude/hook-manager-config.json (which is what the runner would hit
    when a real provider is resolved).
    """
    from hook_manager import config as cfg
    fake = tmp_path / 'hook-manager-config.json'

    class _Provider:
        def hook_manager_config_path(self):
            return fake
    monkeypatch.setattr(cfg, 'CONFIG_PATH', str(fake))
    monkeypatch.setattr(cfg, 'build_provider', lambda _pid: _Provider())
    cfg.set_priorities({'a': 5}, agent_type='claude')

    a = Handler(name='a', events=['PreToolUse'], kind='enrich',
                priority=20, fn=_always_fn('A'))
    b = Handler(name='b', events=['PreToolUse'], kind='enrich',
                priority=10, fn=_always_fn('B'))

    out = io.StringIO()
    run('PreToolUse', [a, b], _payload(), out)
    resp = _drain(out)
    # Without the override, B (10) ran first. With override a→5, A runs first.
    assert resp['hookSpecificOutput']['additionalContext'] == 'A\n---\nB'


def test_star_event_handler_fires_on_any_event():
    star = Handler(name='star', events=['*'], kind='trace',
                   priority=10, fn=_always_fn('STAR'))
    specific = Handler(name='specific', events=['PreToolUse'], kind='enrich',
                       priority=20, fn=_always_fn('SPEC'))

    out = io.StringIO()
    run('Stop', [star, specific], _payload(event='Stop'), out)
    resp = _drain(out)
    assert resp['hookSpecificOutput']['additionalContext'] == 'STAR'


def test_non_matching_handler_is_skipped():
    h = Handler(name='only-post', events=['PostToolUse'], kind='enrich',
                priority=10, fn=_always_fn('X'))
    out = io.StringIO()
    run('PreToolUse', [h], _payload(), out)
    resp = _drain(out)
    assert 'hookSpecificOutput' not in resp or 'additionalContext' not in resp.get('hookSpecificOutput', {})


# ── Exception safety ──────────────────────────────────────────────────

def test_handler_exception_is_swallowed():
    def boom(p: HookPayload) -> HookResponse:
        raise RuntimeError('synthetic')
    bad = Handler(name='bad', events=['*'], kind='trace', fn=boom)
    good = Handler(name='good', events=['*'], kind='enrich',
                   priority=200, fn=_always_fn('OK'))

    out = io.StringIO()
    rc = run('PreToolUse', [bad, good], _payload(), out)
    assert rc == 0
    resp = _drain(out)
    assert resp['hookSpecificOutput']['additionalContext'] == 'OK'


def test_predicate_exception_means_no_match():
    def raising_predicate(p: HookPayload) -> bool:
        raise ValueError('boom')
    h = Handler(name='p', events=['*'], kind='trace',
                predicate=raising_predicate, fn=_always_fn('X'))
    out = io.StringIO()
    rc = run('PreToolUse', [h], _payload(), out)
    assert rc == 0
    # Handler didn't run (predicate failed) so no context
    resp = _drain(out)
    assert 'additionalContext' not in resp.get('hookSpecificOutput', {})


# ── Exit-code semantics per spec ──────────────────────────────────────

def test_exit_code_2_honored_for_blockable_event():
    def block(p: HookPayload) -> HookResponse:
        return HookResponse(exit_code=2)
    h = Handler(name='blocker', events=['PreToolUse'], kind='gate', fn=block)
    out = io.StringIO()
    rc = run('PreToolUse', [h], _payload(), out)
    assert rc == 2


def test_exit_code_2_downgraded_on_non_blockable_event():
    def block(p: HookPayload) -> HookResponse:
        return HookResponse(exit_code=2)
    h = Handler(name='blocker', events=['PostToolUse'], kind='gate', fn=block)
    out = io.StringIO()
    rc = run('PostToolUse', [h], _payload(event='PostToolUse'), out)
    assert rc == 0  # PostToolUse is not blockable via exit 2


# ── Unknown event ────────────────────────────────────────────────────

def test_unknown_event_is_logged_but_runs_matching_handlers(monkeypatch):
    logs = []
    def fake_log(handler, event, exc, payload=None):
        logs.append((handler, event, type(exc).__name__))
    monkeypatch.setattr('hook_manager.runner._log_error', fake_log)

    star = Handler(name='star', events=['*'], kind='trace', fn=_always_fn('S'))
    out = io.StringIO()
    rc = run('MadeUpEvent', [star], _payload(event='MadeUpEvent'), out)
    assert rc == 0
    # The handler still runs (['*'] matches any event including unknowns),
    # but the runner should have logged an 'unknown event' warning.
    assert any('MadeUpEvent' in e and 'ValueError' == t for _, e, t in logs)


def test_camel_case_payload_fields_are_normalized():
    """Codex payloads use camelCase keys; dispatch should still work."""
    h = Handler(
        name='read-check',
        events=['PreToolUse'],
        kind='enrich',
        fn=lambda p: HookResponse(
            additional_context=(p.tool_input or {}).get('file_path', '')
        ),
    )
    payload = json.dumps({
        'hookEventName': 'PreToolUse',
        'sessionId': 'sess',
        'toolName': 'Read',
        'toolInput': {'filePath': '/tmp/demo.txt'},
    })
    out = io.StringIO()
    rc = run('PreToolUse', [h], payload, out)
    assert rc == 0
    resp = _drain(out)
    assert resp['hookSpecificOutput']['additionalContext'] == '/tmp/demo.txt'


def test_run_injects_cli_agent_type_when_payload_lacks_it():
    h = Handler(
        name='agent-type',
        events=['SessionStart'],
        kind='trace',
        fn=lambda p: HookResponse(additional_context=p.raw.get('agent_type', '')),
    )
    out = io.StringIO()
    rc = run('SessionStart', [h], _payload(event='SessionStart'), out,
             agent_type='codex')
    assert rc == 0
    resp = _drain(out)
    assert resp['hookSpecificOutput']['additionalContext'] == 'codex'


def test_run_keeps_payload_agent_type_over_cli_default():
    h = Handler(
        name='agent-type',
        events=['SessionStart'],
        kind='trace',
        fn=lambda p: HookResponse(additional_context=p.raw.get('agent_type', '')),
    )
    out = io.StringIO()
    rc = run(
        'SessionStart',
        [h],
        _payload(event='SessionStart', agent_type='claude'),
        out,
        agent_type='codex',
    )
    assert rc == 0
    resp = _drain(out)
    assert resp['hookSpecificOutput']['additionalContext'] == 'claude'


def test_run_filters_handlers_by_cli_agent_type(monkeypatch):
    calls = []

    def fake_filter(handlers, agent_type=None):
        calls.append(agent_type)
        return handlers

    monkeypatch.setattr('hook_manager.runner.filter_enabled', fake_filter)
    h = Handler(
        name='agent-type',
        events=['SessionStart'],
        kind='trace',
        fn=lambda p: HookResponse(additional_context=p.raw.get('agent_type', '')),
    )
    out = io.StringIO()
    rc = run('SessionStart', [h], _payload(event='SessionStart'), out,
             agent_type='codex')
    assert rc == 0
    assert calls == ['codex']
