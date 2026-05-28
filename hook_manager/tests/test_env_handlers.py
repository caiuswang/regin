"""Tests for iteration-2-continuation event handlers.

Per the silent-trace policy (commit fa3922e), most handlers return
`HookResponse(suppress_output=True)` with no additional_context. They
emit trace spans instead so the trace DB retains the data. Handlers still
allowed to emit additional_context (per fa3922e's actionable-info list):
post_tool_failure, file_changed (env-file re-export info only).
"""

import pytest

from hook_manager.core import HookPayload
from hook_manager.handlers import cwd_changed, file_changed, permission_events, post_tool_failure


def _p(event, **kw):
    return HookPayload.from_stdin_json(event, {'hook_event_name': event, **kw})


@pytest.fixture
def captured_spans(monkeypatch):
    import lib.hook_plugin as hp
    spans: list[dict] = []
    monkeypatch.setattr(hp, 'post_span', lambda **kw: spans.append(kw))
    return spans


# ── post_tool_failure (still emits additional_context per policy) ─────

def test_post_tool_failure_basic(captured_spans):
    r = post_tool_failure.handle(_p('PostToolUseFailure', session_id='s1',
        tool_name='Bash', error='command not found: foo'))
    assert r and 'tool-failure: Bash' in r.additional_context
    assert "error='command not found: foo'" in r.additional_context
    # Span also emitted with ERROR status
    assert captured_spans[0]['name'] == 'tool.failure'
    assert captured_spans[0]['status_code'] == 'ERROR'
    assert captured_spans[0]['attributes']['error'] == 'command not found: foo'


def test_post_tool_failure_keeps_full_error_truncates_context_only(captured_spans):
    """The model-facing additional_context stays short (≤200 chars + …),
    but the span attribute holds the full traceback so the trace UI can
    show it. Only errors longer than _ERROR_MAX (16 KB) get truncated on
    the span, with a marker recording the dropped byte count."""
    r = post_tool_failure.handle(_p('PostToolUseFailure', session_id='s1',
        tool_name='Bash', error='x' * 500))
    assert r and '…' in r.additional_context
    assert len(r.additional_context) < 300
    # Span gets the full error — no ellipsis, no truncation marker.
    assert captured_spans[0]['attributes']['error'] == 'x' * 500
    assert 'error_truncated_bytes' not in captured_spans[0]['attributes']


def test_post_tool_failure_truncates_error_above_cap(captured_spans):
    huge = 'x' * (post_tool_failure._ERROR_MAX + 777)
    post_tool_failure.handle(_p('PostToolUseFailure', session_id='s1',
        tool_name='Bash', error=huge))
    attrs = captured_spans[0]['attributes']
    assert len(attrs['error']) == post_tool_failure._ERROR_MAX
    assert attrs['error_truncated_bytes'] == 777


def test_post_tool_failure_captures_bash_command(captured_spans):
    """A Bash failure span must carry the command that triggered it,
    otherwise the trace UI shows only the error with no context for
    what was attempted."""
    long_cmd = 'echo hi && ' + ('x' * 500)
    post_tool_failure.handle(_p('PostToolUseFailure', session_id='s1',
        tool_name='Bash',
        tool_input={'command': long_cmd},
        error='boom'))
    attrs = captured_spans[0]['attributes']
    assert attrs['command_preview'].startswith('echo hi &&')
    assert attrs['command_preview'].endswith('…')
    assert attrs['command'] == long_cmd


def test_post_tool_failure_captures_file_path_for_edit(captured_spans):
    post_tool_failure.handle(_p('PostToolUseFailure', session_id='s1',
        tool_name='Edit',
        tool_input={'file_path': '/tmp/broken.py', 'old_string': 'a', 'new_string': 'b'},
        error='no such file'))
    attrs = captured_spans[0]['attributes']
    assert attrs['file_path'] == '/tmp/broken.py'
    assert 'command' not in attrs


def test_post_tool_failure_flags_user_interrupt(captured_spans):
    r = post_tool_failure.handle(_p('PostToolUseFailure', session_id='s1',
        tool_name='Bash', error='', is_interrupt=True))
    assert r and '(user interrupt)' in r.additional_context
    assert captured_spans[0]['attributes']['is_interrupt'] is True


def test_post_tool_failure_preserves_tool_use_id(captured_spans):
    """The failure span must carry Anthropic's tool_use_id so it can be
    correlated with the assistant_response.tool_calls entry and grafted
    under the right prompt — mirrors post_tool_trace's success path."""
    post_tool_failure.handle(_p('PostToolUseFailure', session_id='s1',
        tool_name='Bash',
        tool_input={'command': 'false'},
        tool_use_id='toolu_01ABCdef',
        error='Exit code 1'))
    assert captured_spans[0]['attributes']['tool_use_id'] == 'toolu_01ABCdef'


# ── permission_events (span-only now) ────────────────────────────────

def test_permission_request_logs_tool(captured_spans):
    permission_events.handle_request(_p('PermissionRequest', session_id='s1',
                                        tool_name='Edit'))
    assert captured_spans[0]['name'] == 'permission.request'
    assert captured_spans[0]['attributes']['tool_name'] == 'Edit'


