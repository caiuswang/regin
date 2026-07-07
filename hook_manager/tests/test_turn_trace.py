"""Tests for turn_trace handler.

The handler fires on UserPromptSubmit + SessionEnd and extracts the
latest turn's model from the transcript jsonl file Claude Code writes.
Firing on Stop is racy — Claude hasn't always flushed the assistant
entry by then; UserPromptSubmit and SessionEnd both land *after* the
previous turn is fully written. It's the only path the rollup has to
catch `/model` switches mid-session — Claude Code doesn't fire any
standalone hook event for /model.
"""

from __future__ import annotations

import json

import pytest

from hook_manager.core import HookPayload
from hook_manager.handlers import turn_trace


def _p(event, **kw):
    return HookPayload.from_stdin_json(event, {'hook_event_name': event, **kw})


@pytest.fixture
def captured_spans(monkeypatch):
    import lib.hook_plugin as hp
    spans: list[dict] = []

    def _capture(**kw):
        spans.append(kw)
        # Real post_span returns True on a 2xx; the seen-uuid cache mark
        # depends on it, so the fake has to mirror the success contract
        # or the throttle test will think every post failed.
        return True

    monkeypatch.setattr(hp, 'post_span', _capture)
    return spans


def _write_transcript(path, entries):
    """Write a list of entries as a jsonl file using the compact
    no-whitespace separators that real Claude Code transcripts use.
    Some handler substring filters (e.g. `"subtype":"local_command"`)
    are calibrated to that on-disk form."""
    path.write_text('\n'.join(json.dumps(e, separators=(',', ':')) for e in entries) + '\n')


# ── Happy path ──────────────────────────────────────────────────────

def test_extracts_model_from_last_assistant_turn(captured_spans, tmp_path):
    transcript = tmp_path / 'session.jsonl'
    _write_transcript(transcript, [
        {'type': 'user', 'message': {'content': 'hi'}},
        {'type': 'assistant', 'message': {
            'model': 'claude-haiku-4-5-20251001',
            'content': [{'type': 'text', 'text': 'HI'}],
        }},
    ])
    turn_trace.handle(_p('UserPromptSubmit', session_id='s1',
                         transcript_path=str(transcript)))
    assert len(captured_spans) == 1
    s = captured_spans[0]
    assert s['name'] == 'turn'
    assert s['attributes']['model'] == 'claude-haiku-4-5-20251001'
    assert s['trace_id'] == 's1'


def test_walks_backward_to_find_assistant_entry(captured_spans, tmp_path):
    """When the transcript ends with user/system entries (e.g. after
    `/exit` writes a slash-command record), the handler walks back
    through preceding lines to the last `type=assistant` entry. Picking
    the file's final line naïvely would produce no model."""
    transcript = tmp_path / 'session.jsonl'
    _write_transcript(transcript, [
        {'type': 'user', 'message': {'content': 'hi'}},
        {'type': 'assistant', 'message': {
            'model': 'claude-sonnet-4-6',
            'content': [{'type': 'text', 'text': 'HI'}],
        }},
        {'type': 'user', 'message': {'content': '/exit'}},   # slash command record
        {'type': 'user', 'message': {'content': 'Catch you later!'}},
    ])
    turn_trace.handle(_p('UserPromptSubmit', session_id='s1',
                         transcript_path=str(transcript)))
    assert captured_spans[0]['attributes']['model'] == 'claude-sonnet-4-6'


def test_picks_most_recent_model_when_multiple_turns(captured_spans, tmp_path):
    """If the session switched model mid-flight (via /model), the
    transcript has multiple assistant entries with different models.
    The handler must return the LAST one."""
    transcript = tmp_path / 'session.jsonl'
    _write_transcript(transcript, [
        {'type': 'assistant', 'message': {'model': 'claude-haiku-4-5-20251001'}},
        {'type': 'assistant', 'message': {'model': 'claude-sonnet-4-6'}},
    ])
    turn_trace.handle(_p('UserPromptSubmit', session_id='s1',
                         transcript_path=str(transcript)))
    assert captured_spans[0]['attributes']['model'] == 'claude-sonnet-4-6'


# ── Defensive paths: no span emitted when info is missing/unreadable ─

def test_no_span_when_transcript_path_missing(captured_spans):
    """Stop payload may not include transcript_path — don't crash, don't
    emit a span with empty model."""
    turn_trace.handle(_p('SessionEnd', session_id='s1'))
    assert captured_spans == []


def test_no_span_when_transcript_file_does_not_exist(captured_spans, tmp_path):
    """Path present but file deleted/not yet flushed — also a no-op."""
    turn_trace.handle(_p('UserPromptSubmit', session_id='s1',
                         transcript_path=str(tmp_path / 'never.jsonl')))
    assert captured_spans == []


def test_no_span_when_transcript_has_no_assistant_entries(captured_spans, tmp_path):
    transcript = tmp_path / 'session.jsonl'
    _write_transcript(transcript, [
        {'type': 'user', 'message': {'content': 'hi'}},
        {'type': 'system', 'message': {'content': 'sys'}},
    ])
    turn_trace.handle(_p('UserPromptSubmit', session_id='s1',
                         transcript_path=str(transcript)))
    assert captured_spans == []


def test_no_span_when_assistant_entry_has_no_model(captured_spans, tmp_path):
    """An assistant entry without a model field (unexpected schema,
    partial write) must not produce an empty-string span."""
    transcript = tmp_path / 'session.jsonl'
    _write_transcript(transcript, [
        {'type': 'assistant', 'message': {
            'content': [{'type': 'text', 'text': 'HI'}],
        }},
    ])
    turn_trace.handle(_p('UserPromptSubmit', session_id='s1',
                         transcript_path=str(transcript)))
    assert captured_spans == []


def test_tolerates_malformed_jsonl_lines(captured_spans, tmp_path):
    """If one line is bad JSON (partial flush, concurrent writer), the
    handler must skip it and keep looking. The valid assistant entry
    before it should still surface."""
    transcript = tmp_path / 'session.jsonl'
    transcript.write_text(
        json.dumps({'type': 'assistant', 'message': {
            'model': 'claude-opus-4-7',
            'content': [{'type': 'text', 'text': 'OK'}],
        }}) + '\n'
        '{this is not valid json\n'  # malformed tail
    )
    turn_trace.handle(_p('UserPromptSubmit', session_id='s1',
                         transcript_path=str(transcript)))
    assert captured_spans[0]['attributes']['model'] == 'claude-opus-4-7'


def test_response_is_silent_trace(captured_spans, tmp_path):
    """Return `suppress_output=True` with no `additional_context` — the
    transcript itself is the model source, no need to echo into the
    transcript a second time."""
    transcript = tmp_path / 'session.jsonl'
    _write_transcript(transcript, [
        {'type': 'assistant', 'message': {'model': 'claude-haiku-4-5-20251001'}},
    ])
    r = turn_trace.handle(_p('SessionEnd', session_id='s1',
                             transcript_path=str(transcript)))
    assert r is not None
    assert r.suppress_output is True
    assert r.additional_context is None


def test_swallows_post_span_exceptions(monkeypatch, tmp_path):
    """An unreachable ingest must not crash the Stop pipeline. Hook
    returns its silent response regardless."""
    transcript = tmp_path / 'session.jsonl'
    _write_transcript(transcript, [
        {'type': 'assistant', 'message': {'model': 'claude-haiku-4-5-20251001'}},
    ])

    def _boom(**_kw):
        raise RuntimeError('ingest unreachable')
    import lib.hook_plugin as hp
    monkeypatch.setattr(hp, 'post_span', _boom)

    r = turn_trace.handle(_p('SessionEnd', session_id='s1',
                             transcript_path=str(transcript)))
    assert r is not None
    assert r.suppress_output is True


# ── Large-file behavior ──────────────────────────────────────────────

# ── assistant_response spans ────────────────────────────────────────

def _assistant_with_usage(*, msg_id, model='claude-opus-4-7', text='hello',
                          uuid='turn-uuid-1234567890abc',
                          parent_uuid=None, ts='2026-04-27T12:00:00Z',
                          extra_blocks=None):
    """Helper: a real assistant entry with usage + text content."""
    content = [{'type': 'text', 'text': text}]
    if extra_blocks:
        content.extend(extra_blocks)
    return {
        'type': 'assistant',
        'uuid': uuid,
        'parentUuid': parent_uuid,
        'timestamp': ts,
        'requestId': 'req-' + msg_id,
        'message': {
            'id': msg_id,
            'model': model,
            'content': content,
            'usage': {
                'input_tokens': 100,
                'output_tokens': 20,
                'cache_creation_input_tokens': 0,
                'cache_read_input_tokens': 0,
            },
        },
    }


def test_emits_assistant_response_span_with_text_and_parent_link(
    captured_spans, tmp_path,
):
    """A real turn (with usage) should produce an assistant_response
    span carrying the response text, a deterministic span_id, and a
    write-time parent_id pointing at the turn's prompt anchor
    (`prompt-<prompt_uuid[:13]>`) — so the read path nests it without
    chronological grafting."""
    transcript = tmp_path / 'session.jsonl'
    user_uuid = 'user-uuid-abcdef0123456'
    turn_uuid = 'asst-uuid-9876543210xyz'
    _write_transcript(transcript, [
        {'type': 'user', 'uuid': user_uuid, 'parentUuid': None,
         'message': {'content': 'do the thing'}},
        _assistant_with_usage(msg_id='m1', text='Sure, here we go.\n\nDone.',
                              uuid=turn_uuid, parent_uuid=user_uuid),
    ])
    turn_trace.handle(_p('UserPromptSubmit', session_id='s1',
                         transcript_path=str(transcript)))
    response_spans = [s for s in captured_spans if s.get('name') == 'assistant_response']
    assert len(response_spans) == 1
    s = response_spans[0]
    assert s['trace_id'] == 's1'
    assert s['span_id'] == f'resp-{turn_uuid[:13]}'
    assert s['parent_id'] == f'prompt-{user_uuid[:13]}'
    assert s['attributes']['text'] == 'Sure, here we go.\n\nDone.'
    assert s['attributes']['truncated'] is False
    assert s['attributes']['turn_uuid'] == turn_uuid
    assert s['attributes']['response_chars'] == len('Sure, here we go.\n\nDone.')


