"""Contract-pin tests for lib.trace.trace_service._span_counter_buckets.

The bucket-builder is a 96-line pure function with several intertwined
state machines (counters, is_test, started_at/last_seen min/max,
agent_type 'earliest-non-empty wins', model 'latest wins but turn
skips less-specific', session.end ended_at). The existing
`test_trace_service.py` covers title precedence in depth but leaves
the other branches under-pinned. These tests lock the contract before
the refactor that splits the dispatch into per-name handlers.
"""

from __future__ import annotations

from lib.trace.trace_service import _span_counter_buckets


def _span(trace_id, span_id, name, start='2026-05-15T10:00:00',
          end=None, attrs=None):
    """Build one (span, attrs) tuple in the shape ingest_session_spans hands in."""
    return (
        {'trace_id': trace_id, 'span_id': span_id, 'parent_id': None,
         'name': name, 'kind': 'internal',
         'start_time': start, 'end_time': end or start,
         'duration_ms': 0, 'status_code': 'UNSET', 'status_message': None},
        attrs or {},
    )


# ── Counter increments ──────────────────────────────────────────

def test_skill_read_invoke_launch_increment_skill_reads_counter():
    # Three disjoint read signals: a content.md Read, a slash-command
    # expansion, and an assistant `Skill` tool launch.
    spans = [_span('t', 'a', 'skill.read'),
             _span('t', 'b', 'skill.invoke'),
             _span('t', 'c', 'skill.launch')]
    bucket = _span_counter_buckets(spans, set())['t']
    assert bucket['skill_reads'] == 3
    assert bucket['span_count'] == 3


def test_edit_tool_spans_increment_file_edits():
    # Edits are emitted as raw `tool.*` spans now (file.edit/plan.edit are
    # dead). Each edit-tool span bumps file_edits AND tool_calls.
    spans = [_span('t', 'a', 'tool.Edit'),
             _span('t', 'b', 'tool.Write'),
             _span('t', 'c', 'tool.apply_patch')]
    bucket = _span_counter_buckets(spans, set())['t']
    assert bucket['file_edits'] == 3
    assert bucket['tool_calls'] == 3


def test_non_edit_tool_spans_do_not_increment_file_edits():
    spans = [_span('t', 'a', 'tool.Bash'),
             _span('t', 'b', 'tool.Read'),
             _span('t', 'c', 'tool.TodoWrite')]
    bucket = _span_counter_buckets(spans, set())['t']
    assert bucket['file_edits'] == 0
    assert bucket['tool_calls'] == 3


def test_rule_check_increments_rule_checks():
    bucket = _span_counter_buckets([_span('t', 'a', 'rule.check')], set())['t']
    assert bucket['rule_checks'] == 1


def test_plan_enter_no_longer_derives_plan_enters():
    # `plan.enter` spans aren't emitted anymore and the counter is no
    # longer span-derived (the Sessions list computes plans live from
    # plan_sessions). A stray plan.enter span only bumps span_count.
    bucket = _span_counter_buckets([_span('t', 'a', 'plan.enter')], set())['t']
    assert bucket['plan_enters'] == 0
    assert bucket['span_count'] == 1


def test_prompt_increments_prompts():
    spans = [_span('t', 'a', 'prompt', attrs={'text': 'hi'}),
             _span('t', 'b', 'prompt', attrs={'text': 'again'})]
    bucket = _span_counter_buckets(spans, set())['t']
    assert bucket['prompts'] == 2


def test_tool_prefix_spans_increment_tool_calls():
    spans = [_span('t', 'a', 'tool.Bash'),
             _span('t', 'b', 'tool.Edit'),
             _span('t', 'c', 'pre_tool.Bash')]
    bucket = _span_counter_buckets(spans, set())['t']
    assert bucket['tool_calls'] == 3


def test_unknown_name_only_bumps_span_count():
    bucket = _span_counter_buckets([_span('t', 'a', 'something.else')], set())['t']
    assert bucket['span_count'] == 1
    assert bucket['skill_reads'] == 0
    assert bucket['file_edits'] == 0
    assert bucket['tool_calls'] == 0


# ── Duplicate (trace_id, span_id) pairs are skipped ─────────────

def test_duplicate_span_skipped_no_increment():
    spans = [_span('t', 'a', 'tool.Bash')]
    duplicates = {('t', 'a')}
    out = _span_counter_buckets(spans, duplicates)
    assert out == {}  # nothing inserted, no bucket created


def test_duplicate_span_does_not_affect_other_traces():
    spans = [
        _span('t1', 'a', 'tool.Bash'),
        _span('t2', 'a', 'tool.Bash'),  # same span_id, different trace
    ]
    duplicates = {('t1', 'a')}
    out = _span_counter_buckets(spans, duplicates)
    assert 't1' not in out
    assert out['t2']['tool_calls'] == 1