def test_permission_denied_includes_reason(captured_spans):
    permission_events.handle_denied(_p('PermissionDenied', session_id='s1',
        tool_name='Bash', reason='auto-mode said no'))
    s = captured_spans[0]
    assert s['name'] == 'permission.denied'
    assert s['status_code'] == 'ERROR'
    assert s['attributes']['tool_name'] == 'Bash'
    assert s['attributes']['reason'] == 'auto-mode said no'


def test_permission_denied_accepts_message_alias(captured_spans):
    """Claude Code sends the denial explanation as either `reason` or
    `message`. The handler aliases to a unified `reason` attribute so
    dashboards don't need two code paths."""
    permission_events.handle_denied(_p('PermissionDenied', session_id='s1',
        tool_name='Edit', message='file is read-only'))
    assert captured_spans[0]['attributes']['reason'] == 'file is read-only'


def test_permission_denied_truncates_long_reason(captured_spans):
    """Reasons get clamped to 500 chars — otherwise a multi-KB stderr
    dump attached to a denial would bloat every span it produces."""
    long = 'x' * 800
    permission_events.handle_denied(_p('PermissionDenied', session_id='s1',
        tool_name='Bash', reason=long))
    reason = captured_spans[0]['attributes']['reason']
    assert len(reason) == 500
    assert reason == 'x' * 500


def test_permission_handlers_return_suppress_output():
    """Silent-trace policy: permission handlers post spans but return
    suppress_output=True + no additional_context. Denial propagates
    through the tool's error response, not the hook context."""
    for fn, ev in [
        (permission_events.handle_request, 'PermissionRequest'),
        (permission_events.handle_denied, 'PermissionDenied'),
    ]:
        r = fn(_p(ev, session_id='s1', tool_name='Bash'))
        assert r is not None
        assert r.suppress_output is True
        assert r.additional_context is None


def test_permission_request_missing_tool_name_posts_span(captured_spans):
    """An empty payload should still emit a span marking the boundary —
    just without the `tool_name` attribute. Never crash on missing fields."""
    permission_events.handle_request(_p('PermissionRequest', session_id='s1'))
    s = captured_spans[0]
    assert s['name'] == 'permission.request'
    assert 'tool_name' not in s['attributes']


def test_permission_request_captures_askuserquestion_content(captured_spans):
    """When the user denies AskUserQuestion, no PostToolUse fires — so the
    `tool.AskUserQuestion` span (which normally carries the questions) is
    never written. The permission.request span is the only trace artifact,
    so it must capture the question text + options so the trace UI can still
    show what the agent asked."""
    permission_events.handle_request(_p('PermissionRequest', session_id='s1',
        tool_name='AskUserQuestion',
        tool_use_id='toolu_01ABCdef',
        tool_input={'questions': [{
            'question': 'How aggressively should we strip framework knowledge?',
            'header': 'Cleanup scope',
            'multiSelect': False,
            'options': [
                {'label': 'Narrow', 'description': 'Just Dubbo', 'preview': 'preview-1'},
                {'label': 'Wide',   'description': 'All of it'},
            ],
        }]}))
    s = captured_spans[0]
    assert s['name'] == 'permission.request'
    assert s['attributes']['tool_name'] == 'AskUserQuestion'
    assert s['attributes']['tool_use_id'] == 'toolu_01ABCdef'
    qs = s['attributes']['questions']
    assert len(qs) == 1
    assert qs[0]['question'] == 'How aggressively should we strip framework knowledge?'
    assert qs[0]['header'] == 'Cleanup scope'
    assert qs[0]['multiSelect'] is False
    assert qs[0]['options'][0] == {'label': 'Narrow', 'description': 'Just Dubbo', 'preview': 'preview-1'}
    assert qs[0]['options'][1] == {'label': 'Wide', 'description': 'All of it'}


def test_permission_request_skips_questions_for_non_askuser_tools(captured_spans):
    permission_events.handle_request(_p('PermissionRequest', session_id='s1',
        tool_name='Bash', tool_input={'command': 'rm -rf /'}))
    assert 'questions' not in captured_spans[0]['attributes']


def test_permission_handlers_swallow_emit_errors(monkeypatch):
    """Like every trace emitter, these must survive a dead ingest
    endpoint: no exception leaks out of handle_*, regardless of what
    post_span does."""
    def _boom(**_kw):
        raise RuntimeError('ingest unreachable')
    import lib.hook_plugin as hp
    monkeypatch.setattr(hp, 'post_span', _boom)

    r1 = permission_events.handle_request(_p('PermissionRequest', session_id='s1'))
    r2 = permission_events.handle_denied(_p('PermissionDenied', session_id='s1'))
    for r in (r1, r2):
        assert r is not None
        assert r.suppress_output is True


# ── file_changed ─────────────────────────────────────────────────────