def test_assistant_response_concatenates_split_message_blocks(
    captured_spans, tmp_path,
):
    """A single API response can be split across two assistant entries
    sharing message.id (text-block then tool_use-block, or vice versa).
    The response span must carry text from BOTH entries."""
    transcript = tmp_path / 'session.jsonl'
    user_uuid = 'user-uuid-abcdef0123456'
    e1 = _assistant_with_usage(
        msg_id='same-msg', text='first half',
        uuid='asst-1aaaaaaaaaaaa', parent_uuid=user_uuid,
    )
    # second entry: same msg_id (dedup key), no usage, but a second text block
    e2 = {
        'type': 'assistant',
        'uuid': 'asst-2bbbbbbbbbbbb',
        'parentUuid': 'asst-1aaaaaaaaaaaa',
        'timestamp': '2026-04-27T12:00:01Z',
        'message': {
            'id': 'same-msg',
            'model': 'claude-opus-4-7',
            'content': [
                {'type': 'tool_use', 'id': 't1', 'name': 'Bash', 'input': {}},
                {'type': 'text', 'text': 'second half'},
            ],
        },
    }
    _write_transcript(transcript, [
        {'type': 'user', 'uuid': user_uuid, 'parentUuid': None,
         'message': {'content': 'go'}},
        e1, e2,
    ])
    turn_trace.handle(_p('UserPromptSubmit', session_id='s1',
                         transcript_path=str(transcript)))
    response_spans = [s for s in captured_spans if s.get('name') == 'assistant_response']
    assert len(response_spans) == 1
    assert response_spans[0]['attributes']['text'] == 'first half\n\nsecond half'


def test_assistant_response_truncates_at_byte_cap(
    captured_spans, tmp_path, monkeypatch,
):
    """Long responses must be truncated to settings.assistant_response_max_bytes."""
    from lib import settings as settings_mod
    monkeypatch.setattr(settings_mod.settings, 'assistant_response_max_bytes', 32)

    transcript = tmp_path / 'session.jsonl'
    user_uuid = 'user-uuid-abcdef0123456'
    long_text = 'x' * 10_000
    _write_transcript(transcript, [
        {'type': 'user', 'uuid': user_uuid, 'parentUuid': None,
         'message': {'content': 'go'}},
        _assistant_with_usage(msg_id='m1', text=long_text,
                              uuid='asst-uuid-9876543210xyz',
                              parent_uuid=user_uuid),
    ])
    turn_trace.handle(_p('UserPromptSubmit', session_id='s1',
                         transcript_path=str(transcript)))
    response_spans = [s for s in captured_spans if s.get('name') == 'assistant_response']
    assert len(response_spans) == 1
    s = response_spans[0]
    assert s['attributes']['truncated'] is True
    # text encoded utf-8 must be ≤ cap + the truncation marker length
    assert s['attributes']['text'].endswith('…[truncated]')
    body = s['attributes']['text'].rsplit('\n\n', 1)[0]
    assert len(body.encode('utf-8')) <= 32


def test_capture_gate_disables_assistant_response_emission(
    captured_spans, tmp_path, monkeypatch,
):
    """Flipping capture_assistant_response=False must suppress the
    assistant_response spans WITHOUT breaking the other spans (turn
    span + turn_usage event continue to flow)."""
    from lib import settings as settings_mod
    monkeypatch.setattr(settings_mod.settings, 'capture_assistant_response', False)

    # Capture post_event too so we can assert turn_usage still fires.
    posted_events: list[tuple[str, object]] = []
    import lib.hook_plugin as hp
    monkeypatch.setattr(hp, 'post_event', lambda name, data: posted_events.append((name, data)))

    transcript = tmp_path / 'session.jsonl'
    _write_transcript(transcript, [
        {'type': 'user', 'uuid': 'u1', 'message': {'content': 'go'}},
        _assistant_with_usage(msg_id='m1', text='hello',
                              uuid='asst-uuid-9876543210xyz', parent_uuid='u1'),
    ])
    turn_trace.handle(_p('UserPromptSubmit', session_id='s1',
                         transcript_path=str(transcript)))
    response_spans = [s for s in captured_spans if s.get('name') == 'assistant_response']
    assert response_spans == []
    # turn_usage rows still posted — capture gate is text-only.
    assert any(name == 'turn_usage' for name, _ in posted_events)


def test_only_tails_last_64_kb_of_large_transcript(captured_spans, tmp_path, monkeypatch):
    """A multi-GB transcript would stall the Stop hook if read whole.
    _TAIL_BYTES caps the I/O; the handler only needs the tail to find
    the most recent assistant turn."""
    transcript = tmp_path / 'big.jsonl'
    # Padding: 200 KiB of ignored user entries, then ONE assistant entry
    # at the end with the real model. If the tail cap wasn't applied,
    # this would still work — we need to assert the tail cap triggers
    # via a monkey-patched smaller threshold.
    monkeypatch.setattr(turn_trace.entry, '_TAIL_BYTES', 2048)  # 2 KiB

    padding = ('\n'.join(
        json.dumps({'type': 'user', 'message': {'content': 'x' * 100}})
        for _ in range(100)
    ) + '\n')
    tail = json.dumps({'type': 'assistant', 'message': {
        'model': 'claude-haiku-4-5-20251001',
        'content': [{'type': 'text', 'text': 'ok'}],
    }}) + '\n'
    transcript.write_text(padding + tail)

    turn_trace.handle(_p('UserPromptSubmit', session_id='s1',
                         transcript_path=str(transcript)))
    # The tail fits in 2 KiB and carries the assistant entry — must match.
    assert captured_spans[0]['attributes']['model'] == 'claude-haiku-4-5-20251001'


# ── PostToolUse fast path ──────────────────────────────────────────────

def test_post_tool_use_emits_assistant_response_span(captured_spans, tmp_path):
    """PostToolUse fires after every tool call. The assistant text that
    preceded the tool_use is in the transcript by then, so a fresh
    `assistant_response` span must appear without waiting for the next
    UserPromptSubmit / Stop / SessionEnd."""
    transcript = tmp_path / 'session.jsonl'
    _write_transcript(transcript, [
        {'type': 'user', 'uuid': 'u1', 'message': {'content': 'go'}},
        _assistant_with_usage(msg_id='m1', text='Working on it.',
                              uuid='asst-uuid-fastpath123', parent_uuid='u1'),
    ])
    turn_trace.handle(_p('PostToolUse', session_id='s1',
                         transcript_path=str(transcript)))
    response_spans = [s for s in captured_spans if s.get('name') == 'assistant_response']
    assert len(response_spans) == 1
    assert response_spans[0]['attributes']['text'] == 'Working on it.'
    # Lean path skips the `turn` span (model rollup) — that's still
    # produced on UserPromptSubmit / Stop / SessionEnd.
    assert not any(s.get('name') == 'turn' for s in captured_spans)


def test_post_tool_use_emits_tool_attribution_and_turn_usage(
    captured_spans, tmp_path, monkeypatch,
):
    """The "Tokens by tool" rollup reads per-tool input/output_tokens
    columns populated by the `tool_attribution` ingest, and the
    untagged total comes from session.input_tokens populated by the
    `turn_usage` ingest. PostToolUse must post both for new turns so
    the rollup reflects live data, not just data from the previous
    user prompt cycle."""
    posted_events: list[tuple[str, object]] = []
    import lib.hook_plugin as hp
    monkeypatch.setattr(hp, 'post_event', lambda name, data: posted_events.append((name, data)))

    transcript = tmp_path / 'session.jsonl'
    _write_transcript(transcript, [
        {'type': 'user', 'uuid': 'u1', 'message': {'content': 'go'}},
        _assistant_with_usage(
            msg_id='m1', text='running',
            uuid='asst-uuid-attribution', parent_uuid='u1',
            extra_blocks=[{'type': 'tool_use', 'id': 'tu_abc',
                           'name': 'Bash', 'input': {'command': 'ls'}}],
        ),
    ])
    turn_trace.handle(_p('PostToolUse', session_id='s4',
                         transcript_path=str(transcript)))
    attribution = [data for name, data in posted_events if name == 'tool_attribution']
    usage = [data for name, data in posted_events if name == 'turn_usage']
    assert len(attribution) == 1
    assert attribution[0]['turn_uuid'] == 'asst-uuid-attribution'
    assert attribution[0]['tool_calls'][0]['name'] == 'Bash'
    assert len(usage) == 1
    assert usage[0][0]['turn_uuid'] == 'asst-uuid-attribution'


def test_server_tool_span_nests_under_assistant_response(
    captured_spans, tmp_path,
):
    """Server-side tool spans (advisor, etc.) must be emitted with
    `parent_id` set to the turn's response span — that's what keeps the
    advisor card sorting after the assistant text that asked for it in
    the conversation view. The +(i+1)ms transcript-timestamp stagger is
    a fallback for legacy data; the parent_id link is the structural
    fix that doesn't depend on JS Date.getTime() preserving sub-ms
    precision (it doesn't)."""
    transcript = tmp_path / 'session.jsonl'
    turn_uuid = 'asst-uuid-srvtoolparent'
    _write_transcript(transcript, [
        {'type': 'user', 'uuid': 'u1', 'message': {'content': 'consult advisor'}},
        _assistant_with_usage(
            msg_id='m1', text="Let me consult the advisor.",
            uuid=turn_uuid, parent_uuid='u1',
            extra_blocks=[{
                'type': 'server_tool_use',
                'id': 'srvtoolu_01abcdef',
                'name': 'advisor',
                'input': {},
            }],
        ),
    ])
    turn_trace.handle(_p('PostToolUse', session_id='s1',
                         transcript_path=str(transcript)))
    srv = [s for s in captured_spans if s.get('span_id', '').startswith('srvtool-')]
    assert len(srv) == 1, f'expected 1 srvtool span, got {len(srv)}'
    assert srv[0]['name'] == 'tool.advisor'
    # The structural fix: parent points at the response span for this turn,
    # not at the prompt. Conversation view's tree walk renders the response
    # first; the advisor falls out under it instead of competing for
    # sibling sort order.
    assert srv[0].get('parent_id') == f'resp-{turn_uuid[:13]}'


def test_server_tool_span_nests_under_assistant_thinking_when_no_text(
    captured_spans, tmp_path,
):
    """When a turn carries only thinking (no user-visible text) plus a
    server-side tool call, the server-tool span nests under the
    `assistant.thinking` span instead. The conversation view's tree
    walk still renders the parent before the tool."""
    transcript = tmp_path / 'session.jsonl'
    turn_uuid = 'asst-uuid-thinkonly1234'
    _write_transcript(transcript, [
        {'type': 'user', 'uuid': 'u1', 'message': {'content': 'go'}},
        _assistant_with_usage(
            msg_id='m1', text='',  # no user-visible text
            uuid=turn_uuid, parent_uuid='u1',
            extra_blocks=[
                {'type': 'thinking', 'thinking': 'figuring out…', 'signature': 'sig1'},
                {
                    'type': 'server_tool_use',
                    'id': 'srvtoolu_02fedcba',
                    'name': 'advisor',
                    'input': {},
                },
            ],
        ),
    ])
    # Override the helper's text-only content with the extra_blocks
    # ordering (thinking first, then server_tool_use, no plain text).
    # _assistant_with_usage adds a leading text block; suppress it via empty string.
    turn_trace.handle(_p('PostToolUse', session_id='s1',
                         transcript_path=str(transcript)))
    srv = [s for s in captured_spans if s.get('span_id', '').startswith('srvtool-')]
    assert len(srv) == 1
    assert srv[0].get('parent_id') == f'think-{turn_uuid[:13]}'


