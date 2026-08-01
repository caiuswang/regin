"""A regin-launched SDK run is traced twice; the read path serves it once.

The runner synthesizes spans from the SDK message stream under `sdk-<hex>`,
while the child `claude` — which loads the user's hooks — writes a second trace
under its own session id. Neither is redundant: only the SDK stream is live,
only the hook trace carries `rule.check`/`turn`/`cwd.changed`.

Guards the three ways this goes wrong: the union double-renders every event,
the dedup drops the *live* row and reintroduces hook latency, and the whole
mechanism leaks into ordinary single-writer sessions.
"""

from __future__ import annotations

import sqlite3

from lib.agent_sdk.store import heal_cli_session_ids
from lib.trace.alias import ORIGIN_KEY, trace_group
from lib.trace.merge import merge_spans

SDK = 'sdk-deadbeef'
HOOK = '11111111-2222-3333-4444-555555555555'


def _row(span_id, name, attrs, *, origin, start, id, status='UNSET',
         tool_use_id=None):
    return {
        'id': id, 'trace_id': HOOK, 'span_id': span_id, 'parent_id': None,
        'name': name, 'kind': 'internal',
        'start_time': start, 'end_time': None, 'duration_ms': 0,
        'status_code': status, 'status_message': None,
        'attributes': attrs, 'turn_uuid': None,
        'tool_use_id': tool_use_id, ORIGIN_KEY: origin,
    }


def _alias_db() -> sqlite3.Connection:
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE agent_runs (
        trace_id TEXT, cli_session_id TEXT, status TEXT, cwd TEXT)""")
    conn.execute("""CREATE TABLE sessions (
        trace_id TEXT, started_at TEXT, cwd TEXT, title TEXT)""")
    conn.execute("""CREATE TABLE session_spans (
        trace_id TEXT, span_id TEXT, tool_use_id TEXT, attributes TEXT)""")
    return conn


# ── resolution ───────────────────────────────────────────────────────

def test_either_id_resolves_to_the_same_group_canonical_first():
    """Both ids are legitimate entry points — `/live` links carry the run's."""
    conn = _alias_db()
    conn.execute("INSERT INTO agent_runs VALUES (?,?,'exited','/w')",
                 (SDK, HOOK))
    assert trace_group(conn, HOOK) == [HOOK, SDK]
    assert trace_group(conn, SDK) == [HOOK, SDK]


def test_unaliased_session_is_its_own_group():
    """The 99% case must stay a single-trace read."""
    assert trace_group(_alias_db(), 'plain-session') == ['plain-session']


def test_run_without_a_child_yet_is_not_a_group():
    """`cli_session_id` is NULL until the child names itself; that is not an
    error and must not resolve to a half-built group."""
    conn = _alias_db()
    conn.execute("INSERT INTO agent_runs VALUES (?,NULL,'running','/w')",
                 (SDK,))
    assert trace_group(conn, SDK) == [SDK]


# ── dedup ────────────────────────────────────────────────────────────

def test_single_writer_window_is_untouched():
    """No `sdk` origin → every cross-source rule is a no-op.

    Compares the mixed-origin result against the SAME rows with the sdk half
    removed, so it actually exercises the guard rather than diffing a list
    against a copy of itself.
    """
    hook_only = [
        _row('h1', 'tool.Bash', {'command': 'ls'}, origin='hook',
             start='2026-08-01T10:00:00', id=1, tool_use_id='toolu_1'),
        _row('h2', 'assistant.thinking', {'thinking_signature_bytes': 40,
                                          'thinking_blocks': 1,
                                          'output_tokens': 9},
             origin='hook', start='2026-08-01T10:00:02', id=2),
    ]
    assert merge_spans([dict(r) for r in hook_only]) == hook_only


def test_thinking_dedups_without_any_text_attribute():
    """Neither writer stores reasoning verbatim — `assistant.thinking` carries
    a signature and block count, never `text`. Keying it on text matched
    nothing and left every reasoning card rendered twice."""
    shape = {'thinking_signature_bytes': 512, 'thinking_blocks': 1,
             'output_tokens': 353}
    rows = [
        _row('sdk-th', 'assistant.thinking', dict(shape), origin='sdk',
             start='2026-07-31T22:17:21.521000', id=1),
        _row('hook-th', 'assistant.thinking', {**shape, 'turn_index': 48},
             origin='hook', start='2026-07-31T22:17:21.513000', id=2),
    ]
    merged = merge_spans(rows)
    assert [s['span_id'] for s in merged] == ['hook-th']


