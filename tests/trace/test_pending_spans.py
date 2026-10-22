"""Unit tests for lib/trace/pending_spans.py — reserved pending-span ids
and the resolved→pending handoff key derivation."""

from __future__ import annotations

from lib.trace.pending_spans import (
    PERM_PENDING_PREFIX,
    PROMPT_PLACEHOLDER_PREFIX,
    TOOL_PENDING_PREFIX,
    gate_key,
    is_pending_span_id,
    pending_id_for_resolved,
    perm_pending_id,
    prompt_placeholder_id,
    tool_pending_id,
    _tool_use_token,
)


def test_prompt_placeholder_id_is_stable_and_prefixed():
    a = prompt_placeholder_id('sess', 'do the thing')
    b = prompt_placeholder_id('sess', 'do the thing')
    assert a == b
    assert a.startswith(PROMPT_PLACEHOLDER_PREFIX)


def test_prompt_placeholder_id_strips_and_prefix_hashes():
    # leading/trailing whitespace ignored; a >512-char tail doesn't change it
    base = prompt_placeholder_id('s', 'hello')
    assert prompt_placeholder_id('s', '  hello  ') == base
    assert prompt_placeholder_id('s', 'hello' + 'x' * 600) != base  # within prefix
    long_a = prompt_placeholder_id('s', 'p' * 512 + 'AAA')
    long_b = prompt_placeholder_id('s', 'p' * 512 + 'BBB')
    assert long_a == long_b  # differ only past the 512-char prefix


def test_prompt_placeholder_id_varies_by_session_and_text():
    assert prompt_placeholder_id('s1', 't') != prompt_placeholder_id('s2', 't')
    assert prompt_placeholder_id('s', 't1') != prompt_placeholder_id('s', 't2')


def test_tool_and_perm_pending_ids():
    tu = 'toolu_0123456789abcdef'
    assert tool_pending_id(tu) == f'{TOOL_PENDING_PREFIX}{_tool_use_token(tu)}'
    assert perm_pending_id(tu) == f'{PERM_PENDING_PREFIX}{_tool_use_token(tu)}'


def test_pending_id_survives_a_shared_prefix():
    """D305: OpenAI-style ids (`chatcmpl-tool-…`) share 13 constant chars, so a
    `[:13]` slice made every call in a session map to `pending-chatcmpl-tool`.
    Hashing the full id keeps parallel pendings distinct."""
    a = 'chatcmpl-tool-0000000000aaaa'
    b = 'chatcmpl-tool-0000000000bbbb'
    assert a[:13] == b[:13] == 'chatcmpl-tool'
    assert tool_pending_id(a) != tool_pending_id(b)
    assert not tool_pending_id(a).endswith('chatcmpl-tool')
    assert len(tool_pending_id(a)) == len(TOOL_PENDING_PREFIX) + 13


def test_is_pending_span_id():
    assert is_pending_span_id('promptlive-abc')
    assert is_pending_span_id('pending-toolu_x')
    assert is_pending_span_id('permreq-toolu_x')
    assert not is_pending_span_id('prompt-uuid123')
    assert not is_pending_span_id('resp-uuid123')
    assert not is_pending_span_id(None)


def test_resolved_prompt_anchor_supersedes_its_placeholder():
    span = {'span_id': 'prompt-abcdef0123456', 'name': 'prompt', 'trace_id': 's'}
    assert pending_id_for_resolved(span, {'text': 'hi'}) == [
        prompt_placeholder_id('s', 'hi')
    ]


def test_resolved_tool_supersedes_pending_and_perm():
    span = {'span_id': 'rand16hex', 'name': 'tool.AskUserQuestion', 'trace_id': 's'}
    got = set(pending_id_for_resolved(span, {'tool_use_id': 'toolu_x'}))
    assert got == {tool_pending_id('toolu_x'), perm_pending_id('toolu_x')}


def test_pending_span_supersedes_nothing():
    # the self-delete guard: a pending insert must not retire another row
    span = {'span_id': tool_pending_id('toolu_x'), 'name': 'tool.X', 'trace_id': 's'}
    assert pending_id_for_resolved(span, {'tool_use_id': 'toolu_x'}) == []


def test_gate_key_agrees_across_the_two_events_describing_one_call():
    """PreToolUse and PermissionRequest hand it the same tool + input in
    different key orders; the digest must not care."""
    assert gate_key('Bash', {'command': 'git status', 'description': 'st'}) \
        == gate_key('Bash', {'description': 'st', 'command': 'git status'})


def test_gate_key_separates_different_calls():
    assert gate_key('Bash', {'command': 'git status'}) \
        != gate_key('Bash', {'command': 'git diff'})
    assert gate_key('Bash', {'command': 'x'}) != gate_key('Read', {'command': 'x'})


def test_gate_key_is_none_when_it_cannot_be_built():
    assert gate_key('Bash', None) is None
    assert gate_key(None, {'command': 'x'}) is None
    assert gate_key('', {}) is None


def test_gate_key_refuses_an_unserializable_input():
    """Tool inputs are arbitrary provider JSON. A value json can't encode must
    degrade to "no key" — never raise into the hook, and never a coerced digest
    that two hook processes would compute differently."""
    assert gate_key('Bash', {'blob': object()}) is None


def test_resolved_with_no_text_or_tool_use_id_supersedes_nothing():
    span = {'span_id': 'resp-x', 'name': 'assistant_response', 'trace_id': 's'}
    assert pending_id_for_resolved(span, {'turn_uuid': 'x'}) == []


def test_subagent_marker_launch_link_supersedes_nothing():
    """The `subagent.start` marker names the `tool.Agent` call that launched it
    under `launch_tool_use_id`, NOT `tool_use_id`. The distinction is the whole
    reason for the name: keyed on the real attribute, the marker would retire
    the launching call's `pending-…` placeholder — killing the running Agent
    card the instant its subagent starts, while the subagent is still working.
    """
    span = {'span_id': 'start-ag123', 'name': 'subagent.start', 'trace_id': 's'}
    attrs = {'agent_id': 'ag123', 'child_trace_id': 'sa-ag123',
             'launch_tool_use_id': 'toolu_01CBd2'}
    assert pending_id_for_resolved(span, attrs) == []
