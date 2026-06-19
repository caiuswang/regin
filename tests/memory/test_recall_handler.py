"""memory_recall handler: <recalled_experience> injection + guards."""

from __future__ import annotations

import lib.memory as memory
from hook_manager.core import HookPayload
from hook_manager.handlers import memory_recall


def _payload(prompt, cwd="/tmp/anywhere", session_id="s1", **raw_extra):
    raw = {"prompt": prompt, "cwd": cwd, "session_id": session_id, **raw_extra}
    return HookPayload(event="UserPromptSubmit", prompt=prompt, cwd=cwd,
                       session_id=session_id, raw=raw)


def _seed():
    # is_test stays False on purpose: the handler recalls real rows only,
    # and the autouse tmp_memory_db fixture already isolates the DB file.
    memory.remember(
        "Playwright reuses a stale backend on :8321; restart after edits.",
        kind="gotcha", title="Stale backend")


def test_injects_recalled_experience():
    _seed()
    r = memory_recall.handle(_payload(
        "why does playwright hit a stale backend in e2e?"))
    assert r is not None
    assert "<recalled_experience>" in r.additional_context
    assert "Stale backend" in r.additional_context
    assert r.suppress_output is True


def test_no_match_returns_none():
    _seed()
    assert memory_recall.handle(
        _payload("completely unrelated gardening question")) is None


def test_single_token_overlap_is_gated():
    """BM25 would rank this (it shares 'backend'), but the inject path
    requires inject_min_overlap distinct content tokens."""
    _seed()
    assert memory_recall.handle(
        _payload("implement a new backend feature for search")) is None


def test_guards():
    _seed()
    assert memory_recall.handle(_payload("yes")) is None
    assert memory_recall.handle(_payload("/clear")) is None  # bare command
    assert memory_recall.handle(_payload("/review x")) is None  # args too short
    assert memory_recall.handle(_payload(
        "x <task-notification><task-id>1</task-id></task-notification>")) is None
    assert memory_recall.handle(
        _payload("playwright stale backend",
                 agent_type="workflow-subagent")) is None


def test_slash_command_recalls_on_arg_text():
    """A slash command itself is skill machinery, but the argument text the
    user typed after it is a real task — recall runs on the args."""
    _seed()
    r = memory_recall.handle(_payload(
        "/review why does playwright hit a stale backend in e2e?"))
    assert r is not None
    assert "Stale backend" in r.additional_context


def test_disabled_settings_return_none(monkeypatch):
    _seed()
    from lib.settings import settings
    monkeypatch.setattr(settings.agent_memory, "auto_inject", False)
    assert memory_recall.handle(
        _payload("playwright stale backend e2e issue")) is None


def _capture_spans(monkeypatch):
    """Intercept the handler's post_span so tests assert on the span
    without a live ingest endpoint (and without HTTP retry latency)."""
    posted = []

    def _fake_post_span(*, trace_id, name, attributes=None, **_kw):
        posted.append({"trace_id": trace_id, "name": name,
                       "attributes": attributes or {}})
        return True

    from lib import hook_plugin
    monkeypatch.setattr(hook_plugin, "post_span", _fake_post_span)
    return posted


def test_inject_emits_memory_recall_span(monkeypatch):
    _seed()
    posted = _capture_spans(monkeypatch)
    r = memory_recall.handle(_payload(
        "why does playwright hit a stale backend in e2e?"))
    assert r is not None  # still injects
    spans = [s for s in posted if s["name"] == "memory.recall"]
    assert len(spans) == 1
    attrs = spans[0]["attributes"]
    assert attrs["block"] == r.additional_context  # the rendered block
    assert attrs["hit_count"] >= 1
    assert attrs["hits"][0]["title"] == "Stale backend"
    assert spans[0]["trace_id"] == "s1"