def test_two_genuinely_separate_thinking_blocks_both_survive():
    """Same shape, minutes apart — two real reasoning steps, not one."""
    shape = {'thinking_signature_bytes': 512, 'thinking_blocks': 1,
             'output_tokens': 353}
    rows = [
        _row('sdk-a', 'assistant.thinking', dict(shape), origin='sdk',
             start='2026-07-31T22:00:00', id=1),
        _row('hook-a', 'assistant.thinking', dict(shape), origin='hook',
             start='2026-07-31T22:00:00', id=2),
        _row('sdk-b', 'assistant.thinking', dict(shape), origin='sdk',
             start='2026-07-31T22:30:00', id=3),
        _row('hook-b', 'assistant.thinking', dict(shape), origin='hook',
             start='2026-07-31T22:30:00', id=4),
    ]
    assert [s['span_id'] for s in merge_spans(rows)] == ['hook-a', 'hook-b']


def test_the_same_prompt_typed_twice_stays_two_prompts():
    """`continue` typed twice is two turns. A global text key collapsed them
    into one and deleted an entire turn; 151 real traces have a repeated
    prompt, one of them 8 times."""
    rows = [
        _row('sdk-p1', 'prompt', {'text': 'continue'}, origin='sdk',
             start='2026-08-01T10:00:00', id=1),
        _row('prompt-p1', 'prompt', {'text': 'continue'}, origin='hook',
             start='2026-08-01T10:00:00', id=2),
        _row('sdk-p2', 'prompt', {'text': 'continue'}, origin='sdk',
             start='2026-08-01T10:20:00', id=3),
        _row('prompt-p2', 'prompt', {'text': 'continue'}, origin='hook',
             start='2026-08-01T10:20:00', id=4),
    ]
    assert [s['span_id'] for s in merge_spans(rows)] == ['prompt-p1', 'prompt-p2']


def test_repeated_lifecycle_markers_are_not_collapsed():
    """A resumed session emits several `session.start`s — 117 real traces do,
    one 42 times. Keying on the name alone left exactly one."""
    rows = [
        _row('sdk-s1', 'session.start', {}, origin='sdk',
             start='2026-08-01T10:00:00', id=1),
        _row('hook-s1', 'session.start', {'source': 'startup'}, origin='hook',
             start='2026-08-01T10:00:00', id=2),
        _row('sdk-s2', 'session.start', {}, origin='sdk',
             start='2026-08-01T11:00:00', id=3),
        _row('hook-s2', 'session.start', {'source': 'resume'}, origin='hook',
             start='2026-08-01T11:00:00', id=4),
    ]
    merged = merge_spans(rows)
    assert [s['span_id'] for s in merged] == ['hook-s1', 'hook-s2']
    assert [s['attributes']['source'] for s in merged] == ['startup', 'resume']


def test_bare_slash_command_renders_one_prompt_not_two():
    """`_absorb_slash_command_expansions` runs first and rewrites the hook
    anchor's text to the full expansion, while the SDK row still holds the bare
    echo. Keying on raw text put them in different buckets and rendered the
    turn anchor twice — and `prompt` is a turn anchor, so that is a phantom
    turn."""
    rows = [
        _row('promptlive-x', 'prompt',
             {'text': '/goal-verified\n\nYou are the builder…'},
             origin='hook', status='PENDING',
             start='2026-08-01T10:00:00', id=1),
        _row('sdkprompt', 'prompt', {'text': '/goal-verified'}, origin='sdk',
             start='2026-08-01T10:00:01', id=2),
        _row('prompt-abc', 'prompt', {'text': '/goal-verified'}, origin='hook',
             start='2026-08-01T10:00:02', id=3),
    ]
    prompts = [s for s in merge_spans(rows) if s['name'] == 'prompt']
    assert [s['span_id'] for s in prompts] == ['prompt-abc']
    # The expansion still lands on the canonical anchor, not the SDK row.
    assert prompts[0]['attributes']['text'].startswith('/goal-verified\n\nYou')