def test_assistant_response_carries_estimated_output_tokens(
    captured_spans, tmp_path,
):
    """The assistant_response span must include an output_tokens estimate
    in attributes — ingest_session_spans promotes it into the column so
    fetch_tool_token_rollup can sum it into the 'assistant text' bucket."""
    transcript = tmp_path / 'session.jsonl'
    text = 'Hello world, this is a multi-sentence assistant response.'
    _write_transcript(transcript, [
        {'type': 'user', 'uuid': 'u1', 'message': {'content': 'go'}},
        _assistant_with_usage(msg_id='m1', text=text,
                              uuid='asst-uuid-outtokens0', parent_uuid='u1'),
    ])
    turn_trace.handle(_p('UserPromptSubmit', session_id='s7',
                         transcript_path=str(transcript)))
    response_spans = [s for s in captured_spans if s.get('name') == 'assistant_response']
    assert len(response_spans) == 1
    attrs = response_spans[0]['attributes']
    assert isinstance(attrs.get('output_tokens'), int)
    # Tokenizer for cl100k_base puts this text in the ~12-20 token range —
    # exact value is encoder-dependent, just assert plausible bounds.
    assert 5 < attrs['output_tokens'] < 40


def _enc_thinking_assistant(*, uuid, content_blocks, output_tokens,
                            advisor_out=None, ts='2026-06-03T06:48:49Z'):
    """An assistant entry whose extended thinking is *encrypted* — a
    `thinking` block with an empty plaintext field but a present
    `signature` (the real Opus 4.x transcript shape). `advisor_out`, when
    given, bills via `usage.iterations` (an `advisor_message`), NOT the
    top-level output_tokens — exactly how the API reports a server-tool
    sub-call: it is excluded from the main turn's output_tokens."""
    usage = {
        'input_tokens': 4,
        'output_tokens': output_tokens,
        'cache_creation_input_tokens': 0,
        'cache_read_input_tokens': 0,
    }
    if advisor_out is not None:
        usage['iterations'] = [
            {'type': 'message', 'output_tokens': output_tokens},
            {'type': 'advisor_message', 'output_tokens': advisor_out,
             'input_tokens': 192174},
        ]
    return {
        'type': 'assistant', 'uuid': uuid, 'parentUuid': 'u1',
        'timestamp': ts, 'requestId': 'req-' + uuid,
        'message': {'id': 'msg-' + uuid, 'model': 'claude-opus-4-8',
                    'content': content_blocks, 'usage': usage},
    }


def _turn_split(captured_spans, posted_events):
    """Pull a single turn's emitted figures out of the capture buffers:
    (assistant_response spans, assistant.thinking spans, {tool_name:
    attribution_call}). Keeps the comprehensions out of the test bodies."""
    resp = [s for s in captured_spans if s.get('name') == 'assistant_response']
    think = [s for s in captured_spans if s.get('name') == 'assistant.thinking']
    attr = [d for n, d in posted_events if n == 'tool_attribution']
    calls = {c['name']: c for c in attr[0]['tool_calls']} if attr else {}
    return resp, think, calls


def test_encrypted_thinking_residual_lands_on_thinking_span_not_tool(
    captured_spans, tmp_path, monkeypatch,
):
    """Regression for the "Write a file = 22k tokens" inflation.

    On a turn with user-visible text, encrypted (signature-only) extended
    thinking, a Write, and an advisor sub-call, the turn's API
    output_tokens must split as:
        assistant_response  = text estimate
        tool.Write          = its RAW tool_use estimate (NOT inflated)
        assistant.thinking  = the leftover reasoning residual
    with `text + Write + thinking == turn output_tokens` and the advisor's
    iteration tokens kept OUT of that sum. Before the fix, redistribution
    smeared the residual onto Write, inflating it toward the whole turn
    total."""
    from lib.tokens.token_estimator import (  # type: ignore
        estimate_text_tokens, estimate_tool_use_tokens,
    )
    posted_events: list[tuple[str, object]] = []
    import lib.hook_plugin as hp
    monkeypatch.setattr(hp, 'post_event',
                        lambda n, d: posted_events.append((n, d)))

    turn_uuid = 'asst-uuid-encthink01'
    text = 'Done — decomposed the component.'
    write_input = {'file_path': '/repo/Big.vue',
                   'content': '  const x = computeValue(items)\n' * 300}
    transcript = tmp_path / 'session.jsonl'
    _write_transcript(transcript, [
        {'type': 'user', 'uuid': 'u1', 'message': {'content': 'decompose it'}},
        _enc_thinking_assistant(
            uuid=turn_uuid, output_tokens=22195, advisor_out=15073,
            content_blocks=[
                {'type': 'thinking', 'thinking': '', 'signature': 'S' * 4000},
                {'type': 'text', 'text': text},
                {'type': 'server_tool_use', 'id': 'srvtoolu_adv01',
                 'name': 'advisor', 'input': {}},
                {'type': 'thinking', 'thinking': '', 'signature': 'S' * 600},
                {'type': 'tool_use', 'id': 'tu_write01', 'name': 'Write',
                 'input': write_input},
            ],
        ),
    ])
    turn_trace.handle(_p('PostToolUse', session_id='senc',
                         transcript_path=str(transcript)))

    resp, think, calls = _turn_split(captured_spans, posted_events)
    # The "show card too" behaviour: a text turn that also reasoned emits
    # BOTH spans.
    assert len(resp) == 1
    assert len(think) == 1
    resp_out = resp[0]['attributes']['output_tokens']
    think_out = think[0]['attributes']['output_tokens']
    write_out = calls['Write']['output_tokens']
    raw_write = estimate_tool_use_tokens('Write', write_input)
    # 1. Write keeps its RAW estimate — redistribution did NOT inflate it.
    assert write_out == raw_write
    # 2. Response span carries only the text estimate.
    assert resp_out == estimate_text_tokens(text)
    # 3. Thinking span claims the leftover reasoning residual, which
    #    dwarfs the tool — the bulk of the turn was reasoning.
    assert think_out == max(0, 22195 - resp_out - write_out)
    assert think_out > write_out
    # 4. Invariant: the three independently-emitted figures account for
    #    the full turn output_tokens, nothing lost or double-counted.
    assert resp_out + write_out + think_out == 22195
    # 5. The advisor bills via usage.iterations, NOT this turn's output —
    #    it stays out of the sum above.
    assert calls['advisor']['output_tokens'] == 15073
    # 6. The thinking card sorts before the response (1 ms stagger).
    assert think[0]['start_time'] < resp[0]['start_time']


def test_thinking_only_residual_excludes_server_side_tool(
    captured_spans, tmp_path, monkeypatch,
):
    """A thinking-only turn (no user-visible text) with encrypted thinking
    + an advisor sub-call: the assistant.thinking residual must subtract
    only the RAW *non-server* tool_use, never the advisor's iteration
    tokens (which aren't part of this turn's output_tokens). Subtracting
    the advisor would wrongly clamp the thinking span to zero."""
    from lib.tokens.token_estimator import estimate_tool_use_tokens  # type: ignore
    import lib.hook_plugin as hp
    monkeypatch.setattr(hp, 'post_event', lambda n, d: None)

    turn_uuid = 'asst-uuid-thinkadv01'
    bash_input = {'command': 'ls -la /some/path'}
    transcript = tmp_path / 'session.jsonl'
    _write_transcript(transcript, [
        {'type': 'user', 'uuid': 'u1', 'message': {'content': 'go'}},
        _enc_thinking_assistant(
            uuid=turn_uuid, output_tokens=8000, advisor_out=15073,
            content_blocks=[
                {'type': 'thinking', 'thinking': '', 'signature': 'S' * 2000},
                {'type': 'server_tool_use', 'id': 'srvtoolu_adv02',
                 'name': 'advisor', 'input': {}},
                {'type': 'tool_use', 'id': 'tu_bash02', 'name': 'Bash',
                 'input': bash_input},
            ],
        ),
    ])
    turn_trace.handle(_p('PostToolUse', session_id='sthk',
                         transcript_path=str(transcript)))

    resp, think, _calls = _turn_split(captured_spans, [])
    # thinking-only: no response card.
    assert len(think) == 1
    assert resp == []
    think_out = think[0]['attributes']['output_tokens']
    raw_bash = estimate_tool_use_tokens('Bash', bash_input)
    # Residual excludes the advisor's 15073; thinking stays well above 0.
    assert think_out == max(0, 8000 - raw_bash)
    assert think_out > 7000


def test_post_tool_use_throttled_by_seen_uuid_cache(captured_spans, tmp_path):
    """A second PostToolUse on the same transcript must not re-post a
    turn we've already seen. Idempotency on the server is a safety net;
    the client cache is the throttle."""
    transcript = tmp_path / 'session.jsonl'
    _write_transcript(transcript, [
        {'type': 'user', 'uuid': 'u1', 'message': {'content': 'go'}},
        _assistant_with_usage(msg_id='m1', text='first',
                              uuid='asst-uuid-cache00000a', parent_uuid='u1'),
    ])
    turn_trace.handle(_p('PostToolUse', session_id='s2',
                         transcript_path=str(transcript)))
    assert len([s for s in captured_spans if s.get('name') == 'assistant_response']) == 1
    captured_spans.clear()
    turn_trace.handle(_p('PostToolUse', session_id='s2',
                         transcript_path=str(transcript)))
    assert [s for s in captured_spans if s.get('name') == 'assistant_response'] == []


def test_post_tool_use_emits_only_new_turn_after_existing_one(
    captured_spans, tmp_path,
):
    """When PostToolUse fires twice, the second call must post only the
    turn that arrived between the two — not the one we already saw."""
    transcript = tmp_path / 'session.jsonl'
    _write_transcript(transcript, [
        {'type': 'user', 'uuid': 'u1', 'message': {'content': 'go'}},
        _assistant_with_usage(msg_id='m1', text='first',
                              uuid='asst-uuid-firstturn0', parent_uuid='u1',
                              ts='2026-04-27T12:00:00Z'),
    ])
    turn_trace.handle(_p('PostToolUse', session_id='s3',
                         transcript_path=str(transcript)))
    captured_spans.clear()
    # Append a second turn and fire again.
    with open(transcript, 'a') as f:
        f.write(json.dumps(_assistant_with_usage(
            msg_id='m2', text='second',
            uuid='asst-uuid-secondturn', parent_uuid='asst-uuid-firstturn0',
            ts='2026-04-27T12:00:01Z',
        )) + '\n')
    turn_trace.handle(_p('PostToolUse', session_id='s3',
                         transcript_path=str(transcript)))
    response_spans = [s for s in captured_spans if s.get('name') == 'assistant_response']
    assert len(response_spans) == 1
    assert response_spans[0]['attributes']['text'] == 'second'


