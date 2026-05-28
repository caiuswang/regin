"""Tests for the denied-AskUserQuestion synthesis path in turn_trace.

When the user denies an AskUserQuestion (or picks "Chat about this"),
Claude Code never fires PostToolUse, so `post_tool_trace.py` writes no
`tool.AskUserQuestion` span. turn_trace synthesises one from the
transcript's `tool_use` + `tool_result(is_error=true)` pair so the trace
UI can show what was asked and what the user said back.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hook_manager.handlers import turn_trace
from lib import hook_plugin
from lib.trace.transcript_usage import read_usage


@pytest.fixture
def _isolated_state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv('REGIN_TURN_TRACE_STATE_DIR', str(tmp_path / 'state'))
    yield


@pytest.fixture
def _captured(monkeypatch):
    spans: list[dict] = []

    def fake_post_span(**kw):
        spans.append(kw)

    def fake_post_event(*_args, **_kwargs):
        pass

    monkeypatch.setattr(hook_plugin, 'post_span', fake_post_span)
    monkeypatch.setattr(hook_plugin, 'post_event', fake_post_event)
    return spans


_QUESTIONS = [{
    'question': 'Should we strip the framework knowledge?',
    'header': 'Cleanup scope',
    'multiSelect': False,
    'options': [
        {'label': 'Narrow', 'description': 'Just one slice', 'preview': 'p1'},
        {'label': 'Wide',   'description': 'Whole layer'},
    ],
}]


def _deny_transcript(tmp_path: Path, *, is_error: bool, result_text: str) -> str:
    p = tmp_path / 'transcript.jsonl'
    entries = [
        {"type": "user", "uuid": "user-1", "parentUuid": None,
         "message": {"content": "do the cleanup"}},
        {
            "type": "assistant",
            "uuid": "asst-1",
            "parentUuid": "user-1",
            "timestamp": "2026-05-15T12:00:00Z",
            "message": {
                "id": "msg-1",
                "model": "claude-opus-4-7",
                "content": [
                    {"type": "text", "text": "I need to ask one scope question."},
                    {"type": "tool_use",
                     "id": "toolu_AAAAAAAAAAAA", "name": "AskUserQuestion",
                     "input": {"questions": _QUESTIONS}},
                ],
                "usage": {"input_tokens": 7, "output_tokens": 25,
                          "cache_read_input_tokens": 0,
                          "cache_creation_input_tokens": 0},
            },
        },
        {
            "type": "user",
            "uuid": "user-2",
            "parentUuid": "asst-1",
            "timestamp": "2026-05-15T12:00:01Z",
            "message": {
                "content": [
                    {"type": "tool_result",
                     "tool_use_id": "toolu_AAAAAAAAAAAA",
                     "is_error": is_error,
                     "content": result_text},
                ],
            },
        },
    ]
    with open(p, 'w') as f:
        for e in entries:
            f.write(json.dumps(e) + '\n')
    return str(p)


def _emit(usage, spans):
    turn_trace._post_live_turn_data(
        trace_id='trace-1',
        turns=usage.turns,
        fallback_model='claude-opus-4-7',
        capture_text=True,
        max_text_bytes=50_000,
        seen=set(),
    )
    return [s for s in spans if s.get('name') == 'tool.AskUserQuestion']


def test_denied_askuser_emits_synth_span_with_questions(tmp_path, _isolated_state_dir, _captured):
    """A tool_result with is_error=true on an AskUserQuestion tool_use must
    produce a synthetic tool.AskUserQuestion span carrying the questions
    (captured from the assistant turn's tool_use.input) and a deterministic
    askdeny-* span_id so re-scans are idempotent."""
    spans = _captured
    deny_text = ('The user doesn\'t want to proceed with this tool use. '
                 'The tool use was rejected.')
    usage = read_usage(_deny_transcript(tmp_path, is_error=True, result_text=deny_text))
    assert usage is not None

    tool_spans = _emit(usage, spans)
    assert len(tool_spans) == 1
    span = tool_spans[0]
    assert span['span_id'] == 'askdeny-toolu_AAAAAAA'
    assert span['status_code'] == 'ERROR'
    attrs = span['attributes']
    assert attrs['tool_name'] == 'AskUserQuestion'
    assert attrs['tool_use_id'] == 'toolu_AAAAAAAAAAAA'
    assert attrs['denied'] is True
    assert attrs['deny_kind'] == 'deny'
    assert attrs['denial_reason'] == deny_text
    assert len(attrs['questions']) == 1
    assert attrs['questions'][0]['question'] == _QUESTIONS[0]['question']
    assert attrs['questions'][0]['header'] == 'Cleanup scope'
    assert attrs['questions'][0]['options'][0] == {
        'label': 'Narrow', 'description': 'Just one slice', 'preview': 'p1',
    }


def test_chat_about_this_is_distinct_deny_kind(tmp_path, _isolated_state_dir, _captured):
    """The "Chat about this" option in Claude Code's permission dialog
    produces a tool_result with a recognisable phrase. We tag it
    `deny_kind='chat'` so the UI can label it differently from a hard
    deny — same data, different reader intent."""
    spans = _captured
    chat_text = ('The user wants to clarify these questions. They may have '
                 'additional context.')
    usage = read_usage(_deny_transcript(tmp_path, is_error=True, result_text=chat_text))
    assert usage is not None

    tool_spans = _emit(usage, spans)
    assert tool_spans[0]['attributes']['deny_kind'] == 'chat'


def test_approved_askuser_emits_no_synth_span(tmp_path, _isolated_state_dir, _captured):
    """An approved AskUserQuestion (is_error=false) leaves the synth path
    silent — post_tool_trace owns the success span. Without this guard
    we'd double-write for every answered question."""
    spans = _captured
    answer_text = 'User has answered your questions: "Q" = "A"'
    usage = read_usage(_deny_transcript(tmp_path, is_error=False, result_text=answer_text))
    assert usage is not None

    assert _emit(usage, spans) == []


# --- Generic non-AskUserQuestion deny path ---------------------------
#
# Permission-deny synth must work for any tool, not just AskUserQuestion.
# These tests exercise an MCP browser_evaluate call denied at the
# permission prompt (matches the real-world session that motivated the
# generalisation: 9ac3ecbe-70d9-45ff-b032-0a5a1e376be3, where a
# `browser_evaluate` deny left no span at all before this change).


_BROWSER_EVAL_INPUT = {
    'function': "async ({page}) => { return await page.title(); }",
}


def _generic_deny_transcript(
    tmp_path: Path,
    *,
    tool_name: str,
    tool_input: dict,
    is_error: bool,
    result_text: str,
) -> str:
    p = tmp_path / 'transcript.jsonl'
    entries = [
        {"type": "user", "uuid": "user-1", "parentUuid": None,
         "message": {"content": "check the page title"}},
        {
            "type": "assistant",
            "uuid": "asst-1",
            "parentUuid": "user-1",
            "timestamp": "2026-05-15T12:00:00Z",
            "message": {
                "id": "msg-1",
                "model": "claude-opus-4-7",
                "content": [
                    {"type": "text", "text": "Running browser_evaluate."},
                    {"type": "tool_use",
                     "id": "toolu_BBBBBBBBBBBB", "name": tool_name,
                     "input": tool_input},
                ],
                "usage": {"input_tokens": 5, "output_tokens": 18,
                          "cache_read_input_tokens": 0,
                          "cache_creation_input_tokens": 0},
            },
        },
        {
            "type": "user",
            "uuid": "user-2",
            "parentUuid": "asst-1",
            "timestamp": "2026-05-15T12:00:01Z",
            "message": {
                "content": [
                    {"type": "tool_result",
                     "tool_use_id": "toolu_BBBBBBBBBBBB",
                     "is_error": is_error,
                     "content": result_text},
                ],
            },
        },
    ]
    with open(p, 'w') as f:
        for e in entries:
            f.write(json.dumps(e) + '\n')
    return str(p)


def _emit_generic(usage, spans, tool_name: str):
    turn_trace._post_live_turn_data(
        trace_id='trace-2',
        turns=usage.turns,
        fallback_model='claude-opus-4-7',
        capture_text=True,
        max_text_bytes=50_000,
        seen=set(),
    )
    return [s for s in spans if s.get('name') == f'tool.{tool_name}']


def test_denied_browser_evaluate_emits_synth_span(tmp_path, _isolated_state_dir, _captured):
    """A user-denied MCP tool (here: browser_evaluate) leaves no
    PostToolUse trail. The synth path must produce one
    `tool.<full-name>` span carrying the input + the denial reason +
    a tooldeny-* id (distinct prefix from AskUserQuestion's askdeny-*
    so the per-tool backfill rows never collide)."""
    spans = _captured
    deny_text = ("The user doesn't want to proceed with this tool use. "
                 "The tool use was rejected (eg. if it was a file edit "
                 "that the user didn't allow).")
    tool_name = 'mcp__plugin_playwright_playwright__browser_evaluate'
    usage = read_usage(_generic_deny_transcript(
        tmp_path, tool_name=tool_name, tool_input=_BROWSER_EVAL_INPUT,
        is_error=True, result_text=deny_text,
    ))
    assert usage is not None

    tool_spans = _emit_generic(usage, spans, tool_name)
    assert len(tool_spans) == 1
    span = tool_spans[0]
    assert span['span_id'] == 'tooldeny-toolu_BBBBBBB'
    assert span['status_code'] == 'ERROR'
    attrs = span['attributes']
    assert attrs['tool_name'] == tool_name
    assert attrs['tool_use_id'] == 'toolu_BBBBBBBBBBBB'
    assert attrs['denied'] is True
    assert attrs['deny_kind'] == 'deny'
    assert attrs['denial_reason'].startswith("The user doesn't want to proceed")
    assert attrs['tool_input'] == _BROWSER_EVAL_INPUT
    # Generic deny must NOT carry the AskUserQuestion-only `questions`
    # field — that would confuse the AskUserQuestion-specific renderer.
    assert 'questions' not in attrs


def test_non_deny_tool_error_does_not_synth_span(tmp_path, _isolated_state_dir, _captured):
    """A normal tool failure (Bash exit code, EISDIR, etc.) already
    gets a `tool.failure` span via PostToolUseFailure. The synth path
    must skip these — its sentinel filter looks for the literal
    permission-deny phrase, not any is_error=true result. Without
    this guard we'd double-write a span for every failed Bash run."""
    spans = _captured
    error_text = "EISDIR: illegal operation on a directory, read '/some/dir'"
    tool_name = 'Read'
    usage = read_usage(_generic_deny_transcript(
        tmp_path, tool_name=tool_name, tool_input={'file_path': '/some/dir'},
        is_error=True, result_text=error_text,
    ))
    assert usage is not None

    assert _emit_generic(usage, spans, tool_name) == []


def test_denied_tool_with_file_path_carries_file_path(tmp_path, _isolated_state_dir, _captured):
    spans = _captured
    deny_text = ("The user doesn't want to proceed with this tool use. "
                 "The tool use was rejected.")
    tool_name = 'Write'
    usage = read_usage(_generic_deny_transcript(
        tmp_path,
        tool_name=tool_name,
        tool_input={'file_path': '/some/file.json', 'content': '{}'},
        is_error=True,
        result_text=deny_text,
    ))
    assert usage is not None

    tool_spans = _emit_generic(usage, spans, tool_name)
    assert len(tool_spans) == 1
    assert tool_spans[0]['attributes']['file_path'] == '/some/file.json'


# --- Pre-execution tool rejections (<tool_use_error> envelope) -------
#
# Same shape of problem as permission deny: PostToolUse never fires for
# these, so without a synth path the trace UI drops the call entirely.
# The triggering session was d9ecffc4-edfd-4f3f-9c1e-04518f96a1f0,
# where a Write tool_use hit Claude Code's Read-before-Write guard and
# left no `tool.Write` row.


def test_tool_use_error_envelope_emits_synth_span(tmp_path, _isolated_state_dir, _captured):
    """The exact failure from the d9ecffc4 trace: a Write call with
    no prior Read returns a tool_result wrapped in
    `<tool_use_error>…</tool_use_error>`. PostToolUse never fires; the
    synth path produces a `tool.Write` span with the inner reason and
    a `toolerr-*` id (distinct prefix from tooldeny- so the two
    categories never collide on the same tool_use_id)."""
    spans = _captured
    envelope = ('<tool_use_error>File has not been read yet. '
                'Read it first before writing to it.</tool_use_error>')
    tool_name = 'Write'
    tool_input = {'file_path': '/some/file.json', 'content': '{}'}
    usage = read_usage(_generic_deny_transcript(
        tmp_path, tool_name=tool_name, tool_input=tool_input,
        is_error=True, result_text=envelope,
    ))
    assert usage is not None

    tool_spans = _emit_generic(usage, spans, tool_name)
    assert len(tool_spans) == 1
    span = tool_spans[0]
    assert span['span_id'] == 'toolerr-toolu_BBBBBBB'
    assert span['status_code'] == 'ERROR'
    attrs = span['attributes']
    assert attrs['tool_name'] == tool_name
    assert attrs['tool_use_id'] == 'toolu_BBBBBBBBBBBB'
    assert attrs['rejected'] is True
    assert attrs['reject_kind'] == 'tool_use_error'
    assert attrs['file_path'] == '/some/file.json'
    # Envelope tags stripped from the reason — the UI shows the
    # human-readable message, not the wrapper.
    assert attrs['reject_reason'] == (
        'File has not been read yet. Read it first before writing to it.'
    )
    assert attrs['tool_input'] == tool_input


def test_tool_use_error_extracts_file_path_from_truncated_preview(tmp_path, _isolated_state_dir, _captured):
    spans = _captured
    envelope = ('<tool_use_error>File has not been read yet. '
                'Read it first before writing to it.</tool_use_error>')
    tool_name = 'Write'
    huge_content = 'x' * (20 * 1024)
    usage = read_usage(_generic_deny_transcript(
        tmp_path,
        tool_name=tool_name,
        tool_input={'file_path': '/some/large/file.json', 'content': huge_content},
        is_error=True,
        result_text=envelope,
    ))
    assert usage is not None

    tool_spans = _emit_generic(usage, spans, tool_name)
    assert len(tool_spans) == 1
    attrs = tool_spans[0]['attributes']
    assert attrs['file_path'] == '/some/large/file.json'
    assert attrs['tool_input']['__truncated'] is True


def test_tool_use_error_does_not_double_synth_with_deny(tmp_path, _isolated_state_dir, _captured):
    """A permission-deny tool_result text doesn't accidentally trip
    the tool_use_error path (different sentinels, different prefixes)
    — we'd otherwise mint two synth spans for the same tool_use_id."""
    spans = _captured
    deny_text = ("The user doesn't want to proceed with this tool use. "
                 "The tool use was rejected.")
    tool_name = 'Write'
    usage = read_usage(_generic_deny_transcript(
        tmp_path, tool_name=tool_name, tool_input={'file_path': '/x'},
        is_error=True, result_text=deny_text,
    ))
    assert usage is not None

    tool_spans = _emit_generic(usage, spans, tool_name)
    assert len(tool_spans) == 1
    assert tool_spans[0]['span_id'].startswith('tooldeny-')
