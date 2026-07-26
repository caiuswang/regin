"""Serve-time prompt-expansion reads must not re-parse transcripts per poll.

`_attach_prompt_expansions` runs on `/api/sessions/<id>/map` *and* on
`/spans/<id>/children`, which the live view deep-refreshes every few seconds
per open session, so an uncapped parse there costs ~1s on a 20 MB transcript.

The pass is gated to `_EXPANSION_PROVIDER_IDS`: it was inert for its whole
life (it read a `settings.transcript_dir` that never existed), so enabling it
for Claude would have added that cost to the busiest endpoint in the product
for a feature nobody had. These pin that Claude stays structurally inert,
and that for the providers that *do* run it the read is memoized, capped, and
refreshed by a genuinely new prompt.
"""

from __future__ import annotations

import pytest

from lib.trace.trace_service import queries


CLAUDE_TRACE = 'trace-expansion-hotpath-claude'
KIMI_TRACE = 'session_expansion-hotpath-kimi'
UUID_ONE = 'abcdefghijklmnop'
UUID_TWO = 'zyxwvutsrqponml'


class _Usage:
    def __init__(self, expansions):
        self.prompt_expansions = expansions


def _record_session(trace_id: str, agent_type: str | None) -> None:
    from lib.orm import SessionLocal
    from lib.orm.models.trace import Session as SessionModel

    with SessionLocal() as db:
        db.add(SessionModel(
            trace_id=trace_id, agent_type=agent_type,
            started_at='2026-07-26T00:00:00Z', last_seen='2026-07-26T00:00:00Z',
        ))
        db.commit()


def _prompt_span(uuid: str) -> dict:
    return {'span_id': f'prompt-{uuid[:13]}', 'name': 'prompt',
            'attributes': {'text': '/review'}}


@pytest.fixture(autouse=True)
def _clear_cache():
    queries._reset_prompt_expansion_cache()
    yield
    queries._reset_prompt_expansion_cache()


@pytest.fixture
def transcripts(tmp_path, monkeypatch):
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
    return {'claude': claude_file, 'kimi': wire}


@pytest.fixture
def kimi_reads(monkeypatch):
    """Record every KimiProvider transcript parse; return `/review` for
    UUID_ONE, plus UUID_TWO once the caller flips `reads['second']`."""
    reads: dict = {'calls': [], 'second': False}

    def _parse(path, *, max_text_bytes=None):
        reads['calls'].append((path, max_text_bytes))
        out = {UUID_ONE: 'the full /review expansion'}
        if reads['second']:
            out[UUID_TWO] = 'the full /goal expansion'
        return _Usage(out)

    monkeypatch.setattr('lib.providers.kimi.KimiProvider.parse_transcript',
                        staticmethod(_parse))
    return reads


@pytest.fixture
def claude_reads(monkeypatch):
    """Record every ClaudeProvider transcript parse. The gate should mean this
    stays empty on every path."""
    reads: dict = {'calls': []}

    def _parse(path, *, max_text_bytes=None):
        reads['calls'].append((path, max_text_bytes))
        return _Usage({UUID_ONE: 'the full /review expansion'})

    monkeypatch.setattr('lib.providers.claude.ClaudeProvider.parse_transcript',
                        staticmethod(_parse))
    return reads


def test_claude_never_reads_a_transcript(transcripts, claude_reads):
    """The regression this gate exists for: a Claude projection must not pay
    for a transcript parse, even though a readable transcript is right there
    and the prompt span is a valid expansion target."""
    _record_session(CLAUDE_TRACE, 'claude')
    spans = [_prompt_span(UUID_ONE)]

    queries._attach_prompt_expansions(CLAUDE_TRACE, spans)

    assert claude_reads['calls'] == []
    assert 'expanded_text' not in spans[0]['attributes']


def test_expansion_attaches(transcripts, kimi_reads):
    _record_session(KIMI_TRACE, 'kimi')
    spans = [_prompt_span(UUID_ONE)]

    queries._attach_prompt_expansions(KIMI_TRACE, spans)

    assert spans[0]['attributes']['expanded_text'] == 'the full /review expansion'
    assert [c[0] for c in kimi_reads['calls']] == [str(transcripts['kimi'])]