# ── ai-title spans ──────────────────────────────────────────────────


def test_emits_session_title_span_from_ai_title(captured_spans, tmp_path):
    """Claude Code writes `{"type":"ai-title","aiTitle":"..."}` into the
    transcript. The handler reads it and emits a `session.title` span."""
    transcript = tmp_path / 'session.jsonl'
    _write_transcript(transcript, [
        {'type': 'user', 'message': {'content': 'hi'}},
        {'type': 'ai-title', 'aiTitle': 'Refactor the parser', 'sessionId': 's1'},
        {'type': 'assistant', 'message': {'model': 'claude-opus-4-7'}},
    ])
    turn_trace.handle(_p('UserPromptSubmit', session_id='s1',
                         transcript_path=str(transcript)))
    title_spans = [s for s in captured_spans if s.get('name') == 'session.title']
    assert len(title_spans) == 1
    assert title_spans[0]['attributes']['text'] == 'Refactor the parser'
    assert title_spans[0]['attributes']['source'] == 'claude_ai_title'


def test_session_title_uses_last_ai_title(captured_spans, tmp_path):
    """A session can have multiple ai-title entries — the LAST one wins
    (Claude regenerates the title when the topic pivots)."""
    transcript = tmp_path / 'session.jsonl'
    _write_transcript(transcript, [
        {'type': 'ai-title', 'aiTitle': 'Original topic'},
        {'type': 'assistant', 'message': {'model': 'claude-opus-4-7'}},
        {'type': 'ai-title', 'aiTitle': 'Pivoted topic'},
    ])
    turn_trace.handle(_p('UserPromptSubmit', session_id='s1',
                         transcript_path=str(transcript)))
    title_spans = [s for s in captured_spans if s.get('name') == 'session.title']
    assert len(title_spans) == 1
    assert title_spans[0]['attributes']['text'] == 'Pivoted topic'


def test_session_title_uses_stable_span_id(captured_spans, tmp_path):
    """Re-emits must overwrite the same row, not spam new spans —
    so the span_id must be deterministic from trace_id."""
    transcript = tmp_path / 'session.jsonl'
    _write_transcript(transcript, [
        {'type': 'ai-title', 'aiTitle': 'X'},
        {'type': 'assistant', 'message': {'model': 'claude-opus-4-7'}},
    ])
    turn_trace.handle(_p('UserPromptSubmit', session_id='abc123',
                         transcript_path=str(transcript)))
    title_span = next(s for s in captured_spans if s.get('name') == 'session.title')
    assert title_span['span_id'] == 'sttl-abc123'


def test_session_title_skips_when_unchanged(captured_spans, tmp_path):
    """Once a title is posted, subsequent hook invocations with the same
    ai-title must NOT re-emit the span. Avoids needlessly hitting the
    trace ingest endpoint every Stop tick."""
    transcript = tmp_path / 'session.jsonl'
    _write_transcript(transcript, [
        {'type': 'ai-title', 'aiTitle': 'Same title'},
        {'type': 'assistant', 'message': {'model': 'claude-opus-4-7'}},
    ])
    turn_trace.handle(_p('UserPromptSubmit', session_id='s1',
                         transcript_path=str(transcript)))
    captured_spans.clear()
    # Second invocation — same title still in transcript.
    turn_trace.handle(_p('UserPromptSubmit', session_id='s1',
                         transcript_path=str(transcript)))
    title_spans = [s for s in captured_spans if s.get('name') == 'session.title']
    assert title_spans == []


def test_session_title_re_emits_when_changed(captured_spans, tmp_path):
    """If Claude regenerates the title between hook invocations, the
    handler must pick that up and emit a fresh span."""
    transcript = tmp_path / 'session.jsonl'
    _write_transcript(transcript, [
        {'type': 'ai-title', 'aiTitle': 'First title'},
        {'type': 'assistant', 'message': {'model': 'claude-opus-4-7'}},
    ])
    turn_trace.handle(_p('UserPromptSubmit', session_id='s1',
                         transcript_path=str(transcript)))
    captured_spans.clear()
    # Append a new title line.
    with open(transcript, 'a') as f:
        f.write(json.dumps({'type': 'ai-title', 'aiTitle': 'Second title'}) + '\n')
    turn_trace.handle(_p('UserPromptSubmit', session_id='s1',
                         transcript_path=str(transcript)))
    title_spans = [s for s in captured_spans if s.get('name') == 'session.title']
    assert len(title_spans) == 1
    assert title_spans[0]['attributes']['text'] == 'Second title'


# A real /rename invocation lands in the transcript as a single-line
# `local_command` system entry. The handler uses presence of this line
# to distinguish a user-issued rename from a `custom-title` line that
# Claude Code inherits from a /clear'd parent conversation.
_RENAME_SYSTEM_ENTRY = {
    'type': 'system',
    'subtype': 'local_command',
    'content': (
        '<command-name>/rename</command-name>\n'
        '            <command-message>rename</command-message>\n'
        '            <command-args>My chosen name</command-args>'
    ),
}


def test_emits_session_title_span_from_custom_title(captured_spans, tmp_path):
    """`/rename` writes a `local_command` system entry plus a
    `{"type":"custom-title","customTitle":"..."}` line. The handler
    must emit the span with source='user_rename'."""
    transcript = tmp_path / 'session.jsonl'
    _write_transcript(transcript, [
        {'type': 'user', 'message': {'content': 'hi'}},
        _RENAME_SYSTEM_ENTRY,
        {'type': 'custom-title', 'customTitle': 'My chosen name', 'sessionId': 's1'},
        {'type': 'assistant', 'message': {'model': 'claude-opus-4-7'}},
    ])
    turn_trace.handle(_p('UserPromptSubmit', session_id='s1',
                         transcript_path=str(transcript)))
    title_spans = [s for s in captured_spans if s.get('name') == 'session.title']
    assert len(title_spans) == 1
    assert title_spans[0]['attributes']['text'] == 'My chosen name'
    assert title_spans[0]['attributes']['source'] == 'user_rename'


def test_session_title_custom_title_wins_over_ai_title(captured_spans, tmp_path):
    """A `/rename` is the user's authoritative intent — even if Claude
    writes another ai-title afterward, the custom-title still wins."""
    transcript = tmp_path / 'session.jsonl'
    _write_transcript(transcript, [
        {'type': 'ai-title', 'aiTitle': 'Auto title (early)'},
        _RENAME_SYSTEM_ENTRY,
        {'type': 'custom-title', 'customTitle': 'User chose this'},
        {'type': 'ai-title', 'aiTitle': 'Auto title (late, after rename)'},
        {'type': 'assistant', 'message': {'model': 'claude-opus-4-7'}},
    ])
    turn_trace.handle(_p('UserPromptSubmit', session_id='s1',
                         transcript_path=str(transcript)))
    title_spans = [s for s in captured_spans if s.get('name') == 'session.title']
    assert len(title_spans) == 1
    assert title_spans[0]['attributes']['text'] == 'User chose this'
    assert title_spans[0]['attributes']['source'] == 'user_rename'


def test_session_title_inherited_custom_title_falls_back_to_ai_title(
    captured_spans, tmp_path,
):
    """When `/clear` spawns a new session, Claude Code copies the
    parent transcript's custom-title forward as the first lines of the
    new file and re-stamps it on later turns — without ever issuing a
    fresh `/rename`. The handler must NOT treat this inherited copy as
    a user rename; an ai-title that Claude later generates for the new
    topic should take over instead."""
    transcript = tmp_path / 'session.jsonl'
    _write_transcript(transcript, [
        # Inherited custom-title at the top — no /rename system entry.
        {'type': 'custom-title', 'customTitle': 'Old parent topic'},
        {'type': 'custom-title', 'customTitle': 'Old parent topic'},
        {'type': 'user', 'message': {'content': 'hi'}},
        {'type': 'ai-title', 'aiTitle': 'Fresh topic for this session'},
        {'type': 'assistant', 'message': {'model': 'claude-opus-4-7'}},
        # Claude re-stamps the inherited title mid-session.
        {'type': 'custom-title', 'customTitle': 'Old parent topic'},
    ])
    turn_trace.handle(_p('UserPromptSubmit', session_id='s1',
                         transcript_path=str(transcript)))
    title_spans = [s for s in captured_spans if s.get('name') == 'session.title']
    assert len(title_spans) == 1
    assert title_spans[0]['attributes']['text'] == 'Fresh topic for this session'
    assert title_spans[0]['attributes']['source'] == 'claude_ai_title'


def test_session_title_inherited_custom_title_alone_emits_nothing(
    captured_spans, tmp_path,
):
    """A /clear'd session with only inherited custom-title lines (no
    ai-title, no /rename) must emit no title span — the sessions
    aggregator will fall back to the first-prompt title instead."""
    transcript = tmp_path / 'session.jsonl'
    _write_transcript(transcript, [
        {'type': 'custom-title', 'customTitle': 'Old parent topic'},
        {'type': 'user', 'message': {'content': 'hi'}},
        {'type': 'assistant', 'message': {'model': 'claude-opus-4-7'}},
    ])
    turn_trace.handle(_p('UserPromptSubmit', session_id='s1',
                         transcript_path=str(transcript)))
    title_spans = [s for s in captured_spans if s.get('name') == 'session.title']
    assert title_spans == []


def test_session_title_assistant_bash_mentioning_rename_is_not_a_rename(
    captured_spans, tmp_path,
):
    """An assistant Bash tool_use that merely references the literal
    text `<command-name>/rename</command-name>` (e.g. grepping
    transcripts while debugging this very bug) must NOT count as a
    real rename. Only a `local_command` system entry counts."""
    transcript = tmp_path / 'session.jsonl'
    _write_transcript(transcript, [
        {'type': 'custom-title', 'customTitle': 'Inherited'},
        {
            'type': 'assistant',
            'message': {
                'model': 'claude-opus-4-7',
                'content': [{
                    'type': 'tool_use',
                    'name': 'Bash',
                    'input': {
                        'command': (
                            'grep "<command-name>/rename</command-name>" *.jsonl'
                        ),
                    },
                }],
            },
        },
    ])
    turn_trace.handle(_p('UserPromptSubmit', session_id='s1',
                         transcript_path=str(transcript)))
    title_spans = [s for s in captured_spans if s.get('name') == 'session.title']
    assert title_spans == []