def test_recall_span_reflects_fts_mode(monkeypatch):
    """When FTS path is taken (server disabled), span mode is 'fts'."""
    _seed()
    from lib.settings import settings
    monkeypatch.setattr(settings.agent_memory, "inject_dense_via_server", False)
    posted = _capture_spans(monkeypatch)
    r = memory_recall.handle(_payload(
        "why does playwright hit a stale backend in e2e?"))
    assert r is not None
    spans = [s for s in posted if s["name"] == "memory.recall"]
    assert len(spans) == 1
    assert spans[0]["attributes"]["mode"] == "fts"


def test_recall_span_reflects_server_mode(monkeypatch):
    """When server path succeeds, span mode is 'server'."""
    from lib.settings import settings
    monkeypatch.setattr(settings.agent_memory, "inject_dense_via_server", True)
    server_payload = {"hits": [{
        "id": "srv1", "kind": "lesson", "title": "From server",
        "body": "a dense-only hit", "scope": "global",
        "score": 0.9, "score_kind": "rerank"}]}
    _stub_urlopen(monkeypatch,
                  lambda req, timeout=None: _FakeResp(200, server_payload))
    posted = _capture_spans(monkeypatch)
    r = memory_recall.handle(_payload("anything at all goes here please"))
    assert r is not None
    spans = [s for s in posted if s["name"] == "memory.recall"]
    assert len(spans) == 1
    assert spans[0]["attributes"]["mode"] == "server"


def test_no_span_when_trace_recall_disabled(monkeypatch):
    _seed()
    posted = _capture_spans(monkeypatch)
    from lib.settings import settings
    monkeypatch.setattr(settings.agent_memory, "trace_recall", False)
    r = memory_recall.handle(_payload(
        "why does playwright hit a stale backend in e2e?"))
    assert r is not None  # injection still happens
    assert not [s for s in posted if s["name"] == "memory.recall"]


def test_no_span_when_nothing_injected(monkeypatch):
    _seed()
    posted = _capture_spans(monkeypatch)
    assert memory_recall.handle(
        _payload("completely unrelated gardening question")) is None
    assert not posted


# --- #3 same-session dedup + #4 reinforce-on-resurface ----------------------

def _seeded_memory_id():
    import lib.memory as memory
    return memory.get_store().list_memories()[0]["id"]


def _recall_count(mid):
    import lib.memory as memory
    rows = memory.get_store().list_memories()
    return next(r["recall_count"] for r in rows if r["id"] == mid)


def test_dedup_same_session_skips_repeat_and_reinforces():
    """A memory injected once isn't re-rendered for the same session; its
    second match instead reinforces it exactly once (idempotent)."""
    _seed()
    q = "why does playwright hit a stale backend in e2e?"

    first = memory_recall.handle(_payload(q))
    assert first is not None and "Stale backend" in first.additional_context
    mid = _seeded_memory_id()
    assert _recall_count(mid) == 0  # auto-inject never reinforces

    assert memory_recall.handle(_payload(q)) is None  # deduped
    assert _recall_count(mid) == 1                     # re-surface reinforced
    assert memory_recall.handle(_payload(q)) is None
    assert _recall_count(mid) == 1                     # only once, ever


def test_dedup_is_per_session():
    """Dedup is scoped to a session — a different session re-injects."""
    _seed()
    q = "why does playwright hit a stale backend in e2e?"
    assert memory_recall.handle(_payload(q, session_id="A")) is not None
    assert memory_recall.handle(_payload(q, session_id="A")) is None
    # Fresh session id → injected again.
    assert memory_recall.handle(_payload(q, session_id="B")) is not None


def test_dedup_off_reinjects_but_still_reinforces_once(monkeypatch):
    _seed()
    from lib.settings import settings
    monkeypatch.setattr(settings.agent_memory, "inject_dedup_session", False)
    q = "why does playwright hit a stale backend in e2e?"

    assert memory_recall.handle(_payload(q)) is not None
    mid = _seeded_memory_id()
    assert memory_recall.handle(_payload(q)) is not None  # re-injected
    assert _recall_count(mid) == 1                         # still reinforced once
    assert memory_recall.handle(_payload(q)) is not None
    assert _recall_count(mid) == 1


# --- #2 dense recall via the warm server -----------------------------------