def test_repeat_polls_reuse_one_transcript_read(transcripts, kimi_reads):
    """The live-tail case: same session, same spans, polled repeatedly."""
    _record_session(KIMI_TRACE, 'kimi')

    for _ in range(5):
        spans = [_prompt_span(UUID_ONE)]
        queries._attach_prompt_expansions(KIMI_TRACE, spans)
        assert spans[0]['attributes']['expanded_text'] == 'the full /review expansion'

    assert len(kimi_reads['calls']) == 1


def test_expansion_read_is_byte_capped(transcripts, kimi_reads):
    """An uncapped read materialises the whole transcript's text on a path
    that only wants the isMeta expansion map."""
    _record_session(KIMI_TRACE, 'kimi')

    queries._attach_prompt_expansions(KIMI_TRACE, [_prompt_span(UUID_ONE)])

    cap = kimi_reads['calls'][0][1]
    assert isinstance(cap, int) and cap > 0


def test_a_new_prompt_span_refreshes_the_read(transcripts, kimi_reads):
    _record_session(KIMI_TRACE, 'kimi')
    queries._attach_prompt_expansions(KIMI_TRACE, [_prompt_span(UUID_ONE)])

    kimi_reads['second'] = True
    transcripts['kimi'].write_text('{}\n{}\n')
    spans = [_prompt_span(UUID_ONE), _prompt_span(UUID_TWO)]
    queries._attach_prompt_expansions(KIMI_TRACE, spans)

    assert len(kimi_reads['calls']) == 2
    assert spans[1]['attributes']['expanded_text'] == 'the full /goal expansion'


def test_spans_without_prompts_never_touch_the_transcript(transcripts, kimi_reads):
    """`/spans/<id>/children` subtrees are mostly tool spans; those must not
    pay for a transcript read at all."""
    _record_session(KIMI_TRACE, 'kimi')
    spans = [{'span_id': 'tool-1', 'name': 'tool.Bash', 'attributes': {}}]

    queries._attach_prompt_expansions(KIMI_TRACE, spans)

    assert kimi_reads['calls'] == []


def test_already_expanded_prompts_never_touch_the_transcript(transcripts, kimi_reads):
    _record_session(KIMI_TRACE, 'kimi')
    span = _prompt_span(UUID_ONE)
    span['attributes']['expanded_text'] = 'already there'

    queries._attach_prompt_expansions(KIMI_TRACE, [span])

    assert kimi_reads['calls'] == []
    assert span['attributes']['expanded_text'] == 'already there'


def test_kimi_session_reads_its_own_wire_transcript(transcripts, monkeypatch):
    _record_session(KIMI_TRACE, 'kimi')
    seen: list = []

    def _parse(path, *, max_text_bytes=None):
        seen.append(path)
        return _Usage({UUID_ONE: 'the kimi expansion'})

    monkeypatch.setattr('lib.providers.kimi.KimiProvider.parse_transcript',
                        staticmethod(_parse))
    monkeypatch.setattr(
        'lib.providers.claude.ClaudeProvider.parse_transcript',
        staticmethod(lambda *_a, **_k: pytest.fail('claude reader ran for kimi')),
    )
    spans = [_prompt_span(UUID_ONE)]

    queries._attach_prompt_expansions(KIMI_TRACE, spans)

    assert seen == [str(transcripts['kimi'])]
    assert spans[0]['attributes']['expanded_text'] == 'the kimi expansion'


def test_a_failed_parse_is_swallowed(transcripts, monkeypatch):
    _record_session(KIMI_TRACE, 'kimi')
    monkeypatch.setattr(
        'lib.providers.kimi.KimiProvider.parse_transcript',
        staticmethod(lambda *_a, **_k: (_ for _ in ()).throw(ValueError('boom'))),
    )
    spans = [_prompt_span(UUID_ONE)]

    queries._attach_prompt_expansions(KIMI_TRACE, spans)

    assert 'expanded_text' not in spans[0]['attributes']


def test_missing_transcript_is_a_no_op(monkeypatch, tmp_path):
    from lib.settings import settings

    _record_session(KIMI_TRACE, 'kimi')
    monkeypatch.setattr(settings, 'providers', {
        'kimi': {'transcript_projects_dir': str(tmp_path / 'empty')},
    }, raising=False)
    spans = [_prompt_span(UUID_ONE)]

    queries._attach_prompt_expansions(KIMI_TRACE, spans)

    assert 'expanded_text' not in spans[0]['attributes']
