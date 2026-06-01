"""Unit tests for lib/trace/pending_spans.py — reserved pending-span ids
and the resolved→pending handoff key derivation."""

from __future__ import annotations

from lib.trace.pending_spans import (
    PERM_PENDING_PREFIX,
    PROMPT_PLACEHOLDER_PREFIX,
    TOOL_PENDING_PREFIX,
    is_pending_span_id,
    pending_id_for_resolved,
    perm_pending_id,
    prompt_placeholder_id,
    tool_pending_id,
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
    assert tool_pending_id(tu) == f'{TOOL_PENDING_PREFIX}{tu[:13]}'
    assert perm_pending_id(tu) == f'{PERM_PENDING_PREFIX}{tu[:13]}'


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


def test_resolved_with_no_text_or_tool_use_id_supersedes_nothing():
    span = {'span_id': 'resp-x', 'name': 'assistant_response', 'trace_id': 's'}
    assert pending_id_for_resolved(span, {'turn_uuid': 'x'}) == []