def test_many_rows_under_one_tool_use_id_all_collapse():
    """A `tool_use_id` names the CALL, not the row — real traces carry up to 31
    rows under one. Pairing only the first left the rest as duplicates."""
    def call(sid, origin, n):
        return _row(sid, 'tool.Bash', {}, origin=origin,
                    start=f'2026-08-01T10:00:0{n}', id=n, tool_use_id='toolu_x')
    rows = [call('h1', 'hook', 1), call('h2', 'hook', 2),
            call('s1', 'sdk', 3), call('s2', 'sdk', 4)]
    merged = merge_spans(rows)
    assert [s['span_id'] for s in merged] == ['h1', 'h2']


def test_plain_prompt_does_not_double_render_via_a_leftover_placeholder():
    """A non-slash prompt gets no `_absorb_slash_command_expansions` pass, so
    the hook's `promptlive-` placeholder can still be in the window. Pairing it
    positionally let it consume the SDK row — it wins for being resolved — and
    left the hook's real anchor unpaired and rendered a second time."""
    rows = [
        _row('promptlive-y', 'prompt', {'text': 'hello world'}, origin='hook',
             status='PENDING', start='2026-08-01T10:00:00', id=1),
        _row('sdkprompt', 'prompt', {'text': 'hello world'}, origin='sdk',
             start='2026-08-01T10:00:01', id=2),
        _row('prompt-ghi', 'prompt', {'text': 'hello world'}, origin='hook',
             start='2026-08-01T10:00:02', id=3),
    ]
    prompts = [s for s in merge_spans(rows) if s['name'] == 'prompt']
    assert [s['span_id'] for s in prompts] == ['prompt-ghi']


def test_liveness_survives_the_placeholder_filter():
    """When the hook side has ONLY a placeholder — nothing resolved yet — the
    SDK's resolved row must still pair and win, or the filter above would undo
    the whole point of reading the SDK stream."""
    rows = [
        _row('pending-toolu_7', 'tool.Bash', {}, origin='hook',
             status='PENDING', start='2026-08-01T10:00:00', id=1,
             tool_use_id='toolu_7'),
        _row('sdk-done', 'tool.Bash', {'command': 'ls'}, origin='sdk',
             start='2026-08-01T10:00:00', id=2, tool_use_id='toolu_7'),
    ]
    assert [s['span_id'] for s in merge_spans(rows)] == ['sdk-done']


def test_an_event_only_one_writer_saw_survives_unpaired():
    """The reason both traces are kept at all."""
    rows = [
        _row('hook-only', 'rule.check', {'rule': 'x'}, origin='hook',
             start='2026-08-01T10:00:00', id=1),
        _row('sdk-t', 'tool.Bash', {}, origin='sdk',
             start='2026-08-01T10:00:01', id=2, tool_use_id='toolu_solo'),
    ]
    assert len(merge_spans(rows)) == 2


def test_origin_marker_never_reaches_the_client():
    from lib.trace.alias import strip_origin
    rows = [_row('h1', 'tool.Bash', {}, origin='hook',
                 start='2026-08-01T10:00:00', id=1, tool_use_id='t1')]
    assert all(ORIGIN_KEY not in s for s in strip_origin(rows))


def test_blank_trace_id_reads_as_an_empty_session_not_a_crash():
    """Callers index `group[0]`; a blank id used to return [] and IndexError."""
    assert trace_group(_alias_db(), '') == ['']


def test_same_tool_call_from_both_writers_renders_once():
    rows = [
        _row('pending-toolu_1', 'tool.Bash', {}, origin='sdk',
             start='2026-08-01T10:00:00', id=1, tool_use_id='toolu_1'),
        _row('hook-1', 'tool.Bash', {'command': 'ls'}, origin='hook',
             start='2026-08-01T10:00:00', id=2, tool_use_id='toolu_1'),
    ]
    merged = merge_spans(rows)
    assert [s['span_id'] for s in merged] == ['hook-1']


