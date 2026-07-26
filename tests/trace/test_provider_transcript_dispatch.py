"""Per-session provider dispatch for transcript discovery.

Every backfill / self-heal path used to hardcode Claude's
`~/.claude/projects/*/<trace_id>.jsonl` layout, so a Kimi session got no live
rescan, no repair button and no prompt-expansion read. These pin that the
resolver follows `sessions.agent_type`, and — just as important — that a
Claude session (including the untagged and subagent-role-tagged majority of
the store) resolves exactly as it did before.
"""

from __future__ import annotations

import pytest

import lib.trace.live_rescan as lr
import lib.trace.repair as repair
from lib.providers.base import provider_for_trace


CLAUDE_TRACE = 'trace-dispatch-claude'
KIMI_TRACE = 'session_dispatch-kimi'


def _record_session(trace_id: str, agent_type: str | None) -> None:
    from lib.orm import SessionLocal
    from lib.orm.models.trace import Session as SessionModel

    with SessionLocal() as db:
        db.add(SessionModel(
            trace_id=trace_id, agent_type=agent_type,
            started_at='2026-07-26T00:00:00Z', last_seen='2026-07-26T00:00:00Z',
        ))
        db.commit()


@pytest.fixture
def transcript_roots(tmp_path, monkeypatch):
    """Tmp stand-ins for `~/.claude/projects` and `~/.kimi-code/sessions`,
    each holding one session in that provider's own on-disk layout."""
    from lib.settings import settings

    claude_root = tmp_path / 'claude-projects'
    (claude_root / '-Users-x-repo').mkdir(parents=True)
    claude_file = claude_root / '-Users-x-repo' / f'{CLAUDE_TRACE}.jsonl'
    claude_file.write_text('{}\n')

    kimi_root = tmp_path / 'kimi-sessions'
    wire_dir = kimi_root / 'wd_abc' / KIMI_TRACE / 'agents' / 'main'
    wire_dir.mkdir(parents=True)
    wire = wire_dir / 'wire.jsonl'
    wire.write_text('{}\n')

    monkeypatch.setattr(settings, 'providers', {
        'claude': {'transcript_projects_dir': str(claude_root)},
        'kimi': {'transcript_projects_dir': str(kimi_root)},
    }, raising=False)
    lr._provider_cache.clear()
    yield {'claude': str(claude_file), 'kimi': str(wire)}
    lr._provider_cache.clear()


def test_kimi_trace_resolves_wire_jsonl(transcript_roots):
    _record_session(KIMI_TRACE, 'kimi')

    assert provider_for_trace(KIMI_TRACE).provider_id == 'kimi'
    assert lr._find_main_transcript(KIMI_TRACE) == transcript_roots['kimi']
    assert repair._find_transcript(KIMI_TRACE) == transcript_roots['kimi']


def test_claude_trace_resolution_unchanged(transcript_roots):
    _record_session(CLAUDE_TRACE, 'claude')

    assert lr._find_main_transcript(CLAUDE_TRACE) == transcript_roots['claude']
    assert repair._find_transcript(CLAUDE_TRACE) == transcript_roots['claude']


@pytest.mark.parametrize('agent_type', [None, '', 'explorer', 'workflow-subagent'])
def test_non_provider_agent_types_stay_on_claude(transcript_roots, agent_type):
    """`agent_type` is free-form vendor text; the store is full of NULLs and
    Claude subagent role names, all of which really are Claude transcripts."""
    _record_session(CLAUDE_TRACE, agent_type)

    assert provider_for_trace(CLAUDE_TRACE).provider_id == 'claude'
    assert lr._find_main_transcript(CLAUDE_TRACE) == transcript_roots['claude']


def test_missing_session_row_falls_back_to_claude(transcript_roots):
    assert provider_for_trace(CLAUDE_TRACE).provider_id == 'claude'
    assert lr._find_main_transcript(CLAUDE_TRACE) == transcript_roots['claude']


def test_claude_default_root_is_still_dot_claude_projects(tmp_path, monkeypatch):
    """With no path override configured — the shipped default — the Claude
    resolver must glob the same `~/.claude/projects/*/<trace_id>.jsonl` the
    hardcoded version did."""
    from lib.settings import settings

    monkeypatch.setattr(settings, 'providers', {}, raising=False)
    monkeypatch.setattr('pathlib.Path.home', lambda: tmp_path)
    lr._provider_cache.clear()
    target = tmp_path / '.claude' / 'projects' / '-Users-x-repo'
    target.mkdir(parents=True)
    (target / f'{CLAUDE_TRACE}.jsonl').write_text('{}\n')
    _record_session(CLAUDE_TRACE, 'claude')

    assert lr._find_main_transcript(CLAUDE_TRACE) == str(
        target / f'{CLAUDE_TRACE}.jsonl')