def test_session_title_resends_when_source_flips(captured_spans, tmp_path):
    """If a `/rename` happens between hook invocations the sentinel must
    treat the source change as a re-emit signal even when the text alone
    could coincidentally match the previously cached value."""
    transcript = tmp_path / 'session.jsonl'
    _write_transcript(transcript, [
        {'type': 'ai-title', 'aiTitle': 'Same Words'},
        {'type': 'assistant', 'message': {'model': 'claude-opus-4-7'}},
    ])
    turn_trace.handle(_p('UserPromptSubmit', session_id='s1',
                         transcript_path=str(transcript)))
    captured_spans.clear()
    with open(transcript, 'a') as f:
        f.write(json.dumps(_RENAME_SYSTEM_ENTRY, separators=(',', ':')) + '\n')
        f.write(json.dumps(
            {'type': 'custom-title', 'customTitle': 'Same Words'},
            separators=(',', ':'),
        ) + '\n')
    turn_trace.handle(_p('UserPromptSubmit', session_id='s1',
                         transcript_path=str(transcript)))
    title_spans = [s for s in captured_spans if s.get('name') == 'session.title']
    assert len(title_spans) == 1
    assert title_spans[0]['attributes']['source'] == 'user_rename'


def test_no_title_span_when_transcript_has_no_ai_title(captured_spans, tmp_path):
    transcript = tmp_path / 'session.jsonl'
    _write_transcript(transcript, [
        {'type': 'user', 'message': {'content': 'hi'}},
        {'type': 'assistant', 'message': {'model': 'claude-opus-4-7'}},
    ])
    turn_trace.handle(_p('UserPromptSubmit', session_id='s1',
                         transcript_path=str(transcript)))
    title_spans = [s for s in captured_spans if s.get('name') == 'session.title']
    assert title_spans == []


# ── Harness attachment spans ────────────────────────────────────────


def test_emits_attachment_spans_for_useful_kinds(captured_spans, tmp_path, monkeypatch):
    """The three attachment kinds we trace land as `harness.*` spans;
    noise kinds (hook_success, hook_additional_context) are skipped."""
    monkeypatch.setenv('REGIN_TURN_TRACE_STATE_DIR', str(tmp_path / '_state'))
    transcript = tmp_path / 'session.jsonl'
    _write_transcript(transcript, [
        {'type': 'user', 'uuid': 'u1', 'message': {'content': 'go'}},
        _assistant_with_usage(msg_id='m1', uuid='asst-att-turn001', parent_uuid='u1'),
        {'type': 'attachment', 'uuid': 'att-noise',
         'attachment': {'type': 'hook_success', 'name': 'PreToolUse'}},
        {'type': 'attachment', 'uuid': 'att-task-9999999',
         'timestamp': '2026-04-27T12:00:05Z',
         'attachment': {'type': 'task_reminder', 'itemCount': 2,
                        'content': ['todo-a', 'todo-b']}},
        {'type': 'attachment', 'uuid': 'att-skill-init-1',
         'timestamp': '2026-04-27T12:00:06Z',
         'attachment': {'type': 'skill_listing', 'isInitial': True,
                        'skillCount': 3, 'content': '- foo\n- bar\n- baz'}},
        {'type': 'attachment', 'uuid': 'att-tools-delta1',
         'timestamp': '2026-04-27T12:00:07Z',
         'attachment': {'type': 'deferred_tools_delta',
                        'addedNames': ['NewTool'], 'removedNames': [],
                        'readdedNames': [], 'pendingMcpServers': []}},
    ])
    turn_trace.handle(_p('UserPromptSubmit', session_id='att-s1',
                         transcript_path=str(transcript)))
    names = sorted(s.get('name') for s in captured_spans if str(s.get('name', '')).startswith('harness.'))
    assert names == ['harness.skill_listing', 'harness.task_reminder', 'harness.tools_delta']

    # Span-id rules: initial skill_listing uses a session-stable id;
    # other attachments use att-<uuid[:13]>.
    by_name = {s['name']: s for s in captured_spans if str(s.get('name', '')).startswith('harness.')}
    assert by_name['harness.skill_listing']['span_id'].startswith('skill-init-')
    assert by_name['harness.task_reminder']['span_id'] == 'att-att-task-9999'
    assert by_name['harness.tools_delta']['span_id'] == 'att-att-tools-del'

    # Content sanity
    assert by_name['harness.task_reminder']['attributes']['item_count'] == 2
    assert by_name['harness.skill_listing']['attributes']['skill_count'] == 3
    assert by_name['harness.tools_delta']['attributes']['added_names'] == ['NewTool']


def test_queued_command_emits_prompt_span(captured_spans, tmp_path, monkeypatch):
    """A prompt typed while the agent is mid-turn is queued and, on
    dequeue, injected as a `queued_command` attachment instead of firing
    UserPromptSubmit — so it leaves no `prompt` span. The transcript scan
    recovers it as a `prompt` span (span_id `prompt-<uuid[:13]>`, the
    same scheme UserPromptSubmit uses so `_graft_orphans` anchors the
    following assistant turns to it)."""
    monkeypatch.setenv('REGIN_TURN_TRACE_STATE_DIR', str(tmp_path / '_state'))
    transcript = tmp_path / 'session.jsonl'
    _write_transcript(transcript, [
        {'type': 'user', 'uuid': 'u1', 'message': {'content': 'go'}},
        {'type': 'attachment', 'uuid': 'qcmd-abc1234567xyz',
         'parentUuid': 'u1', 'timestamp': '2026-04-27T12:00:05Z',
         'attachment': {'type': 'queued_command',
                        'prompt': 'i mean not the same time format',
                        'commandMode': 'prompt'}},
    ])
    turn_trace.handle(_p('UserPromptSubmit', session_id='qc-s1',
                         transcript_path=str(transcript)))
    prompt_spans = [s for s in captured_spans if s.get('name') == 'prompt']
    assert len(prompt_spans) == 1
    s = prompt_spans[0]
    assert s['span_id'] == 'prompt-qcmd-abc12345'
    assert s['attributes']['text'] == 'i mean not the same time format'
    assert s['attributes']['chars'] == len('i mean not the same time format')
    assert s['attributes']['queued'] is True


def test_queued_command_anchor_retimed_to_first_response(captured_spans, tmp_path, monkeypatch):
    """The recovered queued-prompt anchor sorts at its FIRST RESPONSE (the
    first turn at/after the attachment ts), not its type-time — so it groups
    with its own turn instead of splitting the interrupted one. Robust to an
    intermediate attachment between the queued_command and the response (the
    response's parentUuid is the intermediate, not the queued_command)."""
    monkeypatch.setenv('REGIN_TURN_TRACE_STATE_DIR', str(tmp_path / '_state'))
    transcript = tmp_path / 'session.jsonl'
    _write_transcript(transcript, [
        {'type': 'user', 'uuid': 'u1', 'message': {'content': 'go'}},
        {'type': 'attachment', 'uuid': 'qc-uuid-aaaaaa', 'parentUuid': 'u1',
         'timestamp': '2026-04-27T12:00:05Z',
         'attachment': {'type': 'queued_command', 'prompt': 'queued one',
                        'commandMode': 'prompt'}},
        {'type': 'attachment', 'uuid': 'inter-uuid-bb',  # intermediate
         'parentUuid': 'qc-uuid-aaaaaa', 'timestamp': '2026-04-27T12:00:05Z'},
        {'type': 'assistant', 'uuid': 'a1', 'parentUuid': 'inter-uuid-bb',
         'timestamp': '2026-04-27T12:00:20Z', 'requestId': 'r1',
         'message': {'id': 'm1', 'model': 'claude-opus-4-8',
                     'content': [{'type': 'text', 'text': 'reply'}],
                     'usage': {'input_tokens': 1, 'output_tokens': 1}}},
    ])
    turn_trace.handle(_p('Stop', session_id='qc-rt',
                         transcript_path=str(transcript)))
    queued = [s for s in captured_spans if s.get('name') == 'prompt'
              and s['attributes'].get('text') == 'queued one']
    resp_spans = [s for s in captured_spans if s.get('name') == 'assistant_response']
    assert len(queued) == 1 and len(resp_spans) == 1
    # anchored at the response time, not the 12:00:05 type-time
    assert queued[0]['start_time'] == resp_spans[0]['start_time']
    assert not queued[0]['start_time'].startswith('2026-04-27T12:00:05')
    # the response nests UNDER the queued prompt (resolves to it, not the
    # original 'go' prompt) — the queued_command is treated as a prompt boundary
    assert resp_spans[0].get('parent_id') == queued[0]['span_id']


def test_queued_command_with_images_emits_prompt_span_and_images(
    captured_spans, tmp_path, monkeypatch,
):
    """A queued prompt that carries pasted images arrives with `prompt`
    as a LIST of content blocks (text marker + base64 image parts), not
    a bare string — same dual shape as `message.content`. It must still
    recover a `prompt` span (text from the text blocks) and persist the
    inline images via `prompt_images`, like the UserPromptSubmit anchor
    path does."""
    monkeypatch.setenv('REGIN_TURN_TRACE_STATE_DIR', str(tmp_path / '_state'))
    events: list = []
    import lib.hook_plugin as hp
    monkeypatch.setattr(hp, 'post_event',
                        lambda name, data: events.append((name, data)))
    transcript = tmp_path / 'session.jsonl'
    _write_transcript(transcript, [
        {'type': 'user', 'uuid': 'u1', 'message': {'content': 'go'}},
        {'type': 'attachment', 'uuid': 'qcmd-img1234567xy',
         'parentUuid': 'u1', 'timestamp': '2026-04-27T12:00:05Z',
         'attachment': {'type': 'queued_command',
                        'prompt': [
                            {'type': 'text', 'text': '[Image #1] [Image #2]'},
                            {'type': 'image',
                             'source': {'type': 'base64',
                                        'media_type': 'image/png',
                                        'data': 'QUJD'}},
                            {'type': 'image',
                             'source': {'type': 'base64',
                                        'media_type': 'image/png',
                                        'data': 'REVG'}},
                        ]}},
    ])
    turn_trace.handle(_p('UserPromptSubmit', session_id='qc-img1',
                         transcript_path=str(transcript)))
    s = _prompt_spans_by_uuid(captured_spans)['prompt-qcmd-img12345']
    assert s['attributes']['text'] == '[Image #1] [Image #2]'
    assert s['attributes']['queued'] is True
    assert s['attributes']['image_indices'] == [1, 2]
    image_events = [d for name, d in events if name == 'prompt_images']
    assert image_events, 'prompt_images must persist for a queued image prompt'
    assert [
        (r['prompt_span_id'], r['idx'], r['data_b64'])
        for r in image_events[0]
    ] == [
        ('prompt-qcmd-img12345', 1, 'QUJD'),
        ('prompt-qcmd-img12345', 2, 'REVG'),
    ]