def test_resolved_sdk_row_beats_a_still_pending_hook_row():
    """The liveness guarantee. The SDK span lands when the model emits it; the
    hook twin can still be a placeholder waiting on the next hook to fire.
    Preferring the hook writer unconditionally would throw that away."""
    rows = [
        _row('sdk-resolved', 'tool.Bash', {'command': 'ls'}, origin='sdk',
             start='2026-08-01T10:00:00', id=1, tool_use_id='toolu_9'),
        _row('pending-toolu_9', 'tool.Bash', {}, origin='hook',
             status='PENDING', start='2026-08-01T10:00:00', id=2,
             tool_use_id='toolu_9'),
    ]
    assert [s['span_id'] for s in merge_spans(rows)] == ['sdk-resolved']


def test_distinct_responses_from_the_two_writers_are_not_collapsed():
    """`assistant_response` DOES carry text on both writers, so it keys on it."""
    rows = [
        _row('sdk-t', 'assistant_response', {'text': 'first'}, origin='sdk',
             start='2026-08-01T10:00:01', id=1),
        _row('hook-t', 'assistant_response', {'text': 'second'},
             origin='hook', start='2026-08-01T10:00:02', id=2),
    ]
    assert len(merge_spans(rows)) == 2


def test_specific_stop_reason_survives_the_generic_one():
    """The child's hook reports 'other' for a run the SDK ended deliberately.
    Dropping the SDK row wholesale would delete the only record of WHY the
    session stopped — the question that motivated this whole merge."""
    rows = [
        _row('sdk-end', 'session.end', {'reason': 'idle timeout'},
             origin='sdk', start='2026-08-01T10:30:00', id=1),
        _row('hook-end', 'session.end', {'reason': 'other', 'cwd': '/w'},
             origin='hook', start='2026-08-01T10:30:01', id=2),
    ]
    merged = merge_spans(rows)
    assert len(merged) == 1
    # Hook row survives (richer), but wearing the reason only the SDK knew.
    assert merged[0]['span_id'] == 'hook-end'
    assert merged[0]['attributes']['reason'] == 'idle timeout'
    assert merged[0]['attributes']['cwd'] == '/w'


def test_dict_valued_attributes_do_not_crash_the_fill():
    """Attributes are arbitrary JSON; an unhashable value must not raise."""
    rows = [
        _row('sdk-x', 'tool.Bash', {'tool_input': {'command': 'ls'}},
             origin='sdk', start='2026-08-01T10:00:00', id=1,
             tool_use_id='toolu_5'),
        _row('hook-x', 'tool.Bash', {'questions': [{'q': 1}]}, origin='hook',
             start='2026-08-01T10:00:00', id=2, tool_use_id='toolu_5'),
    ]
    merged = merge_spans(rows)
    assert merged[0]['attributes']['tool_input'] == {'command': 'ls'}


# ── one message, two writers ─────────────────────────────────────────

def test_one_message_pairs_however_long_the_generation_took():
    """The gap between the two writers is a GENERATION, not clock skew.

    One API message is written to the transcript as several entries and the two
    writers latch onto different ones, so their stamps sit as far apart as the
    answer took to produce — measured +0.03s at 73 chars up to +16.03s at 3418.
    A 5s bound therefore duplicated the majority of long responses (7 of 13 in
    one real session); `message_id` pairs them outright.
    """
    rows = [
        _row('hook-r', 'assistant_response',
             {'text': 'x' * 3418, 'message_id': 'msg_01'}, origin='hook',
             start='2026-08-01T10:00:00', id=1),
        _row('sdk-r', 'assistant_response',
             {'text': 'x' * 3418, 'message_id': 'msg_01'}, origin='sdk',
             start='2026-08-01T10:00:16', id=2),
    ]
    assert [s['span_id'] for s in merge_spans(rows)] == ['hook-r']


def test_two_messages_are_never_paired_however_alike():
    """The other direction: identical text under two ids is two emissions."""
    rows = [
        _row('hook-a', 'assistant_response',
             {'text': 'Done.', 'message_id': 'msg_01'}, origin='hook',
             start='2026-08-01T10:00:00', id=1),
        _row('sdk-b', 'assistant_response',
             {'text': 'Done.', 'message_id': 'msg_02'}, origin='sdk',
             start='2026-08-01T10:00:00', id=2),
    ]
    assert len(merge_spans(rows)) == 2