def test_span_without_trace_id_skipped():
    span = ({'trace_id': None, 'span_id': 'a', 'name': 'tool.Bash',
             'start_time': '2026-05-15T10:00:00'}, {})
    out = _span_counter_buckets([span], set())
    assert out == {}


# ── is_test / test_name ────────────────────────────────────────

def test_is_test_attr_flips_bucket_flag():
    spans = [_span('t', 'a', 'tool.Bash', attrs={'is_test': True})]
    bucket = _span_counter_buckets(spans, set())['t']
    assert bucket['is_test'] == 1


def test_test_name_first_non_empty_wins():
    spans = [
        _span('t', 'a', 'tool.Bash', attrs={'test_name': 'first'}),
        _span('t', 'b', 'tool.Edit', attrs={'test_name': 'second'}),
    ]
    bucket = _span_counter_buckets(spans, set())['t']
    assert bucket['test_name'] == 'first'


def test_test_name_skipped_when_falsy():
    spans = [
        _span('t', 'a', 'tool.Bash', attrs={'test_name': ''}),
        _span('t', 'b', 'tool.Edit', attrs={'test_name': 'real-name'}),
    ]
    bucket = _span_counter_buckets(spans, set())['t']
    assert bucket['test_name'] == 'real-name'


# ── started_at / last_seen min-max ──────────────────────────────

def test_started_at_takes_earliest_start():
    spans = [
        _span('t', 'a', 'tool.Bash', start='2026-05-15T10:05:00'),
        _span('t', 'b', 'tool.Edit', start='2026-05-15T10:00:00'),
    ]
    bucket = _span_counter_buckets(spans, set())['t']
    assert bucket['started_at'] == '2026-05-15T10:00:00'


def test_last_seen_takes_latest_end():
    spans = [
        _span('t', 'a', 'tool.Bash',
              start='2026-05-15T10:00:00', end='2026-05-15T10:01:00'),
        _span('t', 'b', 'tool.Edit',
              start='2026-05-15T10:05:00', end='2026-05-15T10:06:00'),
    ]
    bucket = _span_counter_buckets(spans, set())['t']
    assert bucket['last_seen'] == '2026-05-15T10:06:00'


def test_last_seen_falls_back_to_start_when_no_end():
    span = ({'trace_id': 't', 'span_id': 'a', 'name': 'tool.Bash',
             'start_time': '2026-05-15T10:00:00',
             'end_time': None}, {})
    bucket = _span_counter_buckets([span], set())['t']
    assert bucket['last_seen'] == '2026-05-15T10:00:00'


# ── session.start: agent_type earliest-non-empty wins ───────────

def test_session_start_agent_type_first_in_time_wins():
    spans = [
        _span('t', 'a', 'session.start', start='2026-05-15T10:05:00',
              attrs={'agent_type': 'late'}),
        _span('t', 'b', 'session.start', start='2026-05-15T10:00:00',
              attrs={'agent_type': 'early'}),
    ]
    bucket = _span_counter_buckets(spans, set())['t']
    assert bucket['agent_type'] == 'early'


def test_session_start_agent_type_strips_whitespace():
    spans = [_span('t', 'a', 'session.start',
                   attrs={'agent_type': '  claude  '})]
    bucket = _span_counter_buckets([s for s in spans], set())['t']
    assert bucket['agent_type'] == 'claude'


def test_session_start_agent_type_skipped_when_blank():
    spans = [_span('t', 'a', 'session.start', attrs={'agent_type': '   '})]
    bucket = _span_counter_buckets(spans, set())['t']
    assert bucket['agent_type'] is None


# ── session.start + turn: model LATEST wins, turn skips less-specific ──

def test_session_start_model_latest_in_time_wins():
    spans = [
        _span('t', 'a', 'session.start', start='2026-05-15T10:00:00',
              attrs={'model': 'old-model'}),
        _span('t', 'b', 'session.start', start='2026-05-15T10:05:00',
              attrs={'model': 'new-model'}),
    ]
    bucket = _span_counter_buckets(spans, set())['t']
    assert bucket['model'] == 'new-model'


def test_turn_model_does_not_downgrade_variant_to_bare():
    # session.start carries the variant-bracketed form; the turn span
    # later carries only the bare base. _is_less_specific_model guards
    # against the downgrade.
    spans = [
        _span('t', 'a', 'session.start', start='2026-05-15T10:00:00',
              attrs={'model': 'claude-opus-4-7[1m]'}),
        _span('t', 'b', 'turn', start='2026-05-15T10:05:00',
              attrs={'model': 'claude-opus-4-7'}),
    ]
    bucket = _span_counter_buckets(spans, set())['t']
    assert bucket['model'] == 'claude-opus-4-7[1m]'