def test_queued_command_non_prompt_mode_skipped(captured_spans, tmp_path, monkeypatch):
    """Only prompt-mode queues become `prompt` spans — a queued slash
    command (different `commandMode`) is not a model prompt and must not
    masquerade as one."""
    monkeypatch.setenv('REGIN_TURN_TRACE_STATE_DIR', str(tmp_path / '_state'))
    transcript = tmp_path / 'session.jsonl'
    _write_transcript(transcript, [
        {'type': 'user', 'uuid': 'u1', 'message': {'content': 'go'}},
        {'type': 'attachment', 'uuid': 'qcmd-slash-000000',
         'parentUuid': 'u1', 'timestamp': '2026-04-27T12:00:05Z',
         'attachment': {'type': 'queued_command', 'prompt': '/clear',
                        'commandMode': 'slash'}},
    ])
    turn_trace.handle(_p('UserPromptSubmit', session_id='qc-s2',
                         transcript_path=str(transcript)))
    assert [s for s in captured_spans if s.get('name') == 'prompt'] == []


def test_duration_split_inference_vs_full_cycle(captured_spans, tmp_path, monkeypatch):
    """span.duration_ms = per-API-call latency (timestamp delta from
    the prior content entry). The cumulative whole-prompt-cycle wall
    clock (every API call + tools + hooks) lands on
    `attributes.turn_total_duration_ms` instead — surfacing both
    means a viewer can tell apart "this call took 3s" from "the whole
    cycle took 33s"."""
    monkeypatch.setenv('REGIN_TURN_TRACE_STATE_DIR', str(tmp_path / '_state'))
    transcript = tmp_path / 'session.jsonl'
    _write_transcript(transcript, [
        {'type': 'user', 'uuid': 'u1', 'timestamp': '2026-04-27T12:00:00Z',
         'message': {'content': 'go'}},
        _assistant_with_usage(
            msg_id='m-dur', uuid='asst-uuid-dur00000a', parent_uuid='u1',
            text='reply', ts='2026-04-27T12:00:03Z',  # +3s inference
        ),
        {'type': 'system', 'subtype': 'stop_hook_summary',
         'uuid': 'sys-stop-aaa', 'parentUuid': 'asst-uuid-dur00000a',
         'hookInfos': [{'command': 'py Stop', 'durationMs': 245}],
         'hookCount': 1, 'hookErrors': [], 'preventedContinuation': False,
         'timestamp': '2026-04-27T12:00:33Z'},
        {'type': 'system', 'subtype': 'turn_duration',
         'uuid': 'sys-dur-aaaa', 'parentUuid': 'sys-stop-aaa',
         'durationMs': 33202, 'timestamp': '2026-04-27T12:00:33Z'},
    ])
    turn_trace.handle(_p('UserPromptSubmit', session_id='dur-s1',
                         transcript_path=str(transcript)))
    response_spans = [s for s in captured_spans if s.get('name') == 'assistant_response']
    assert len(response_spans) == 1
    s = response_spans[0]
    assert s['duration_ms'] == 3000  # per-API-call latency (timestamp delta)
    assert s['attributes']['inference_duration_ms'] == 3000
    assert s['attributes']['turn_total_duration_ms'] == 33202
    # Estimated inference start = flush time (start_time) − inference latency.
    from datetime import datetime, timedelta
    est = datetime.fromisoformat(s['attributes']['estimated_start_time'])
    assert est == datetime.fromisoformat(s['start_time']) - timedelta(milliseconds=3000)


def test_stop_hook_summary_emits_hook_span(captured_spans, tmp_path, monkeypatch):
    """`system: stop_hook_summary` produces a `hook.stop_summary` span
    with per-hook latency, total duration_ms summed across hooks, and
    a `turn_uuid` link to the assistant entry it measured."""
    monkeypatch.setenv('REGIN_TURN_TRACE_STATE_DIR', str(tmp_path / '_state'))
    transcript = tmp_path / 'session.jsonl'
    _write_transcript(transcript, [
        {'type': 'user', 'uuid': 'u1', 'message': {'content': 'go'}},
        _assistant_with_usage(
            msg_id='m-hk', uuid='asst-uuid-hookbbb', parent_uuid='u1',
            text='reply', ts='2026-04-27T12:01:00Z',
        ),
        {'type': 'system', 'subtype': 'stop_hook_summary',
         'uuid': 'sys-stop-bbb', 'parentUuid': 'asst-uuid-hookbbb',
         'hookInfos': [
             {'command': 'cmd-a', 'durationMs': 100},
             {'command': 'cmd-b', 'durationMs': 250},
         ],
         'hookCount': 2, 'hookErrors': [], 'preventedContinuation': False,
         'timestamp': '2026-04-27T12:01:10Z'},
    ])
    turn_trace.handle(_p('UserPromptSubmit', session_id='dur-s2',
                         transcript_path=str(transcript)))
    hook_spans = [s for s in captured_spans if s.get('name') == 'hook.stop_summary']
    assert len(hook_spans) == 1
    s = hook_spans[0]
    assert s['span_id'] == 'sys-sys-stop-bbb'
    assert s['duration_ms'] == 350  # sum of per-hook ms
    attrs = s['attributes']
    assert attrs['hook_count'] == 2
    assert attrs['turn_uuid'] == 'asst-uuid-hookbbb'
    assert attrs['hooks'] == [
        {'command': 'cmd-a', 'duration_ms': 100},
        {'command': 'cmd-b', 'duration_ms': 250},
    ]


def test_model_refusal_fallback_emits_marker_span(captured_spans, tmp_path, monkeypatch):
    """`system: model_refusal_fallback` produces a `harness.model_refusal`
    marker span carrying the original/fallback models, refusal category, and
    retracted uuids (flat attrs), linked to the turn it followed. Observability
    only — no reparenting, no token accounting."""
    monkeypatch.setenv('REGIN_TURN_TRACE_STATE_DIR', str(tmp_path / '_state'))
    transcript = tmp_path / 'session.jsonl'
    _write_transcript(transcript, [
        {'type': 'user', 'uuid': 'u1', 'message': {'content': 'go'}},
        _assistant_with_usage(
            msg_id='m-rf', uuid='asst-uuid-refusalxx', parent_uuid='u1',
            text='reply', ts='2026-04-27T12:01:00Z',
        ),
        {'type': 'system', 'subtype': 'model_refusal_fallback',
         'uuid': 'sys-refusal-aaa', 'parentUuid': 'asst-uuid-refusalxx',
         'originalModel': 'claude-sonnet-4-5',
         'fallbackModel': 'claude-opus-4-8',
         'apiRefusalCategory': 'policy',
         'retractedMessageUuids': ['msg-r1', 'msg-r2'],
         'timestamp': '2026-04-27T12:01:05Z'},
    ])
    turn_trace.handle(_p('UserPromptSubmit', session_id='rf-s1',
                         transcript_path=str(transcript)))
    spans = [s for s in captured_spans if s.get('name') == 'harness.model_refusal']
    assert len(spans) == 1
    s = spans[0]
    assert s['span_id'] == 'sys-sys-refusal-a'  # sys-<uuid[:13]>
    attrs = s['attributes']
    kept = {k: attrs.get(k) for k in (
        'subtype', 'turn_uuid', 'original_model', 'fallback_model',
        'api_refusal_category', 'retracted_message_uuids')}
    assert kept == {
        'subtype': 'model_refusal_fallback',
        'turn_uuid': 'asst-uuid-refusalxx',
        'original_model': 'claude-sonnet-4-5',
        'fallback_model': 'claude-opus-4-8',
        'api_refusal_category': 'policy',
        'retracted_message_uuids': ['msg-r1', 'msg-r2'],
    }


def test_emits_local_command_spans_for_user_and_system_entries(
    captured_spans, tmp_path, monkeypatch,
):
    """Standalone local slash commands (`/add-dir`, `/clear`, `/exit`,
    `/usage`, …) never fire UserPromptSubmit, so without this scan path
    they leave no span behind. The transcript records them in one of
    two shapes:

      * user-typed (`/add-dir`): three `type=user` entries — caveat +
        command-name + stdout — chained via parentUuid.
      * system-emitted (`/usage`): two `type=system, subtype=local_command`
        entries — command-name + stdout, no caveat.

    Both must produce a `harness.local_command` span keyed off the
    command-name entry's uuid.
    """
    monkeypatch.setenv('REGIN_TURN_TRACE_STATE_DIR', str(tmp_path / '_state'))
    transcript = tmp_path / 'session.jsonl'
    _write_transcript(transcript, [
        # An assistant turn so read_usage has at least one finalized
        # turn; the local-command spans are emitted independently
        # alongside it.
        {'type': 'user', 'uuid': 'u-pre', 'message': {'content': 'go'}},
        _assistant_with_usage(msg_id='m1', uuid='asst-lc-turn0001', parent_uuid='u-pre'),
        # user-typed `/add-dir ~/note` — three entries chained by parentUuid.
        {'type': 'user', 'uuid': 'lc-caveat-add', 'parentUuid': 'asst-lc-turn0001',
         'timestamp': '2026-05-01T10:00:00Z',
         'message': {'content': '<local-command-caveat>Caveat: ...</local-command-caveat>'}},
        {'type': 'user', 'uuid': 'lc-name-add000', 'parentUuid': 'lc-caveat-add',
         'timestamp': '2026-05-01T10:00:00Z',
         'message': {'content': (
             '<command-name>/add-dir</command-name>\n'
             '<command-args>~/note</command-args>'
         )}},
        {'type': 'user', 'uuid': 'lc-stdout-add0', 'parentUuid': 'lc-name-add000',
         'timestamp': '2026-05-01T10:00:00Z',
         'message': {'content': '<local-command-stdout>Added ~/note</local-command-stdout>'}},
        # system-emitted `/usage` — command-name + stdout, no caveat.
        {'type': 'system', 'subtype': 'local_command',
         'uuid': 'lc-name-usage00', 'parentUuid': 'lc-stdout-add0',
         'timestamp': '2026-05-01T10:00:05Z',
         'content': '<command-name>/usage</command-name>\n<command-args></command-args>'},
        {'type': 'system', 'subtype': 'local_command',
         'uuid': 'lc-stdout-usage', 'parentUuid': 'lc-name-usage00',
         'timestamp': '2026-05-01T10:00:05Z',
         'content': '<local-command-stdout>Settings dialog dismissed</local-command-stdout>'},
    ])
    turn_trace.handle(_p('UserPromptSubmit', session_id='lc-s1',
                         transcript_path=str(transcript)))

    cmd_spans = [s for s in captured_spans if s.get('name') == 'harness.local_command']
    assert len(cmd_spans) == 2

    by_command = {s['attributes']['command_name']: s for s in cmd_spans}
    add = by_command['/add-dir']
    assert add['span_id'] == 'cmd-lc-name-add00'
    assert add['attributes']['args'] == '~/note'
    assert add['attributes']['stdout'] == 'Added ~/note'
    assert add['attributes']['kind'] == 'local_command'

    usage = by_command['/usage']
    assert usage['span_id'] == 'cmd-lc-name-usage'
    assert usage['attributes']['args'] is None
    assert usage['attributes']['stdout'] == 'Settings dialog dismissed'

    # A second hook firing must not re-post — all three uuids per
    # invocation (caveat + name + stdout) are cached as seen.
    captured_spans.clear()
    turn_trace.handle(_p('UserPromptSubmit', session_id='lc-s1',
                         transcript_path=str(transcript)))
    assert [s for s in captured_spans if s.get('name') == 'harness.local_command'] == []