def test_a_long_response_predating_message_id_still_pairs():
    """Rows written before the id was captured must still heal, so the text
    itself carries the identity once it is long enough to be one."""
    rows = [
        _row('hook-r', 'assistant_response', {'text': 'y' * 2006},
             origin='hook', start='2026-08-01T10:00:00', id=1),
        _row('sdk-r', 'assistant_response', {'text': 'y' * 2006},
             origin='sdk', start='2026-08-01T10:00:09', id=2),
    ]
    assert [s['span_id'] for s in merge_spans(rows)] == ['hook-r']


def test_a_short_reply_repeated_without_an_id_stays_two():
    """The limit of the text fallback: "Done." is not an identity, so two of
    them 20 minutes apart keep the time bound and stay two cards."""
    rows = [
        _row('hook-1', 'assistant_response', {'text': 'Done.'},
             origin='hook', start='2026-08-01T10:00:00', id=1),
        _row('sdk-2', 'assistant_response', {'text': 'Done.'},
             origin='sdk', start='2026-08-01T10:20:00', id=2),
    ]
    assert len(merge_spans(rows)) == 2


def test_a_failed_call_renders_once_not_once_per_writer():
    """A failure has two shapes: the hook names the span `tool.failure` and puts
    the tool in `attrs.tool_name`; the SDK keeps `tool.<Name>` and sets ERROR.
    Keying on the raw name filed one call under two keys."""
    rows = [
        _row('hook-f', 'tool.failure',
             {'tool_name': 'Bash', 'error': 'Exit code 1', 'command': 'ls'},
             origin='hook', status='ERROR', start='2026-08-01T10:00:00', id=1,
             tool_use_id='toolu_7'),
        _row('sdk-f', 'tool.Bash', {'tool_name': 'Bash', 'error': 'Exit code 1'},
             origin='sdk', status='ERROR', start='2026-08-01T10:00:00', id=2,
             tool_use_id='toolu_7'),
    ]
    merged = merge_spans(rows)
    assert [s['span_id'] for s in merged] == ['hook-f']
    assert merged[0]['attributes']['error'] == 'Exit code 1'


def test_permission_request_pairs_when_only_the_sdk_knows_the_call_id():
    """Claude Code's PermissionRequest payload carries no `tool_use_id` (32 of
    947 real hook rows have one), so keying on it filed the two writers apart
    and paired neither."""
    rows = [
        _row('permreq-toolu_3', 'permission.request',
             {'tool_name': 'AskUserQuestion', 'tool_use_id': 'toolu_3'},
             origin='sdk', start='2026-08-01T10:00:00', id=1),
        _row('hook-perm', 'permission.request',
             {'tool_name': 'AskUserQuestion', 'option_count': 1},
             origin='hook', start='2026-08-01T10:00:02', id=2),
    ]
    assert [s['span_id'] for s in merge_spans(rows)] == ['hook-perm']


def test_repeated_requests_for_one_tool_are_not_collapsed():
    """Asking the same tool four more times is four more cards — only the
    writer's twin may be dropped."""
    rows = [
        _row('permreq-toolu_3', 'permission.request',
             {'tool_name': 'Bash', 'tool_use_id': 'toolu_3'}, origin='sdk',
             start='2026-08-01T10:00:00', id=1),
    ] + [
        _row(f'hook-perm-{n}', 'permission.request', {'tool_name': 'Bash'},
             origin='hook', start=f'2026-08-01T10:0{n}:02', id=10 + n)
        for n in range(5)
    ]
    merged = merge_spans(rows)
    assert len(merged) == 5
    assert all(s.get(ORIGIN_KEY) == 'hook' for s in merged)


def test_lifecycle_markers_pair_across_a_slow_child_exit():
    """The SDK ends the run, then the child process exits and its hook fires —
    5.5s later in a real session, which the 5s bound refused, rendering the end
    twice. Lifecycle markers get a ceiling sized for process teardown instead;
    `test_lifecycle_markers_hours_apart_are_never_fused` pins the other end."""
    rows = [
        _row('sdk-end', 'session.end', {'reason': 'idle timeout'},
             origin='sdk', start='2026-08-01T10:23:20', id=1),
        _row('hook-end', 'session.end', {'reason': 'other'}, origin='hook',
             start='2026-08-01T10:23:26', id=2),
    ]
    merged = merge_spans(rows)
    assert [s['span_id'] for s in merged] == ['hook-end']
    assert merged[0]['attributes']['reason'] == 'idle timeout'


