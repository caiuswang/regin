"""Tests for hook_manager.core."""

from hook_manager.core import (
    BLOCKABLE_VIA_EXIT_2, HookPayload, SPEC_EVENTS,
    always, match_bash_command, match_tool, match_tool_regex,
)


# ── SPEC_EVENTS ───────────────────────────────────────────────────────

def test_spec_events_is_frozen():
    assert isinstance(SPEC_EVENTS, frozenset)
    assert 'PreToolUse' in SPEC_EVENTS
    assert 'SessionEnd' in SPEC_EVENTS
    # Sanity: no phantom event from the legacy hook
    assert 'PreConversation' not in SPEC_EVENTS


def test_blockable_subset_of_spec():
    assert BLOCKABLE_VIA_EXIT_2 <= SPEC_EVENTS


# ── HookPayload.from_stdin_json ───────────────────────────────────────

def test_from_stdin_uses_event_hint_when_missing_in_json():
    p = HookPayload.from_stdin_json('PreToolUse', {})
    assert p.event == 'PreToolUse'


def test_from_stdin_prefers_payload_event_name_over_hint():
    p = HookPayload.from_stdin_json('PreToolUse', {'hook_event_name': 'Stop'})
    assert p.event == 'Stop'


def test_from_stdin_normalizes_none_containers_to_dicts():
    p = HookPayload.from_stdin_json('PostToolUse', {
        'tool_input': None,
        'tool_response': None,
    })
    assert p.tool_input == {}
    assert p.tool_response == {}


def test_prompt_extraction_prefers_top_level_prompt():
    p = HookPayload.from_stdin_json('UserPromptSubmit', {
        'prompt': 'hello',
        'text': 'other',
    })
    assert p.prompt == 'hello'


def test_prompt_extraction_falls_back_to_tool_input_description():
    p = HookPayload.from_stdin_json('PreToolUse', {
        'tool_input': {'description': 'a bash command'},
    })
    assert p.prompt == 'a bash command'


def test_raw_is_preserved_untouched():
    raw = {'hook_event_name': 'Notification', 'weird_field': 123}
    p = HookPayload.from_stdin_json('Notification', raw)
    assert p.raw['hook_event_name'] == 'Notification'
    assert p.raw['weird_field'] == 123


def test_from_stdin_normalizes_camel_case_codex_payload_keys():
    p = HookPayload.from_stdin_json('PreToolUse', {
        'hookEventName': 'PreToolUse',
        'sessionId': 'sess-1',
        'permissionMode': 'default',
        'toolName': 'Read',
        'toolInput': {'filePath': '/tmp/a.txt'},
        'toolResponse': {'ok': True},
        'transcriptPath': '/tmp/transcript.jsonl',
    })
    assert p.event == 'PreToolUse'
    assert p.session_id == 'sess-1'
    assert p.permission_mode == 'default'
    assert p.tool_name == 'Read'
    assert p.tool_input['file_path'] == '/tmp/a.txt'
    assert p.tool_response['ok'] is True
    assert p.raw['transcript_path'] == '/tmp/transcript.jsonl'


# ── Predicates ────────────────────────────────────────────────────────

def _pl(**kw):
    return HookPayload.from_stdin_json('PreToolUse', kw)


def test_match_tool_exact():
    pred = match_tool('Bash', 'Edit')
    assert pred(_pl(tool_name='Bash'))
    assert pred(_pl(tool_name='Edit'))
    assert not pred(_pl(tool_name='Write'))


def test_match_tool_regex():
    pred = match_tool_regex(r'^mcp__.*')
    assert pred(_pl(tool_name='mcp__foo__bar'))
    assert not pred(_pl(tool_name='Bash'))
    # None is safe
    assert not pred(_pl(tool_name=None))


def test_match_bash_command_sub_shell_boundaries():
    pred = match_bash_command(r'(?:^|[\s;&|])mvn(?:\s|$)')
    assert pred(_pl(tool_name='Bash', tool_input={'command': 'mvn clean'}))
    assert pred(_pl(tool_name='Bash', tool_input={'command': 'cd foo && mvn test'}))
    # Not Bash tool -> no match
    assert not pred(_pl(tool_name='Edit', tool_input={'command': 'mvn test'}))
    # mvnw (wrapper) should NOT match
    assert not pred(_pl(tool_name='Bash', tool_input={'command': 'mvnw test'}))


def test_always():
    assert always()(_pl())


# ── Prompt extraction chain ──────────────────────────────────────────

def test_prompt_fallback_order_full_chain():
    """The extractor walks 8 candidates in priority order. Pin the
    exact order so a refactor that reshuffles the list is caught
    instead of silently changing which field wins under collision."""
    # text wins when prompt is absent.
    assert HookPayload.from_stdin_json('PreToolUse', {
        'text': 'from-text', 'message': 'from-message',
    }).prompt == 'from-text'

    # message wins when prompt + text are absent.
    assert HookPayload.from_stdin_json('PreToolUse', {
        'message': 'from-message',
        'tool_input': {'text': 'from-ti-text'},
    }).prompt == 'from-message'

    # tool_input.text wins over tool_input.message.
    assert HookPayload.from_stdin_json('PreToolUse', {
        'tool_input': {'text': 'ti-text', 'message': 'ti-message'},
    }).prompt == 'ti-text'

    # tool_input.prompt wins over tool_input.description.
    assert HookPayload.from_stdin_json('PreToolUse', {
        'tool_input': {'prompt': 'ti-prompt', 'description': 'ti-desc'},
    }).prompt == 'ti-prompt'

    # input is the last-resort fallback.
    assert HookPayload.from_stdin_json('PreToolUse', {
        'input': 'last-resort',
    }).prompt == 'last-resort'


