"""Tests for lib.trace.trace_context.

Covers the two behavioral guarantees that were broken in the previous
implementation:

1. Concurrent `start_span` calls for the same session must all land on the
   stack (the previous read-then-write-without-holding-the-lock cycle lost
   spans under contention).
2. A corrupt trace-context file must be surfaced to the ingest-error log
   instead of silently resetting the stack to empty.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

import pytest

from lib.trace import trace_context


@pytest.fixture
def isolated_trace_dir(tmp_path, monkeypatch):
    """Redirect trace_context writes into a temp directory."""
    monkeypatch.setattr(trace_context, 'TRACE_DIR', str(tmp_path))
    monkeypatch.setattr(
        trace_context, '_INGEST_ERROR_LOG', str(tmp_path / 'ingest-errors.jsonl')
    )
    return tmp_path


def test_start_span_is_atomic_under_concurrency(isolated_trace_dir):
    """Spawn N threads that each push a distinct span. Every push must survive."""
    session_id = 'concurrency-1'
    n_threads = 20

    errors: list[BaseException] = []

    def push(i: int) -> None:
        try:
            trace_context.start_span(session_id, f'span-{i}', {'i': i})
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=push, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"worker threads raised: {errors}"

    with open(Path(isolated_trace_dir) / f'{session_id}.json') as f:
        ctx = json.load(f)

    names = sorted(s['name'] for s in ctx['stack'])
    assert names == sorted(f'span-{i}' for i in range(n_threads)), (
        f"lost spans under concurrent pushes: {names}"
    )


def test_parent_chain_preserved_under_concurrency(isolated_trace_dir):
    """Each new span's parent_id must be a real span_id that already exists."""
    session_id = 'concurrency-2'

    def push(i: int) -> None:
        trace_context.start_span(session_id, f'span-{i}')

    threads = [threading.Thread(target=push, args=(i,)) for i in range(15)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    with open(Path(isolated_trace_dir) / f'{session_id}.json') as f:
        ctx = json.load(f)

    seen_ids: set[str] = set()
    for span in ctx['stack']:
        parent_id = span.get('parent_id')
        if parent_id is not None:
            assert parent_id in seen_ids, (
                f"span {span['name']} has parent_id {parent_id} that is not "
                f"any preceding span on the stack"
            )
        seen_ids.add(span['span_id'])


def test_corrupt_file_is_logged(isolated_trace_dir):
    """If the JSON file is mangled, we log to ingest-errors and do NOT silently
    reset — but the stack does start empty for the new span so the session
    isn't fatally broken."""
    session_id = 'corrupt-1'
    # Pre-create a garbage file.
    data_path = Path(isolated_trace_dir) / f'{session_id}.json'
    data_path.write_text('{this is not valid json')

    trace_context.start_span(session_id, 'first-after-corruption')

    err_log = Path(isolated_trace_dir) / 'ingest-errors.jsonl'
    assert err_log.exists(), "ingest-errors.jsonl not written on corruption"
    lines = [json.loads(l) for l in err_log.read_text().splitlines() if l.strip()]
    assert any(
        e.get('endpoint') == 'trace_context._read'
        and e.get('session_id') == session_id
        and e.get('error_type') in ('JSONDecodeError', 'ValueError')
        for e in lines
    ), f"no corruption entry logged; entries: {lines}"


def test_end_span_matches_by_name(isolated_trace_dir):
    session_id = 'end-by-name'
    trace_context.start_span(session_id, 'outer')
    trace_context.start_span(session_id, 'middle')
    trace_context.start_span(session_id, 'inner')

    ended = trace_context.end_span(session_id, 'middle')
    assert ended is not None
    assert ended['name'] == 'middle'

    # Both 'middle' AND 'inner' should have been popped (implicit close).
    remaining = trace_context.current_span(session_id)
    assert remaining is not None and remaining['name'] == 'outer'


def test_pop_all_preserves_persistent(isolated_trace_dir):
    session_id = 'persistent'
    trace_context.start_span(session_id, 'conversation', persistent=True)
    trace_context.start_span(session_id, 'prompt')
    trace_context.start_span(session_id, 'tool.Read')

    completed = trace_context.pop_all(session_id, preserve_persistent=True)
    names = sorted(s['name'] for s in completed)
    assert names == ['prompt', 'tool.Read']

    remaining = trace_context.current_span(session_id)
    assert remaining is not None and remaining['name'] == 'conversation'


# ── start_span: return value and parent wiring ────────────────────────

def test_start_span_auto_links_parent_from_top_of_stack(isolated_trace_dir):
    """When no explicit parent_id is supplied, start_span wires the new
    span under the current top-of-stack. Without this auto-linking,
    every span would be a trace root and the parent/child tree would
    be flat — the whole point of the stack is lost."""
    session = 'auto-parent'
    outer = trace_context.start_span(session, 'outer')
    inner = trace_context.start_span(session, 'inner')
    assert outer['parent_id'] is None  # root span has no parent
    assert inner['parent_id'] == outer['span_id']


def test_start_span_respects_explicit_parent_id(isolated_trace_dir):
    """Explicit parent_id wins over auto-linking. Handlers that know
    their causal parent (e.g. subagent_lifecycle) use this to avoid
    getting threaded under the wrong caller."""
    session = 'explicit-parent'
    trace_context.start_span(session, 'outer')
    child = trace_context.start_span(session, 'child',
                                     parent_id='deadbeefcafebabe')
    assert child['parent_id'] == 'deadbeefcafebabe'


def test_start_span_stamps_attributes_and_timestamps(isolated_trace_dir):
    """Span shape contract: required fields on every push. A missing
    start_time would make the later duration_ms calc crash in end_span."""
    span = trace_context.start_span('t', 'custom', {'k': 'v'})
    assert set(span.keys()) >= {'span_id', 'parent_id', 'name',
                                'start_time', 'attributes', 'persistent'}
    assert span['attributes'] == {'k': 'v'}
    assert span['name'] == 'custom'
    assert span['persistent'] is False
    # Timestamp is ISO-parseable.
    from datetime import datetime
    datetime.fromisoformat(span['start_time'])


# ── current_span edge cases ───────────────────────────────────────────

def test_current_span_returns_none_on_empty_session(isolated_trace_dir):
    """Querying before any push returns None (not raising, not empty
    dict). Downstream code does `if current: attrs = current[...]`."""
    assert trace_context.current_span('never-started') is None


def test_current_span_returns_top_of_stack(isolated_trace_dir):
    session = 'top'
    trace_context.start_span(session, 'a')
    trace_context.start_span(session, 'b')
    top = trace_context.current_span(session)
    assert top is not None and top['name'] == 'b'


# ── end_span behaviors ────────────────────────────────────────────────

def test_end_span_pops_top_without_name(isolated_trace_dir):
    """end_span with name=None pops the current top. The stack then
    exposes the previous span as the new current."""
    session = 'pop-top'
    trace_context.start_span(session, 'outer')
    trace_context.start_span(session, 'inner')
    ended = trace_context.end_span(session)
    assert ended and ended['name'] == 'inner'
    assert trace_context.current_span(session)['name'] == 'outer'


def test_end_span_computes_duration_ms(isolated_trace_dir):
    """Duration is end_time - start_time in ms; can be 0 for a tight
    start/end loop but must never be None."""
    session = 'duration'
    trace_context.start_span(session, 'quick')
    ended = trace_context.end_span(session, 'quick')
    assert ended is not None
    assert 'duration_ms' in ended
    assert isinstance(ended['duration_ms'], int)
    assert ended['duration_ms'] >= 0


def test_end_span_by_name_returns_none_when_not_found(isolated_trace_dir):
    """Asking to end a span that was never started (e.g. a handler's
    end is wired for the wrong event) must return None — not pop the
    top arbitrarily."""
    session = 'not-found'
    trace_context.start_span(session, 'only-one')
    result = trace_context.end_span(session, 'missing')
    assert result is None
    # The one real span is still on the stack.
    assert trace_context.current_span(session)['name'] == 'only-one'


def test_end_span_on_empty_stack_returns_none(isolated_trace_dir):
    """No spans yet → end_span is a no-op returning None, not a crash."""
    assert trace_context.end_span('never-pushed') is None
    assert trace_context.end_span('never-pushed', 'anything') is None


# ── pop_all default (no persistent preservation) ──────────────────────

def test_pop_all_default_clears_the_entire_stack(isolated_trace_dir):
    """Without preserve_persistent, pop_all clears everything —
    including persistent spans. Used between conversations when even
    the root `conversation` span should be retired."""
    session = 'clear-all'
    trace_context.start_span(session, 'conversation', persistent=True)
    trace_context.start_span(session, 'prompt')
    completed = trace_context.pop_all(session)
    assert {s['name'] for s in completed} == {'conversation', 'prompt'}
    assert trace_context.current_span(session) is None


def test_pop_all_preserve_with_no_persistent_span_clears_everything(isolated_trace_dir):
    """preserve_persistent=True but no span is marked persistent →
    behaves like default: clear all. Documented fallback from the
    source (line 200 of trace_context.py)."""
    session = 'preserve-but-no-persistent'
    trace_context.start_span(session, 'a')
    trace_context.start_span(session, 'b')
    completed = trace_context.pop_all(session, preserve_persistent=True)
    assert len(completed) == 2
    assert trace_context.current_span(session) is None


def test_pop_all_on_empty_stack_returns_empty_list(isolated_trace_dir):
    """Popping a never-started session must return [] — not None,
    not raise. A hook firing on SubagentStop before Start should not
    crash."""
    assert trace_context.pop_all('empty-session') == []


def test_pop_all_stamps_duration_on_every_returned_span(isolated_trace_dir):
    """All popped spans get end_time + duration_ms set — the ingest
    endpoint downstream requires duration_ms to be present."""
    session = 'bulk-duration'
    trace_context.start_span(session, 'a')
    trace_context.start_span(session, 'b')
    trace_context.start_span(session, 'c')
    completed = trace_context.pop_all(session)
    for span in completed:
        assert 'end_time' in span
        assert isinstance(span.get('duration_ms'), int)