def test_file_changed_plain_file(captured_spans):
    # Plain files are silent in the transcript but DO post a span.
    r = file_changed.handle(_p('FileChanged', session_id='s1',
                               file_path='/tmp/foo.txt'))
    assert r is not None
    assert r.additional_context is None  # silent for non-env files
    s = captured_spans[0]
    assert s['name'] == 'file.changed'
    assert s['attributes']['basename'] == 'foo.txt'
    assert s['attributes']['is_env_file'] is False


def test_file_changed_env_file_reexports(captured_spans, tmp_path, monkeypatch):
    env_src = tmp_path / '.env'
    env_src.write_text(
        'FOO=bar\n'
        '# comment\n'
        'BAZ="quoted value"\n'
        'MALFORMED LINE\n'
    )
    env_out = tmp_path / 'reexport.sh'
    monkeypatch.setenv('CLAUDE_ENV_FILE', str(env_out))

    r = file_changed.handle(_p('FileChanged', session_id='s1',
                               file_path=str(env_src)))
    assert r and 're-exported 2 var(s)' in r.additional_context
    content = env_out.read_text()
    assert 'export FOO=bar' in content
    assert 'export BAZ=quoted value' in content
    assert 'MALFORMED' not in content
    # Span carries is_env_file + reexported count
    s = captured_spans[0]
    assert s['attributes']['is_env_file'] is True
    assert s['attributes']['reexported_vars'] == 2


def test_file_changed_env_file_without_env_var(captured_spans, tmp_path, monkeypatch):
    env_src = tmp_path / '.env'
    env_src.write_text('FOO=bar\n')
    monkeypatch.delenv('CLAUDE_ENV_FILE', raising=False)
    r = file_changed.handle(_p('FileChanged', session_id='s1',
                               file_path=str(env_src)))
    # Still returns context but reports 0 re-exports
    assert r and 're-exported 0 var(s)' in r.additional_context
    assert captured_spans[0]['attributes']['reexported_vars'] == 0


def test_file_changed_skips_missing_path(captured_spans):
    r = file_changed.handle(_p('FileChanged', file_path=''))
    assert r is None
    assert captured_spans == []


def test_file_changed_recognizes_dotted_env_variants(captured_spans, tmp_path, monkeypatch):
    """`.env.local`, `.env.production`, `.envrc` are all env files per
    the handler's `startswith('.env.')` rule. A refactor that tightened
    the match to exact `.env` would silently stop re-exporting from
    per-environment files."""
    monkeypatch.setenv('CLAUDE_ENV_FILE', str(tmp_path / 'out.sh'))
    for name in ('.env.local', '.env.production', '.envrc'):
        src = tmp_path / name
        src.write_text(f'{name.upper().replace(".", "_").strip("_")}=1\n')
        r = file_changed.handle(_p('FileChanged', session_id='s1',
                                   file_path=str(src)))
        assert r is not None
        assert 'file-changed: env file' in r.additional_context
        assert name in r.additional_context


def test_file_changed_does_not_treat_env_shaped_name_as_env(captured_spans, tmp_path):
    """`env.sh` and `myenv.txt` look env-ish but are NOT dotfiles.
    They must be treated as plain files: silent trace, no re-export,
    no additional_context."""
    fake = tmp_path / 'env.sh'
    fake.write_text('FOO=bar\n')
    r = file_changed.handle(_p('FileChanged', session_id='s1',
                               file_path=str(fake)))
    assert r is not None
    assert r.additional_context is None
    assert captured_spans[0]['attributes']['is_env_file'] is False


def test_file_changed_env_handles_whitespace_and_empty_values(captured_spans, tmp_path, monkeypatch):
    """Env parsers vary; ours is intentionally forgiving:
      - leading/trailing whitespace around key/value stripped
      - empty values (KEY=) accepted
      - non-identifier keys (FOO-BAR=) rejected
    These behaviors are load-bearing for real .env files from various tools."""
    env_src = tmp_path / '.env'
    env_src.write_text(
        '   FOO   =   bar   \n'
        'EMPTY=\n'
        'FOO-BAR=rejected\n'          # non-identifier key
        '=alsorejected\n'             # no key
        'SOLO_LINE\n'                 # no =
    )
    env_out = tmp_path / 'out.sh'
    monkeypatch.setenv('CLAUDE_ENV_FILE', str(env_out))
    r = file_changed.handle(_p('FileChanged', session_id='s1',
                               file_path=str(env_src)))
    assert r is not None
    # FOO and EMPTY should be re-exported; three bad lines ignored.
    assert 're-exported 2 var(s)' in r.additional_context
    content = env_out.read_text()
    assert 'export FOO=bar' in content
    assert 'export EMPTY=' in content
    assert 'FOO-BAR' not in content
    assert 'SOLO_LINE' not in content


# ── cwd_changed ──────────────────────────────────────────────────────

def test_cwd_changed_reports_new_dir(captured_spans):
    cwd_changed.handle(_p('CwdChanged', session_id='s1', cwd='/tmp/new-dir'))
    s = captured_spans[0]
    assert s['name'] == 'cwd.changed'
    assert s['attributes']['cwd'] == '/tmp/new-dir'