def test_turn_model_upgrades_when_strictly_different():
    spans = [
        _span('t', 'a', 'session.start', start='2026-05-15T10:00:00',
              attrs={'model': 'claude-opus-4-7'}),
        _span('t', 'b', 'turn', start='2026-05-15T10:05:00',
              attrs={'model': 'claude-sonnet-4-7'}),
    ]
    bucket = _span_counter_buckets(spans, set())['t']
    assert bucket['model'] == 'claude-sonnet-4-7'


def test_assistant_response_sets_model_when_no_turn_or_start_model():
    # Transcript-replayed llm-stage sessions carry the model only on
    # assistant_response spans — their session.start has no model and they
    # emit no live `turn` span. Without this the session model stays NULL.
    spans = [
        _span('t', 'a', 'session.start', start='2026-05-15T10:00:00',
              attrs={'llm_surface': 'topic-proposal-review'}),
        _span('t', 'b', 'assistant_response', start='2026-05-15T10:05:00',
              attrs={'model': 'claude-sonnet-5'}),
    ]
    bucket = _span_counter_buckets(spans, set())['t']
    assert bucket['model'] == 'claude-sonnet-5'


def test_assistant_response_bare_base_does_not_downgrade_variant():
    # A variant-bracketed id from session.start must survive a later
    # bare-base assistant_response span, same guard as `turn`.
    spans = [
        _span('t', 'a', 'session.start', start='2026-05-15T10:00:00',
              attrs={'model': 'claude-opus-4-8[1m]'}),
        _span('t', 'b', 'assistant_response', start='2026-05-15T10:05:00',
              attrs={'model': 'claude-opus-4-8'}),
    ]
    bucket = _span_counter_buckets(spans, set())['t']
    assert bucket['model'] == 'claude-opus-4-8[1m]'


def test_subagent_assistant_response_does_not_set_parent_model():
    # Kimi subagent turns are emitted as assistant_response spans under the
    # PARENT trace_id, tagged with agent_id. A subagent's model must never
    # overwrite the parent session's model.
    spans = [
        _span('t', 'a', 'session.start', start='2026-05-15T10:00:00',
              attrs={'model': 'kimi-main'}),
        _span('t', 'b', 'assistant_response', start='2026-05-15T10:05:00',
              attrs={'model': 'kimi-subagent', 'agent_id': 'sa-1'}),
    ]
    bucket = _span_counter_buckets(spans, set())['t']
    assert bucket['model'] == 'kimi-main'


# ── session.end: ended_at latest, ended_reason follows latest ────

def test_session_end_records_latest_ended_at_and_reason():
    spans = [
        _span('t', 'a', 'session.end', start='2026-05-15T10:00:00',
              attrs={'reason': 'stop'}),
        _span('t', 'b', 'session.end', start='2026-05-15T10:05:00',
              attrs={'reason': 'compact'}),
    ]
    bucket = _span_counter_buckets(spans, set())['t']
    assert bucket['ended_at'] == '2026-05-15T10:05:00'
    assert bucket['ended_reason'] == 'compact'


def test_session_end_non_string_reason_not_recorded():
    spans = [
        _span('t', 'a', 'session.end', start='2026-05-15T10:00:00',
              attrs={'reason': 42}),
    ]
    bucket = _span_counter_buckets(spans, set())['t']
    assert bucket['ended_at'] == '2026-05-15T10:00:00'
    assert bucket['ended_reason'] is None


# ── last_start_at for session.start ─────────────────────────────

def test_session_start_last_start_at_takes_latest():
    spans = [
        _span('t', 'a', 'session.start', start='2026-05-15T10:00:00'),
        _span('t', 'b', 'session.start', start='2026-05-15T10:05:00'),
    ]
    bucket = _span_counter_buckets(spans, set())['t']
    assert bucket['last_start_at'] == '2026-05-15T10:05:00'


# ── Multi-trace isolation ───────────────────────────────────────

def test_multiple_traces_get_independent_buckets():
    spans = [
        _span('t1', 'a', 'tool.Bash'),
        _span('t2', 'a', 'tool.Edit'),
        _span('t2', 'b', 'skill.read'),
    ]
    out = _span_counter_buckets(spans, set())
    assert out['t1']['tool_calls'] == 1 and out['t1']['skill_reads'] == 0
    assert out['t2']['tool_calls'] == 1 and out['t2']['skill_reads'] == 1
