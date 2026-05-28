"""Integration test: parse a real active-provider transcript from disk.

Synthetic fixtures in test_transcript_usage.py cover edge cases we design
for; this test catches shape drift in the actual JSONL produced by the
active provider (e.g. new usage fields, null handling in assistant messages the
synthetic lines don't emit). It auto-skips when no local transcripts
exist so CI on a clean box never fails — the real value is catching
regressions in the developer's own sessions before they ship.
"""

from __future__ import annotations

import glob
import os

import pytest

from lib.providers import get_active_provider
from lib.trace.transcript_usage import read_usage
from lib.tokens.model_windows import infer_window


def _find_any_transcript() -> str | None:
    root = str(get_active_provider().transcript_projects_dir())
    if not os.path.isdir(root):
        return None
    matches = glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True)
    # Prefer larger files — they're more likely to include a variety of
    # turn shapes, including ones with cache_read/cache_creation set.
    matches.sort(key=lambda p: os.path.getsize(p), reverse=True)
    for path in matches:
        if os.path.getsize(path) > 0:
            return path
    return None


@pytest.fixture(scope="module")
def real_transcript():
    path = _find_any_transcript()
    if path is None:
        pytest.skip("no provider transcripts on disk")
    return path


def test_real_transcript_parses_without_error(real_transcript):
    usage = read_usage(real_transcript)
    # Any transcript with assistant turns must produce a result — if this
    # fails, the parser regressed on a field name or line shape.
    assert usage is not None, f"parser returned None on {real_transcript}"
    assert len(usage.turns) >= 1


def test_real_transcript_peak_is_reasonable(real_transcript):
    usage = read_usage(real_transcript)
    assert usage is not None

    # input_tokens alone is always part of context_used, so peak must be
    # at least the largest single-turn input.
    max_input = max(t.input_tokens for t in usage.turns)
    assert usage.peak_context_tokens >= max_input

    # peak_context is the sum of three counters on one turn — it must
    # never exceed the session's total sum across turns.
    total = (usage.input_tokens + usage.cache_read_tokens
             + usage.cache_creation_tokens)
    assert usage.peak_context_tokens <= total


def test_real_transcript_peak_within_inferred_window(real_transcript):
    """infer_window must auto-promote to the 1M variant when the
    measured peak exceeds the base window. Claude Code's transcript
    reports `message.model` as the base id (e.g. `claude-opus-4-7`)
    even when the user is on the 1M extended-context variant — this
    test is the canary for that elision.

    If this asserts, either the model family in _WINDOWS is missing a
    [1m] entry or Claude Code reported tokens that shouldn't count
    toward context_used (unlikely but worth flagging)."""
    usage = read_usage(real_transcript)
    assert usage is not None
    window = infer_window(usage.model, usage.peak_context_tokens)
    assert usage.peak_context_tokens <= window, (
        f"peak {usage.peak_context_tokens} > inferred window {window} "
        f"for model {usage.model} ({real_transcript})"
    )


def test_real_transcript_sums_are_non_negative(real_transcript):
    usage = read_usage(real_transcript)
    assert usage is not None
    for t in usage.turns:
        assert t.input_tokens >= 0
        assert t.output_tokens >= 0
        assert t.cache_read_tokens >= 0
        assert t.cache_creation_tokens >= 0


# ── per-turn provenance: hook_manager.handlers.turn_trace relies on ────
#   these to make stable, idempotent span ids for first-class spans.

def test_real_transcript_turns_carry_uuid_and_timestamp(real_transcript):
    """turn_trace derives span_id from turn.uuid[:16]. If real Claude
    Code transcripts stop writing `uuid` at the top level of assistant
    entries, every replay of the handler would produce collisions
    (same span_id) or worse, synthetic ids that drift on reingest."""
    usage = read_usage(real_transcript)
    assert usage is not None
    # At least 90% of turns should have a uuid — synthetic sidechains
    # (summaries, tool use meta entries) sometimes lack them.
    with_uuid = sum(1 for t in usage.turns if t.uuid)
    assert with_uuid >= int(0.9 * len(usage.turns)), (
        f"only {with_uuid}/{len(usage.turns)} turns had uuid in {real_transcript}"
    )
    for t in usage.turns:
        if t.uuid:
            # Standard UUID format 8-4-4-4-12 = 36 chars.
            assert len(t.uuid) >= 32