def test_emits_local_command_span_for_bang_bash_command(
    captured_spans, tmp_path, monkeypatch,
):
    """A bang/bash command (`!ls`) is a local command too — Claude Code
    runs it without a model round-trip, so it fires no UserPromptSubmit
    and leaves no `prompt` span. Its transcript shape differs from a
    slash command: a `<bash-input>` entry plus a paired entry carrying
    both `<bash-stdout>` and `<bash-stderr>` (no caveat). The scan must
    recover it as a `harness.local_command` span with `command_name`
    carrying the leading `!` and stdout/stderr populated.
    """
    monkeypatch.setenv('REGIN_TURN_TRACE_STATE_DIR', str(tmp_path / '_state'))
    transcript = tmp_path / 'session.jsonl'
    _write_transcript(transcript, [
        {'type': 'user', 'uuid': 'u-pre', 'message': {'content': 'go'}},
        _assistant_with_usage(msg_id='m1', uuid='asst-bash-turn001', parent_uuid='u-pre'),
        {'type': 'user', 'uuid': 'bash-input-0001', 'parentUuid': 'asst-bash-turn001',
         'timestamp': '2026-05-01T10:00:00Z',
         'message': {'content': '<bash-input>ls</bash-input>'}},
        {'type': 'user', 'uuid': 'bash-stdout-001', 'parentUuid': 'bash-input-0001',
         'timestamp': '2026-05-01T10:00:00Z',
         'message': {'content':
            '<bash-stdout>AGENTS.md\nlib\nweb</bash-stdout><bash-stderr>oops</bash-stderr>'}},
    ])
    turn_trace.handle(_p('UserPromptSubmit', session_id='bash-s1',
                         transcript_path=str(transcript)))

    cmd_spans = [s for s in captured_spans if s.get('name') == 'harness.local_command']
    assert len(cmd_spans) == 1
    s = cmd_spans[0]
    assert s['span_id'] == 'cmd-bash-input-00'
    assert s['attributes']['command_name'] == '!ls'
    assert s['attributes']['args'] is None
    assert s['attributes']['kind'] == 'local_command'
    assert s['attributes']['stdout'] == 'AGENTS.md\nlib\nweb'
    assert s['attributes']['stderr'] == 'oops'

    # Idempotent: both bash uuids (input + stdout) are cached as seen.
    captured_spans.clear()
    turn_trace.handle(_p('UserPromptSubmit', session_id='bash-s1',
                         transcript_path=str(transcript)))
    assert [s for s in captured_spans if s.get('name') == 'harness.local_command'] == []


def test_attachment_spans_throttled_by_seen_cache(captured_spans, tmp_path, monkeypatch):
    """A second hook firing on the same transcript must not re-post
    attachments we've already turned into spans."""
    monkeypatch.setenv('REGIN_TURN_TRACE_STATE_DIR', str(tmp_path / '_state'))
    transcript = tmp_path / 'session.jsonl'
    _write_transcript(transcript, [
        {'type': 'user', 'uuid': 'u1', 'message': {'content': 'go'}},
        _assistant_with_usage(msg_id='m1', uuid='asst-att-once-uuid', parent_uuid='u1'),
        {'type': 'attachment', 'uuid': 'att-once-xyzzz',
         'timestamp': '2026-04-27T12:00:08Z',
         'attachment': {'type': 'task_reminder', 'itemCount': 1, 'content': ['x']}},
    ])
    turn_trace.handle(_p('UserPromptSubmit', session_id='att-s2',
                         transcript_path=str(transcript)))
    assert any(s.get('name') == 'harness.task_reminder' for s in captured_spans)
    captured_spans.clear()
    turn_trace.handle(_p('UserPromptSubmit', session_id='att-s2',
                         transcript_path=str(transcript)))
    assert [s for s in captured_spans if s.get('name') == 'harness.task_reminder'] == []


def test_failed_post_does_not_mark_seen(tmp_path, monkeypatch):
    """A turn whose post_span returns False (transient ingest failure)
    must NOT be cached as seen — otherwise the next hook fire would
    skip it forever and permanently lose the span. This is the bug
    that drained whole sessions of assistant_response spans in
    real-world traces (see docs/trace/assistant_response_capture_vs_claudecodeui.md
    and the `lib.trace.repair` recovery path).

    Mocks post_span to return False on the first invocation and True
    on the second so we can verify the second hook fire re-emits.
    """
    monkeypatch.setenv('REGIN_TURN_TRACE_STATE_DIR', str(tmp_path / '_state'))
    import lib.hook_plugin as hp
    spans: list[dict] = []
    # Track which assistant_response posts to fail. Failing only the
    # named post we care about (vs. failing all spans on the first
    # call) lets us assert that the cache mark is gated on THIS post's
    # success, not on some unrelated `turn` or `session.title` post
    # that also happens to run.
    failed_for: set[str] = set()

    def _flaky_post(**kw):
        spans.append(kw)
        if kw.get('name') == 'assistant_response' and kw.get('span_id') not in failed_for:
            failed_for.add(kw.get('span_id'))
            return False
        return True

    monkeypatch.setattr(hp, 'post_span', _flaky_post)

    transcript = tmp_path / 'session.jsonl'
    user_uuid = 'user-uuid-flaky-post12'
    turn_uuid = 'asst-uuid-flaky-post01'
    _write_transcript(transcript, [
        {'type': 'user', 'uuid': user_uuid, 'message': {'content': 'go'}},
        _assistant_with_usage(msg_id='m1', text='hello world',
                              uuid=turn_uuid, parent_uuid=user_uuid),
    ])

    # First fire — post fails — uuid must NOT be cached.
    turn_trace.handle(_p('UserPromptSubmit', session_id='s-flaky',
                         transcript_path=str(transcript)))
    first_response = [s for s in spans if s.get('name') == 'assistant_response']
    assert len(first_response) == 1, 'first fire should attempt the post'

    spans.clear()
    # Second fire — post succeeds — span must be re-emitted (proving
    # the failed first attempt didn't lock the uuid out).
    turn_trace.handle(_p('UserPromptSubmit', session_id='s-flaky',
                         transcript_path=str(transcript)))
    second_response = [s for s in spans if s.get('name') == 'assistant_response']
    assert len(second_response) == 1, (
        'second fire must retry the post; got '
        f'{len(second_response)} assistant_response spans'
    )

    spans.clear()
    # Third fire — already succeeded — must be throttled (uuid is
    # cached now). Confirms the success path still bounds repeated
    # posts so we don't spam the ingest.
    turn_trace.handle(_p('UserPromptSubmit', session_id='s-flaky',
                         transcript_path=str(transcript)))
    third_response = [s for s in spans if s.get('name') == 'assistant_response']
    assert third_response == [], 'cached uuid must throttle the third fire'


# ── prompt turn-anchor spans (off-by-one regression) ────────────────

def _user(uuid, content, parent=None, ts='2026-04-27T12:00:00Z'):
    return {'type': 'user', 'uuid': uuid, 'parentUuid': parent,
            'timestamp': ts, 'message': {'content': content}}


def _prompt_spans_by_uuid(spans):
    return {s['span_id']: s for s in spans if s.get('name') == 'prompt'}


def test_prompt_anchor_carries_its_own_text_not_the_next_prompts(
    captured_spans, tmp_path, monkeypatch,
):
    """Regression for the off-by-one prompt corruption: each
    `prompt-<uuid>` anchor must carry the text of the prompt that uuid
    belongs to, never the *next* prompt's text. turn_trace owns the
    anchor and keys it off each turn's `prompt_uuid`, so three prompts
    A→B→C produce three anchors with A/B/C text respectively — and no
    duplicate where two anchors share one text."""
    monkeypatch.setenv('REGIN_TURN_TRACE_STATE_DIR', str(tmp_path / '_state'))
    transcript = tmp_path / 'session.jsonl'
    ua, ub, uc = 'user-aaaa0000aaaa', 'user-bbbb1111bbbb', 'user-cccc2222cccc'
    _write_transcript(transcript, [
        _user(ua, 'first question'),
        _assistant_with_usage(msg_id='m1', text='answer A',
                              uuid='asst-a0000000000', parent_uuid=ua),
        _user(ub, 'second question', parent='asst-a0000000000'),
        _assistant_with_usage(msg_id='m2', text='answer B',
                              uuid='asst-b1111111111', parent_uuid=ub),
        _user(uc, 'third question', parent='asst-b1111111111'),
        _assistant_with_usage(msg_id='m3', text='answer C',
                              uuid='asst-c2222222222', parent_uuid=uc),
    ])
    turn_trace.handle(_p('UserPromptSubmit', session_id='s1',
                         transcript_path=str(transcript)))
    prompts = _prompt_spans_by_uuid(captured_spans)
    assert prompts[f'prompt-{ua[:13]}']['attributes']['text'] == 'first question'
    assert prompts[f'prompt-{ub[:13]}']['attributes']['text'] == 'second question'
    assert prompts[f'prompt-{uc[:13]}']['attributes']['text'] == 'third question'
    # No duplicate-text anchors (the "go ahead and build the 3 commits"
    # symptom): three distinct anchors, three distinct texts.
    texts = [s['attributes']['text'] for s in prompts.values()]
    assert sorted(texts) == ['first question', 'second question', 'third question']