def test_prompt_strips_whitespace():
    """Raw prompts from the TUI often have trailing newlines. The
    extractor returns the stripped form — downstream code pattern-matches
    on exact strings (e.g. `/plan`) that would otherwise miss."""
    assert HookPayload.from_stdin_json('UserPromptSubmit', {
        'prompt': '   hello\n\n',
    }).prompt == 'hello'


def test_prompt_skips_whitespace_only_candidates():
    """A whitespace-only first candidate should not shadow a meaningful
    later one — otherwise a blank `text` field would hide a real
    `message` behind it."""
    p = HookPayload.from_stdin_json('PreToolUse', {
        'prompt': '   \n',
        'text': 'real text',
    })
    assert p.prompt == 'real text'


def test_prompt_ignores_non_string_candidates():
    """Claude Code has occasionally sent numbers or bools in these
    fields when schema validation was loose. We must skip them rather
    than coerce to string (coercion would pollute transcripts with
    `True` / `42` etc.)."""
    p = HookPayload.from_stdin_json('UserPromptSubmit', {
        'prompt': 42,
        'text': True,
        'message': 'real message',
    })
    assert p.prompt == 'real message'


def test_prompt_empty_when_no_candidates():
    """No fields populated → empty string, never None. Downstream code
    does `if p.prompt.startswith('/')` which would crash on None."""
    p = HookPayload.from_stdin_json('UserPromptSubmit', {})
    assert p.prompt == ''


# ── HookPayload: other fields ────────────────────────────────────────

def test_payload_captures_session_cwd_permission_mode():
    """Three top-level fields used by downstream handlers. If any is
    silently dropped, span attribution (cwd), session projection
    (session_id), or permission audits (permission_mode) breaks."""
    p = HookPayload.from_stdin_json('SessionStart', {
        'session_id': 's-abc',
        'cwd': '/srv/project',
        'permission_mode': 'acceptEdits',
    })
    assert p.session_id == 's-abc'
    assert p.cwd == '/srv/project'
    assert p.permission_mode == 'acceptEdits'


# ── match_tool edge cases ────────────────────────────────────────────

def test_match_tool_with_no_names_never_matches():
    """`match_tool()` with no args returns a predicate that matches an
    empty set — used when a handler is scoped via registry but the
    predicate is a defensive no-op. Must return False, not True."""
    pred = match_tool()
    assert pred(_pl(tool_name='Bash')) is False
    assert pred(_pl(tool_name=None)) is False


def test_match_bash_command_non_bash_tool_returns_false():
    pred = match_bash_command(r'rm\s+-rf')
    assert pred(_pl(tool_name='Edit', tool_input={'command': 'rm -rf /'})) is False


def test_match_bash_command_non_string_command():
    """Exotic payloads can ship `command` as a list or None. The
    predicate must safely return False rather than crash on .search()."""
    pred = match_bash_command(r'anything')
    assert pred(_pl(tool_name='Bash', tool_input={'command': None})) is False
    assert pred(_pl(tool_name='Bash', tool_input={})) is False


# ── Handler.matches ──────────────────────────────────────────────────

def test_handler_matches_wildcard_event():
    from hook_manager.core import Handler
    h = Handler(name='h', events=['*'], kind='trace', fn=lambda p: None)
    for ev in ('PreToolUse', 'Stop', 'SomeFutureEvent'):
        assert h.matches(HookPayload.from_stdin_json(ev, {}))


def test_handler_matches_specific_event_only():
    from hook_manager.core import Handler
    h = Handler(name='h', events=['PreToolUse', 'PostToolUse'],
                kind='trace', fn=lambda p: None)
    assert h.matches(HookPayload.from_stdin_json('PreToolUse', {}))
    assert h.matches(HookPayload.from_stdin_json('PostToolUse', {}))
    assert not h.matches(HookPayload.from_stdin_json('Stop', {}))


def test_handler_matches_returns_bool_from_truthy_predicate():
    """A predicate that returns a truthy non-bool (like the count from
    re.search) must be coerced — .matches() is annotated -> bool."""
    from hook_manager.core import Handler
    h = Handler(name='h', events=['*'], kind='trace', fn=lambda p: None,
                predicate=lambda p: 'truthy string')
    result = h.matches(HookPayload.from_stdin_json('PreToolUse', {}))
    assert result is True
    assert isinstance(result, bool)


def test_handler_matches_predicate_exception_is_false():
    """Predicates that raise must not propagate — the runner relies on
    this to keep one bad handler from poisoning dispatch."""
    from hook_manager.core import Handler
    h = Handler(name='h', events=['*'], kind='trace', fn=lambda p: None,
                predicate=lambda p: (_ for _ in ()).throw(RuntimeError('x')))
    assert h.matches(HookPayload.from_stdin_json('PreToolUse', {})) is False