def test_kimi_queued_prompt_replay_reads_the_kimi_path(transcript_roots):
    """The queued-prompt banner is derived from whatever
    `_find_main_transcript` returns; for Kimi it used to be None (so always an
    empty banner)."""
    from lib.trace.queued_prompts import current_queued_prompts

    _record_session(KIMI_TRACE, 'kimi')
    with open(transcript_roots['kimi'], 'w') as f:
        f.write('{"type":"queue-operation","operation":"enqueue",'
                '"content":"do the thing","timestamp":"2026-07-26T01:00:00Z"}\n')

    assert current_queued_prompts(KIMI_TRACE) == [
        {'content': 'do the thing', 'enqueued_at': '2026-07-26T01:00:00Z'},
    ]


def test_rescan_of_kimi_session_uses_the_provider_parser(transcript_roots, monkeypatch):
    """Claude's resumable scanner / subagent glob / self-heals are all
    Claude-format readers, so a Kimi rescan takes the provider full-parse path
    instead."""
    _record_session(KIMI_TRACE, 'kimi')
    seen: list = []
    monkeypatch.setattr(
        'hook_manager.handlers.turn_trace.entry._ingest_transcript_usage',
        lambda tid, path, model, provider, **k: seen.append(
            (tid, path, provider.provider_id)),
    )

    def _boom(*_a, **_k):
        raise AssertionError('claude resumable scan ran for a kimi session')

    monkeypatch.setattr(
        'hook_manager.handlers.turn_trace.entry.ingest_transcript_usage_resumable',
        _boom,
    )
    lr._last_mtime.clear()
    lr._running.clear()

    lr._do_rescan(KIMI_TRACE)

    assert seen == [(KIMI_TRACE, transcript_roots['kimi'], 'kimi')]


def test_tool_span_backfill_skips_non_claude_transcripts(transcript_roots, monkeypatch):
    _record_session(KIMI_TRACE, 'kimi')
    monkeypatch.setattr(
        repair, '_load_transcript_entries',
        lambda _p: pytest.fail('claude transcript reader ran for a kimi session'),
    )

    out = repair.backfill_transcript_tool_spans(KIMI_TRACE)

    assert out == {'trace_id': KIMI_TRACE, 'spans_backfilled': 0,
                   'transcripts_walked': 0}


def test_repair_payload_carries_the_session_provider(transcript_roots, monkeypatch):
    """The synthetic payload repair feeds to turn_trace must name the
    session's provider — `resolved_provider` dispatches on it, and without the
    tag a Kimi wire.jsonl gets parsed by Claude's reader."""
    _record_session(KIMI_TRACE, 'kimi')
    captured: list = []
    monkeypatch.setattr(
        'hook_manager.handlers.turn_trace.handle',
        lambda payload: captured.append(payload),
    )
    monkeypatch.setattr(repair, '_expected_span_ids_by_uuid', lambda *_a: {})

    repair.repair_session_spans(KIMI_TRACE)

    assert captured, 'turn_trace.handle was never invoked'
    assert captured[0].raw['agent_type'] == 'kimi'
    assert captured[0].resolved_provider.provider_id == 'kimi'


def test_prompt_expansions_stay_inert_for_claude(transcript_roots, monkeypatch):
    """`_attach_prompt_expansions` built its path from a `settings.transcript_dir`
    that does not exist, so it returned early for every provider for its whole
    life. Provider-dispatching it must not silently switch it on for Claude:
    that would add a transcript parse to `/map` and `/spans/<id>/children`,
    which the live view deep-refreshes every few seconds per open session."""
    from lib.trace.trace_service import queries

    _record_session(CLAUDE_TRACE, 'claude')
    parsed: list = []

    class _Usage:
        prompt_expansions = {'abcdefghijklmnop': 'the full /review expansion'}

    def _fake_parse(path, *, max_text_bytes=None):
        parsed.append(path)
        return _Usage()

    monkeypatch.setattr(
        'lib.providers.claude.ClaudeProvider.parse_transcript',
        staticmethod(_fake_parse),
    )
    spans = [{'span_id': 'prompt-abcdefghijklm', 'name': 'prompt',
              'attributes': {'text': '/review'}}]

    queries._attach_prompt_expansions(CLAUDE_TRACE, spans)

    assert parsed == []
    assert 'expanded_text' not in spans[0]['attributes']