def test_one_writer_stamping_the_id_first_does_not_double_render():
    """The upgrade state, and why the identity is not part of the bucket key.

    Rows stamped with `message_id` and rows predating it coexist for as long as
    a session spans the deploy — and a subagent turn reaches the SDK writer
    with an id while `subagent_lifecycle` had none. Bucketing on the id filed
    each row apart from its own twin, into a single-origin bucket that
    reconciles to itself: the exact duplicate this module exists to remove.
    """
    for attrs_hook, attrs_sdk in (
        ({'text': 'hi', 'message_id': 'msg_01'}, {'text': 'hi'}),
        ({'text': 'hi'}, {'text': 'hi', 'message_id': 'msg_01'}),
    ):
        rows = [
            _row('hook-r', 'assistant_response', attrs_hook, origin='hook',
                 start='2026-08-01T10:00:00', id=1),
            _row('sdk-r', 'assistant_response', attrs_sdk, origin='sdk',
                 start='2026-08-01T10:00:00', id=2),
        ]
        assert [s['span_id'] for s in merge_spans(rows)] == ['hook-r']


def test_two_requests_from_one_tool_block_both_survive():
    """Differing ids outrank proximity. A parallel tool block gates two calls
    seconds apart under one tool name — pairing them on the name alone deleted
    the second outright."""
    rows = [
        _row('hook-p', 'permission.request',
             {'tool_name': 'Bash', 'tool_use_id': 'toolu_A'}, origin='hook',
             start='2026-08-01T10:00:00', id=1),
        _row('permreq-toolu_B', 'permission.request',
             {'tool_name': 'Bash', 'tool_use_id': 'toolu_B'}, origin='sdk',
             start='2026-08-01T10:00:02', id=2),
    ]
    assert len(merge_spans(rows)) == 2


def test_one_request_pairs_however_late_the_answer_came():
    """The other side of the same rule: agreeing ids pair regardless of the
    clock, which is what the id key gave a `permission.request` before it had
    to become a class key.

    The SDK row deliberately does NOT use its real `permreq-` span_id here —
    that prefix marks a placeholder, so `_drop_superseded_placeholders` would
    retire the row before pairing is ever consulted and the test would pass
    without exercising the identity path at all.
    """
    rows = [
        _row('sdk-perm', 'permission.request',
             {'tool_name': 'Bash', 'tool_use_id': 'toolu_A'}, origin='sdk',
             start='2026-08-01T10:00:30', id=2),
        _row('hook-p', 'permission.request',
             {'tool_name': 'Bash', 'tool_use_id': 'toolu_A'}, origin='hook',
             start='2026-08-01T10:00:00', id=1),
    ]
    assert [s['span_id'] for s in merge_spans(rows)] == ['hook-p']


def test_an_unhashable_tool_name_does_not_crash_the_read():
    """Attributes are arbitrary JSON. `tool_name` goes into a bucket key, so a
    dict-valued one raised `unhashable type` and 500'd the whole trace."""
    rows = [
        _row('hook-p', 'permission.request', {'tool_name': {'weird': 1}},
             origin='hook', start='2026-08-01T10:00:00', id=1),
        _row('sdk-p', 'permission.request', {'tool_name': 'Bash'},
             origin='sdk', start='2026-08-01T10:00:00', id=2),
    ]
    assert len(merge_spans(rows)) == 2


def test_lifecycle_markers_hours_apart_are_never_fused():
    """The teardown ceiling is a ceiling. Two runs of one session — or two
    writers whose marker lists are misaligned — must not collapse just because
    the counts happen to match."""
    rows = [
        _row('hook-s1', 'session.start', {'source': 'startup'}, origin='hook',
             start='2026-08-01T10:00:00', id=1),
        _row('sdk-s1', 'session.start', {}, origin='sdk',
             start='2026-08-01T14:00:00', id=2),
        _row('hook-s2', 'session.start', {'source': 'resume'}, origin='hook',
             start='2026-08-01T18:00:00', id=3),
        _row('sdk-s2', 'session.start', {}, origin='sdk',
             start='2026-08-01T22:00:00', id=4),
    ]
    assert len(merge_spans(rows)) == 4