def test_real_transcript_turn_uuids_are_unique(real_transcript):
    """Idempotency guarantee — span_id = `usage_<uuid[:16]>`, so two
    turns with the same uuid would produce the same span_id and the
    DB would silently drop the second as a duplicate. If this fires,
    the ingest path would under-count."""
    usage = read_usage(real_transcript)
    assert usage is not None
    uuids = [t.uuid for t in usage.turns if t.uuid]
    assert len(uuids) == len(set(uuids)), "duplicate turn uuids in transcript"
    # span_id is uuid[:16]; collisions at that truncation would also
    # under-count. A real collision at 16 hex chars is astronomically
    # unlikely, but assert it anyway so a change to the truncation
    # length can't slip through.
    prefixes = [u[:16] for u in uuids]
    assert len(prefixes) == len(set(prefixes)), "span_id prefix collision"


def test_real_transcript_peak_equals_max_turn_context(real_transcript):
    """Sanity: the aggregator derives peak_context_tokens by taking
    max(context_used) across all turn.usage spans. That logic must
    agree with what TranscriptUsage.peak_context_tokens reports."""
    usage = read_usage(real_transcript)
    assert usage is not None
    assert usage.peak_context_tokens == max(t.context_used for t in usage.turns)


def test_real_transcript_end_to_end_ingest(real_transcript, tmp_path, monkeypatch):
    """Drive a real transcript through the ingest path the way the live
    handler would: one turn.usage span per assistant message. Then
    query the sessions row and assert peak/sums match the transcript.

    This is the canary for the whole pipeline (parser → span shape →
    aggregator → upsert SQL) working together on real data — the unit
    tests elsewhere cover each piece in isolation, but only this one
    would catch a mismatch between them."""
    import sqlite3
    from web import app as app_module
    import lib.orm.engine as db_module

    db_path = tmp_path / 'real.db'
    monkeypatch.setattr(db_module, 'DB_PATH', str(db_path))
    db_module.init_db()

    # Seed the sessions row AFTER boot so ingest_turn_usage's UPDATE
    # sessions finds it.
    app = app_module.create_app()
    app.config['TESTING'] = True
    client = app.test_client()

    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        INSERT INTO sessions (trace_id, started_at, last_seen, model)
        VALUES (?, ?, ?, ?)
    """, ('real-end-to-end', '2026-04-24T00:00:00',
          '2026-04-24T00:00:00', 'claude-opus-4-7[1m]'))
    conn.commit()
    conn.close()

    usage = read_usage(real_transcript)
    assert usage is not None
    trace_id = 'real-end-to-end'

    def _batch():
        return [
            {
                'trace_id': trace_id,
                'turn_uuid': t.uuid,
                'turn_index': idx,
                'timestamp': t.timestamp,
                'model': t.model or usage.model,
                'input_tokens': t.input_tokens,
                'output_tokens': t.output_tokens,
                'cache_read_tokens': t.cache_read_tokens,
                'cache_creation_tokens': t.cache_creation_tokens,
                'context_used_tokens': t.context_used,
            }
            for idx, t in enumerate(usage.turns)
            if t.uuid and t.timestamp
        ]

    r = client.post('/api/turn-usage', json=_batch())
    assert r.status_code == 200, r.get_data(as_text=True)
    # Replay once to confirm idempotency — same uuids must not double-count.
    r2 = client.post('/api/turn-usage', json=_batch())
    assert r2.status_code == 200

    # Query the sessions row directly — bypasses the session detail API
    # so we're testing the aggregator independently of the projection.
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute("""
            SELECT input_tokens, output_tokens, cache_read_tokens,
                   cache_creation_tokens, peak_context_tokens,
                   context_window_tokens, model
            FROM sessions WHERE trace_id = ?
        """, (trace_id,)).fetchone()
        # Also confirm turn_usage table holds every deduped turn.
        tu_count = conn.execute(
            "SELECT COUNT(*) FROM turn_usage WHERE trace_id = ?",
            (trace_id,),
        ).fetchone()[0]
    finally:
        conn.close()
    assert row is not None
    (ins, outs, cread, ccreate, peak, window, model) = row
    usable = [t for t in usage.turns if t.uuid and t.timestamp]
    assert tu_count == len(usable)
    assert ins == sum(t.input_tokens for t in usable)
    assert outs == sum(t.output_tokens for t in usable)
    assert cread == sum(t.cache_read_tokens for t in usable)
    assert ccreate == sum(t.cache_creation_tokens for t in usable)
    assert peak == max(t.context_used for t in usable)
    assert window >= peak  # infer_window promotes to 1M if peak overflows
    assert isinstance(model, str) and model