class _FakeResp:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload

    def read(self):
        import json
        return json.dumps(self._payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


def _stub_urlopen(monkeypatch, fn):
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", fn)


def test_hits_from_server_gates_low_rerank_scores():
    from lib.settings import settings
    cfg = settings.agent_memory  # recall_min_score default 0.35
    raw = [
        {"id": "a", "kind": "lesson", "title": "T", "body": "B",
         "score": 0.9, "score_kind": "rerank"},
        {"id": "b", "kind": "lesson", "title": "T2", "body": "B2",
         "score": 0.1, "score_kind": "rerank"},   # below gate → dropped
        {"id": "c", "kind": "lesson", "title": "T3", "body": "B3",
         "score": 0.0, "score_kind": "fts"},       # rank-only → kept
    ]
    hits = memory_recall._hits_from_server(raw, cfg)
    assert [h.memory["id"] for h in hits] == ["a", "c"]
    assert hits[0].memory["body"] == "B"
    assert "score" not in hits[0].memory  # score/score_kind stripped out


def test_recall_via_server_returns_none_on_error(monkeypatch):
    from lib.settings import settings
    def _boom(req, timeout=None):
        raise OSError("connection refused")
    _stub_urlopen(monkeypatch, _boom)
    # None signals the caller to fall back to in-process FTS.
    hits, suppress = memory_recall._recall_via_server(
        "a real query here", "global", settings.agent_memory)
    assert hits is None and suppress is False


def test_handle_uses_server_dense_path(monkeypatch):
    """End-to-end: with the local store EMPTY, an injected block can only
    come from the server leg — proving the dense path is wired."""
    from lib.settings import settings
    monkeypatch.setattr(settings.agent_memory, "inject_dense_via_server", True)
    server_payload = {"hits": [{
        "id": "srv1", "kind": "lesson", "title": "From server",
        "body": "a dense-only hit", "scope": "global",
        "score": 0.9, "score_kind": "rerank"}]}
    _stub_urlopen(monkeypatch,
                  lambda req, timeout=None: _FakeResp(200, server_payload))
    r = memory_recall.handle(_payload("anything at all goes here please"))
    assert r is not None
    assert "From server" in r.additional_context


def test_recall_mode_defaults_to_inline():
    from lib.settings import AgentMemoryConfig
    assert AgentMemoryConfig().recall_mode == "inline"


def test_deeper_pull_line_reflects_recall_mode(monkeypatch):
    from lib.settings import settings
    monkeypatch.setattr(settings.agent_memory, "recall_mode", "inline")
    assert "recall` MCP tool" in memory_recall._deeper_pull_line()
    monkeypatch.setattr(settings.agent_memory, "recall_mode", "subagent")
    assert "memory-research" in memory_recall._deeper_pull_line()


def test_build_block_carries_active_mode_line(monkeypatch):
    from lib.settings import settings
    from lib.memory.models import MemoryHit
    hit = MemoryHit(memory={"id": "abc12345", "kind": "lesson",
                            "title": "T", "body": "B"},
                    score=0.5, score_kind="fts")
    monkeypatch.setattr(settings.agent_memory, "recall_mode", "subagent")
    block = memory_recall._build_block([hit], 2000)
    assert "<recalled_experience>" in block
    assert "memory-research" in block


# --- age suffix tests --------------------------------------------------------

def _make_hit(updated_at=None, created_at=None, memory_id="aabbccdd"):
    from lib.memory.models import MemoryHit
    m = {"id": memory_id, "kind": "lesson", "title": "T", "body": "B"}
    if updated_at is not None:
        m["updated_at"] = updated_at
    if created_at is not None:
        m["created_at"] = created_at
    return MemoryHit(memory=m, score=0.5, score_kind="fts")


def test_age_suffix_shows_days():
    from datetime import datetime, timedelta
    old = (datetime.now() - timedelta(days=3)).isoformat()
    line = memory_recall._format_entry(_make_hit(updated_at=old))
    assert "3d old" in line
    assert "(memory aabbccdd" in line


def test_age_suffix_shows_months():
    from datetime import datetime, timedelta
    old = (datetime.now() - timedelta(days=65)).isoformat()
    line = memory_recall._format_entry(_make_hit(updated_at=old))
    assert "mo old" in line


def test_age_suffix_fresh_under_one_hour():
    from datetime import datetime, timedelta
    recent = (datetime.now() - timedelta(minutes=30)).isoformat()
    line = memory_recall._format_entry(_make_hit(updated_at=recent))
    assert "fresh" in line


def test_age_suffix_falls_back_to_created_at():
    from datetime import datetime, timedelta
    old = (datetime.now() - timedelta(hours=5)).isoformat()
    # updated_at absent, created_at present
    line = memory_recall._format_entry(_make_hit(created_at=old))
    assert "5h old" in line


def test_age_suffix_tolerates_missing_timestamp():
    """No crash and no suffix when both stamps are absent."""
    line = memory_recall._format_entry(_make_hit())
    assert "old" not in line
    assert "fresh" not in line
    assert "(memory aabbccdd)" in line


def test_age_suffix_tolerates_bad_timestamp():
    """Unparseable stamp → no suffix, no exception."""
    line = memory_recall._format_entry(_make_hit(updated_at="not-a-date"))
    assert "old" not in line
    assert "(memory aabbccdd)" in line


def test_injected_block_contains_age(monkeypatch):
    """End-to-end: the age suffix appears in the block rendered by handle()."""
    _seed()
    r = memory_recall.handle(_payload(
        "why does playwright hit a stale backend in e2e?"))
    assert r is not None
    # The seeded memory was just created — expect 'fresh' in the footer.
    assert "fresh" in r.additional_context or "old" in r.additional_context


def _server_hit(score, title="t", body="b" * 80, score_kind="rerank"):
    return {"id": "m-" + title, "title": title, "body": body,
            "kind": "lesson", "scope": "global",
            "score": score, "score_kind": score_kind}


def test_gate_rescues_dominant_top_hit():
    """All hits below recall_min_score, but the top dominates the
    runner-up 2x+ at a non-noise score -> the top alone is injected."""
    from lib.settings import settings
    from hook_manager.handlers.memory_recall import _hits_from_server

    hits = _hits_from_server(
        [_server_hit(0.138, "exact-match"), _server_hit(0.021, "noise")],
        settings.agent_memory)
    assert [h.memory["title"] for h in hits] == ["exact-match"]


def test_gate_keeps_silence_on_low_near_ties():
    """Sub-gate near-ties (tangential matches) stay silent — no rescue."""
    from lib.settings import settings
    from hook_manager.handlers.memory_recall import _hits_from_server

    hits = _hits_from_server(
        [_server_hit(0.071, "a"), _server_hit(0.069, "b")],
        settings.agent_memory)
    assert hits == []


def test_gate_no_rescue_below_floor():
    """A dominant but noise-level top hit (under the rescue floor) stays
    silent."""
    from lib.settings import settings
    from hook_manager.handlers.memory_recall import _hits_from_server

    hits = _hits_from_server(
        [_server_hit(0.04, "a"), _server_hit(0.01, "b")],
        settings.agent_memory)
    assert hits == []


def test_gate_normal_path_unchanged():
    """Hits clearing recall_min_score pass through; no rescue involved."""
    from lib.settings import settings
    from hook_manager.handlers.memory_recall import _hits_from_server

    hits = _hits_from_server(
        [_server_hit(0.43, "confident"), _server_hit(0.02, "noise")],
        settings.agent_memory)
    assert [h.memory["title"] for h in hits] == ["confident"]


# ── Topic-router bridge (Step 2: pointer-only <topic_context>) ────────────────

_ROUTED = {"id": "topic-routing", "label": "Topic routing",
           "intent": "How an agent resolves a query to approved context.",
           "refs": [{"path": "lib/topics/route.py", "role": "implementation"}],
           "wiki_pages": [{"content": "FULL WIKI BODY — must not be injected"}]}


def test_build_topic_context_is_pointer_only_and_bounded():
    block = memory_recall._build_topic_context(_ROUTED, 600)
    assert block.startswith("<topic_context>")
    assert "topic-routing" in block
    assert "lib/topics/route.py" in block          # ref pointer present
    assert "FULL WIKI BODY" not in block           # wiki stays opt-in
    assert "regin topics route" in block           # pull-the-wiki hint
    # Char budget is honored (refs/intent trimmed to fit).
    tight = memory_recall._build_topic_context(_ROUTED, 60)
    inner = tight[len("<topic_context>\n"):-len("\n</topic_context>")]
    assert len(inner) <= 60


def test_topic_context_preserves_hint_when_refs_overflow():
    """A topic with far more refs than the budget holds still ends with the
    wiki-pull hint (never tail-clipped), trims the intent on a word boundary,
    and marks the dropped refs with `(+N more)`."""
    routed = {
        "id": "big-topic", "label": "Big topic with many refs",
        "intent": "word " * 100,  # 500 chars, must word-trim not mid-cut
        "refs": [{"path": f"lib/mod_{i}.py", "role": "implementation"}
                 for i in range(40)],
    }
    block = memory_recall._build_topic_context(routed, 600)
    inner = block[len("<topic_context>\n"):-len("\n</topic_context>")]
    assert len(inner) <= 600
    assert inner.rstrip().endswith("--wiki` for the full guide.")  # hint survived
    assert "(+" in inner and "more — see wiki)" in inner  # partial-list marker
    assert "wordword" not in inner                    # no mid-word stub


def test_topic_context_injected_without_memory_match(monkeypatch):
    """A routed topic injects its pointer even when no memory matches —
    the authoritative context is useful on its own."""
    from lib.settings import settings
    monkeypatch.setattr(settings.agent_memory, "topic_route_inject", True)
    monkeypatch.setattr(memory_recall, "_route_topic",
                        lambda q, cwd, cfg: _ROUTED)
    r = memory_recall.handle(_payload("unrelated gardening question entirely"))
    assert r is not None
    assert "<topic_context>" in r.additional_context
    assert "<recalled_experience>" not in r.additional_context


def test_topic_context_prepended_above_memory(monkeypatch):
    _seed()
    from lib.settings import settings
    monkeypatch.setattr(settings.agent_memory, "topic_route_inject", True)
    monkeypatch.setattr(memory_recall, "_route_topic",
                        lambda q, cwd, cfg: _ROUTED)
    r = memory_recall.handle(_payload(
        "why does playwright hit a stale backend in e2e?"))
    assert r is not None
    ctx = r.additional_context
    assert "<topic_context>" in ctx and "<recalled_experience>" in ctx
    assert ctx.index("<topic_context>") < ctx.index("<recalled_experience>")


def test_topic_route_disabled_by_default():
    """The code default keeps topic routing off; with the flag off
    `_route_topic` short-circuits to None so the topic graph is never
    consulted. Built from a fresh config so a user's persisted override of
    the live singleton can't flip this assertion."""
    from lib.settings import AgentMemoryConfig
    cfg = AgentMemoryConfig()
    assert cfg.topic_route_inject is False
    assert memory_recall._route_topic("anything at all", "/tmp", cfg) is None


def test_topic_only_route_emits_span(monkeypatch):
    """A topic-only route (no memory match) still records a memory.recall
    span: hit_count 0, the topic recorded, block == the injected context.
    Regression for the gate that previously emitted only when memories hit."""
    from lib.settings import settings
    monkeypatch.setattr(settings.agent_memory, "topic_route_inject", True)
    monkeypatch.setattr(memory_recall, "_route_topic",
                        lambda q, cwd, cfg: _ROUTED)
    posted = _capture_spans(monkeypatch)
    r = memory_recall.handle(_payload("unrelated gardening question entirely"))
    assert r is not None
    assert "<recalled_experience>" not in r.additional_context
    spans = [s for s in posted if s["name"] == "memory.recall"]
    assert len(spans) == 1
    attrs = spans[0]["attributes"]
    assert attrs["hit_count"] == 0
    assert attrs["topic"]["id"] == "topic-routing"
    assert attrs["block"] == r.additional_context