def test_prompt_anchor_uses_prompt_entry_timestamp_not_now(
    captured_spans, tmp_path, monkeypatch,
):
    """The anchor's start_time must be the prompt entry's transcript
    timestamp (so it sorts at submission time), not the re-emission
    time — otherwise a later anchor mis-sorts ahead of an earlier one."""
    monkeypatch.setenv('REGIN_TURN_TRACE_STATE_DIR', str(tmp_path / '_state'))
    transcript = tmp_path / 'session.jsonl'
    ua = 'user-tttt0000tttt'
    _write_transcript(transcript, [
        _user(ua, 'do it', ts='2026-04-27T09:15:00Z'),
        _assistant_with_usage(msg_id='m1', text='ok', uuid='asst-t0000000000',
                              parent_uuid=ua, ts='2026-04-27T09:15:02Z'),
    ])
    turn_trace.handle(_p('UserPromptSubmit', session_id='s1',
                         transcript_path=str(transcript)))
    anchor = _prompt_spans_by_uuid(captured_spans)[f'prompt-{ua[:13]}']
    # start_time must equal the prompt entry's transcript time (normalised
    # to local-naive the same way the handler does), not a fresh now().
    from hook_manager.handlers.turn_trace.timestamps import _normalise_attachment_ts
    expected = _normalise_attachment_ts('2026-04-27T09:15:00Z')
    assert anchor['start_time'] == expected
    assert anchor['end_time'] == expected


def test_prompt_anchor_carries_prompt_id_from_transcript(
    captured_spans, tmp_path, monkeypatch,
):
    """The transcript's `promptId` on the user-prompt entry (normalized to
    `prompt_id`, snake-cased by `_normalize_dict_keys`) rides through the real
    scan+poster path onto the emitted `prompt-<uuid>` anchor's
    `attributes.prompt_id` — the CLI ground truth `_rung0_source_prompt_parent`
    (lib/trace/projection.py) value-joins a tool span's `source_prompt_id`
    against (design Move 1b)."""
    monkeypatch.setenv('REGIN_TURN_TRACE_STATE_DIR', str(tmp_path / '_state'))
    transcript = tmp_path / 'session.jsonl'
    ua = 'user-pppp0000pppp'
    pid = 'pr-live-submission-abc'
    _write_transcript(transcript, [
        {'type': 'user', 'uuid': ua, 'parentUuid': None, 'promptId': pid,
         'timestamp': '2026-04-27T12:00:00Z',
         'message': {'content': 'do the thing'}},
        _assistant_with_usage(msg_id='m1', text='ok', uuid='asst-p0000000000',
                              parent_uuid=ua),
    ])
    turn_trace.handle(_p('UserPromptSubmit', session_id='s1',
                         transcript_path=str(transcript)))
    anchor = _prompt_spans_by_uuid(captured_spans)[f'prompt-{ua[:13]}']
    assert anchor['attributes']['prompt_id'] == pid


def test_prompt_anchor_detects_slash_command(captured_spans, tmp_path, monkeypatch):
    monkeypatch.setenv('REGIN_TURN_TRACE_STATE_DIR', str(tmp_path / '_state'))
    transcript = tmp_path / 'session.jsonl'
    ua = 'user-ssss0000ssss'
    _write_transcript(transcript, [
        _user(ua, [{'type': 'text', 'text': '/plan ship it'}]),
        _assistant_with_usage(msg_id='m1', text='ok', uuid='asst-s0000000000',
                              parent_uuid=ua),
    ])
    turn_trace.handle(_p('UserPromptSubmit', session_id='s1',
                         transcript_path=str(transcript)))
    anchor = _prompt_spans_by_uuid(captured_spans)[f'prompt-{ua[:13]}']
    assert anchor['attributes']['slash_command'] == '/plan'


def test_prompt_anchor_attaches_inline_images_to_correct_anchor(
    captured_spans, tmp_path, monkeypatch,
):
    """An image prompt's `prompt_images` must be keyed to that prompt's
    own `prompt-<uuid>` anchor (resolved from the inline base64 fallback
    when the cache is gone), not the previous prompt's."""
    monkeypatch.setenv('REGIN_TURN_TRACE_STATE_DIR', str(tmp_path / '_state'))
    events: list = []
    import lib.hook_plugin as hp
    monkeypatch.setattr(hp, 'post_event', lambda name, data: events.append((name, data)))
    transcript = tmp_path / 'session.jsonl'
    ua = 'user-iiii0000iiii'
    _write_transcript(transcript, [
        _user(ua, [
            {'type': 'text', 'text': 'look at [Image #1]'},
            {'type': 'image',
             'source': {'type': 'base64', 'media_type': 'image/png', 'data': 'QUJD'}},
        ]),
        _assistant_with_usage(msg_id='m1', text='nice', uuid='asst-i0000000000',
                              parent_uuid=ua),
    ])
    turn_trace.handle(_p('UserPromptSubmit', session_id='s1',
                         transcript_path=str(transcript)))
    image_events = [d for name, d in events if name == 'prompt_images']
    assert image_events, 'prompt_images event must fire for an image prompt'
    rows = image_events[0]
    assert all(r['prompt_span_id'] == f'prompt-{ua[:13]}' for r in rows)
    assert rows[0]['idx'] == 1
    assert rows[0]['media_type'] == 'image/png'
    assert rows[0]['data_b64'] == 'QUJD'


# ── Turn-initiating slash commands (e.g. /review) ─────────────────────────
# A skill command that expands into a prompt must render as its OWN turn
# anchor (`prompt-<uuid>`), never a `harness.local_command` card — and the
# transition must survive the incremental live firings (the command echo is
# suppressed from the very first firing, so its uuid is never marked seen as
# a local command, so the later anchor isn't throttled away).

def _ut_user(uuid, content, parent, meta=False):
    e = {'type': 'user', 'uuid': uuid, 'parentUuid': parent,
         'timestamp': '2026-06-01T10:00:00Z', 'message': {'content': content}}
    if meta:
        e['isMeta'] = True
    return e


def _ut_asst(uuid, parent, text='ok'):
    return {'type': 'assistant', 'uuid': uuid, 'parentUuid': parent,
            'timestamp': '2026-06-01T10:00:00Z',
            'message': {'id': 'm-' + uuid, 'model': 'claude-opus-4-8',
                        'content': [{'type': 'text', 'text': text}],
                        'usage': {'input_tokens': 10, 'output_tokens': 5,
                                  'cache_read_input_tokens': 0,
                                  'cache_creation_input_tokens': 0}}}


def _local_command_named(spans, name):
    return any(s.get('name') == 'harness.local_command'
               and (s.get('attributes') or {}).get('command_name') == name
               for s in spans)


def _prompt_anchor_with_text(spans, text):
    return next((s for s in spans
                 if s.get('name') == 'prompt'
                 and (s.get('attributes') or {}).get('text') == text), None)


def test_review_command_anchors_not_local_command_across_firings(
        captured_spans, tmp_path, monkeypatch):
    monkeypatch.setenv('REGIN_TURN_TRACE_STATE_DIR', str(tmp_path / 'state'))
    transcript = tmp_path / 'session.jsonl'
    base = [
        _ut_user('p0', 'first typed prompt', None),
        _ut_asst('a0', 'p0', text='answering first'),
        _ut_user('cmd', '<command-message>review</command-message> '
                 '<command-name>/review</command-name>', 'a0'),
        _ut_user('exp', 'You are an expert code reviewer...', 'cmd', meta=True),
    ]
    # Firing 1: /review submitted (echo + expansion present), no response yet.
    _write_transcript(transcript, base)
    turn_trace.handle(_p('UserPromptSubmit', session_id='s1',
                         transcript_path=str(transcript)))
    # Must NOT have emitted a /review local-command card — otherwise its uuid
    # gets marked seen and the anchor below would be throttled away.
    assert not _local_command_named(captured_spans, '/review')

    # Firing 2: the /review response lands.
    _write_transcript(transcript, base + [_ut_asst('a1', 'exp',
                                                   text='doing the review')])
    turn_trace.handle(_p('Stop', session_id='s1',
                         transcript_path=str(transcript)))
    # The /review turn now anchors on a prompt span with a friendly label,
    # and there is still no duplicate local-command card.
    anchor = _prompt_anchor_with_text(captured_spans, '/review')
    assert anchor is not None
    assert anchor['span_id'].startswith('prompt-')
    assert not _local_command_named(captured_spans, '/review')


def test_display_command_stays_local_command(captured_spans, tmp_path,
                                             monkeypatch):
    # /clear has no isMeta expansion — it must still emit as a local command.
    monkeypatch.setenv('REGIN_TURN_TRACE_STATE_DIR', str(tmp_path / 'state'))
    transcript = tmp_path / 'session.jsonl'
    _write_transcript(transcript, [
        _ut_user('p0', 'first typed prompt', None),
        _ut_asst('a0', 'p0'),
        _ut_user('clr', '<command-name>/clear</command-name>', 'a0'),
    ])
    turn_trace.handle(_p('Stop', session_id='s1',
                         transcript_path=str(transcript)))
    assert _local_command_named(captured_spans, '/clear')
    assert _prompt_anchor_with_text(captured_spans, '/clear') is None


def test_command_with_stdout_before_expansion_anchors(captured_spans, tmp_path,
                                                      monkeypatch):
    # /goal prints a `<local-command-stdout>` line ("Goal set: …") between its
    # echo and the isMeta expansion (a Stop-hook injection), so the
    # expansion's parent is the STDOUT entry, not the command. The anchor gate
    # must bridge that gap: /goal still anchors as its own turn, its response
    # parents under it, and it does not also emit a local-command card (which
    # would leave the live promptlive placeholder un-superseded).
    monkeypatch.setenv('REGIN_TURN_TRACE_STATE_DIR', str(tmp_path / 'state'))
    transcript = tmp_path / 'session.jsonl'
    _write_transcript(transcript, [
        _ut_user('p0', 'continue to refactor', None),
        _ut_asst('a0', 'p0', text='refactoring'),
        _ut_user('cmd', '<command-message>goal</command-message> '
                 '<command-name>/goal</command-name> '
                 '<command-args>ship it</command-args>', 'a0'),
        _ut_user('out', '<local-command-stdout>Goal set: ship it'
                 '</local-command-stdout>', 'cmd'),
        _ut_user('exp', 'A session-scoped Stop hook is now active...', 'out',
                 meta=True),
        _ut_asst('a1', 'exp', text='Goal acknowledged'),
    ])
    turn_trace.handle(_p('Stop', session_id='s1',
                         transcript_path=str(transcript)))

    anchor = _prompt_anchor_with_text(captured_spans, '/goal ship it')
    assert anchor is not None, 'a stdout-then-expansion command must anchor'
    assert anchor['span_id'] == 'prompt-cmd'
    # No duplicate local-command card (its uuid would block placeholder drop).
    assert not _local_command_named(captured_spans, '/goal')
    # The response nests under the /goal anchor, not the prior typed prompt.
    resp = next(s for s in captured_spans if s.get('span_id') == 'resp-a1')
    assert resp['parent_id'] == 'prompt-cmd'