def test_an_end_only_one_writer_saw_keeps_the_bound():
    """Unequal counts mean a writer missed an event, so the bound comes back —
    without it the lone SDK row would pair with an unrelated later end."""
    rows = [
        _row('sdk-end', 'session.end', {'reason': 'idle timeout'},
             origin='sdk', start='2026-08-01T10:00:00', id=1),
        _row('hook-end-1', 'session.end', {'reason': 'other'}, origin='hook',
             start='2026-08-01T12:00:00', id=2),
        _row('hook-end-2', 'session.end', {'reason': 'other'}, origin='hook',
             start='2026-08-01T13:00:00', id=3),
    ]
    assert len(merge_spans(rows)) == 3


# ── heal ─────────────────────────────────────────────────────────────

def _seed_session(conn, tid, started, *, cwd='/w', title='do the thing',
                  calls=()):
    conn.execute("INSERT INTO sessions VALUES (?,?,?,?)",
                 (tid, started, cwd, title))
    for i, call in enumerate(calls):
        conn.execute(
            "INSERT INTO session_spans VALUES (?,?,?,?)",
            (tid, f'{tid}-{i}', call, '{}'))


def _seed_heal(conn, *, children, run_cwd='/w', run_calls=()):
    conn.execute("INSERT INTO agent_runs VALUES (?,NULL,'exited',?)",
                 (SDK, run_cwd))
    _seed_session(conn, SDK, '2026-08-01T10:00:00', cwd=run_cwd,
                  calls=run_calls)
    for kwargs in children:
        _seed_session(conn, **kwargs)


def test_heal_links_the_single_candidate_and_is_idempotent():
    conn = _alias_db()
    _seed_heal(conn, children=[dict(tid=HOOK, started='2026-08-01T10:00:01')])
    assert heal_cli_session_ids(conn) == 1
    assert trace_group(conn, SDK) == [HOOK, SDK]
    # Re-runnable: rows keep arriving, so this runs at every startup.
    assert heal_cli_session_ids(conn) == 0


def test_shared_tool_use_id_is_proof_and_beats_the_heuristic():
    """A `toolu_*` is minted once and globally unique, so two traces holding
    one recorded the same call. That outranks title/cwd, which would have
    refused this pair."""
    conn = _alias_db()
    _seed_heal(conn, run_calls=['toolu_shared'],
               children=[dict(tid=HOOK, started='2026-08-01T10:00:01',
                              cwd='/somewhere/else', title='different title',
                              calls=['toolu_shared'])])
    assert heal_cli_session_ids(conn) == 1
    assert trace_group(conn, SDK) == [HOOK, SDK]


def test_heal_refuses_an_ambiguous_pair():
    """Mis-linking fuses two unrelated sessions into one trace and nothing in
    the data can undo it later, so two candidates means link neither."""
    conn = _alias_db()
    _seed_heal(conn, children=[dict(tid=HOOK, started='2026-08-01T10:00:01'),
                               dict(tid='other-child',
                                    started='2026-08-01T10:00:02')])
    assert heal_cli_session_ids(conn) == 0
    assert trace_group(conn, SDK) == [SDK]


def test_heal_refuses_an_unrelated_session_started_seconds_later():
    """Same directory, 3s apart — time and cwd alone cannot tell this from the
    real child. The titles differ because the prompts do."""
    conn = _alias_db()
    _seed_heal(conn, children=[dict(tid='humans-own-session',
                                    started='2026-08-01T10:00:03',
                                    title='totally different prompt')])
    assert heal_cli_session_ids(conn) == 0


def test_heal_does_not_pair_across_directories_when_the_run_has_no_cwd():
    """`cwd IS NULL` must not read as 'matches anything' — most real run rows
    have no cwd, so an escape hatch there would guess constantly."""
    conn = _alias_db()
    _seed_heal(conn, run_cwd=None,
               children=[dict(tid=HOOK, started='2026-08-01T10:00:01',
                              cwd='/totally/other/repo')])
    assert heal_cli_session_ids(conn) == 0


def test_heal_ignores_a_child_outside_the_window():
    conn = _alias_db()
    _seed_heal(conn, children=[dict(tid=HOOK, started='2026-08-01T10:05:00')])
    assert heal_cli_session_ids(conn) == 0
