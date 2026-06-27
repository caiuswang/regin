"""reflect(): dedup, contradiction, promotion, embedding, idempotency."""

from __future__ import annotations

import lib.memory as memory
from lib.memory.reflect import reflect


class StubEmbedder:
    """Deterministic EmbeddingProvider: maps known texts to fixed unit
    vectors so cosine similarity is fully controlled."""

    def __init__(self, vectors_by_substring):
        self._vectors = vectors_by_substring
        self.embedded_texts = []

    @property
    def model_id(self):
        return "stub-embed"

    def embed(self, texts):
        self.embedded_texts.extend(texts)
        out = []
        for t in texts:
            for marker, vec in self._vectors.items():
                if marker in t:
                    out.append(vec)
                    break
            else:
                out.append([0.0, 0.0, 1.0])
        return out


class StubLLM:
    def __init__(self, answer):
        self.answer = answer
        self.prompts = []

    def complete(self, prompt, *, max_tokens=1024):
        self.prompts.append(prompt)
        return self.answer


def _remember(body, **kw):
    kw.setdefault("is_test", True)
    kw.setdefault("title", body[:80])  # lessons now require a (unique) title
    return memory.remember(body, **kw)


def test_reflect_merges_text_duplicates_and_promotes():
    a = _remember("Restart the backend after edits, or E2E hits stale code.")
    b = _remember("Restart the backend after edits or E2E hits stale code!")
    c = _remember("Schema changes must also land in db/schema.sql.")

    result = reflect(memory.get_store())
    assert result.examined == 3
    assert result.merged == 1
    assert result.promoted == 2  # keeper of the pair + the distinct row

    rows = {m["id"]: m for m in memory.get_store().list_memories(
        include_tests=True)}
    pair = sorted((rows[a], rows[b]), key=lambda r: r["status"])
    keeper, loser = pair[0], pair[1]  # 'active' sorts before 'retired'
    assert [keeper["status"], loser["status"]] == ["active", "retired"]
    assert loser["superseded_by"] == keeper["id"]
    assert keeper["tier"] == "episodic" and keeper["consolidated_at"]
    assert rows[c]["tier"] == "episodic"


def test_reflect_dry_run_writes_nothing():
    _remember("Identical sentence about caching behavior.")
    _remember("Identical sentence about caching behavior?")
    result = reflect(memory.get_store(), dry_run=True)
    assert result.merged == 1 and result.promoted == 1
    rows = memory.get_store().list_memories(include_tests=True)
    assert all(r["tier"] == "working" and r["status"] == "active"
               for r in rows)


def test_reflect_is_idempotent():
    _remember("Some standalone lesson body.")
    first = reflect(memory.get_store())
    assert first.promoted == 1
    second = reflect(memory.get_store())
    assert second.examined == 0
    assert second.merged == 0 and second.promoted == 0


def test_reflect_dedup_via_stub_embedder_and_embeds_episodic():
    _remember("MARKER-A first phrasing of the rule")
    _remember("MARKER-B second phrasing, same meaning")
    _remember("MARKER-C unrelated other topic")
    # A and B share a vector (cosine 1.0 > threshold); C is orthogonal.
    embedder = StubEmbedder({
        "MARKER-A": [1.0, 0.0, 0.0],
        "MARKER-B": [1.0, 0.0, 0.0],
        "MARKER-C": [0.0, 1.0, 0.0],
    })
    result = reflect(memory.get_store(), embedder=embedder)
    assert result.merged == 1
    assert result.promoted == 2
    assert result.embedded == 2  # both surviving episodic rows got vectors
    assert memory.get_store().embedding_meta()  # vectors actually stored


def test_reflect_gray_zone_contradiction_with_llm():
    older = _remember("MARKER-A the deploy port is 8321")
    newer = _remember("MARKER-B the deploy port is 9000")
    # similarity 0.8: inside the gray zone [0.75, threshold)
    embedder = StubEmbedder({
        "MARKER-A": [1.0, 0.0, 0.0],
        "MARKER-B": [0.8, 0.6, 0.0],
    })
    llm = StubLLM("CONTRADICT")
    result = reflect(memory.get_store(), embedder=embedder, llm=llm)
    assert result.contradictions == 1
    assert llm.prompts  # the LLM was actually consulted
    old_row = memory.get_store().get_dict(older)
    assert old_row["status"] == "retired"
    assert old_row["veracity"] == "false"
    assert old_row["superseded_by"] == newer


def test_reflect_gray_zone_without_llm_leaves_both():
    _remember("MARKER-A the deploy port is 8321")
    _remember("MARKER-B the deploy port is 9000")
    embedder = StubEmbedder({
        "MARKER-A": [1.0, 0.0, 0.0],
        "MARKER-B": [0.8, 0.6, 0.0],
    })
    result = reflect(memory.get_store(), embedder=embedder)
    assert result.contradictions == 0
    assert result.promoted == 2


# ── synthesis: abstract a higher-order rule from a cluster of related rows ───


def _episodic(body, **kw):
    """Insert an already-consolidated (episodic, active) test memory directly,
    bypassing the working→episodic promotion so synthesis can be exercised in
    one reflect pass."""
    from lib.memory.models import MemoryInput
    kw.setdefault("is_test", True)
    kw.setdefault("title", body[:80])  # lessons now require a (unique) title
    return memory.get_store().remember(MemoryInput(
        body=body, tier="episodic", status="active", **kw))


# Three unit vectors with pairwise cosine ~0.70-0.74 — inside the synthesis
# band [0.55, dedup_cosine_threshold=0.92): related enough to cluster, far
# enough apart not to be merged as duplicates.
_CLUSTER_VECS = {
    "MARKER-A": [1.0, 0.0, 0.0],
    "MARKER-B": [0.7, 0.714, 0.0],
    "MARKER-C": [0.7, 0.357, 0.619],
}

_SYNTHESIS_JSON = (
    '{"title": "Restart the backend after editing server code", '
    '"body": "Across these cases the shared root cause was a stale process: '
    'after editing server-side code, restart the backend so tests and the UI '
    'exercise the new behaviour rather than the cached old one."}')


def _seed_cluster():
    _episodic("MARKER-A reused backend served stale code until restart")
    _episodic("MARKER-B the dev server kept old routes after an edit")
    _episodic("MARKER-C playwright asserted against a not-yet-reloaded backend")


def test_reflect_synthesizes_cluster_with_llm():
    _seed_cluster()
    embedder = StubEmbedder(_CLUSTER_VECS)
    llm = StubLLM(_SYNTHESIS_JSON)

    result = reflect(memory.get_store(), embedder=embedder, llm=llm)

    assert result.synthesized == 1
    rows = memory.get_store().list_memories(include_tests=True)
    synth = [r for r in rows if "synthesis" in (r["tags"] or [])]
    assert len(synth) == 1
    s = synth[0]
    assert s["tier"] == "episodic" and s["status"] == "active"
    assert s["title"] == "Restart the backend after editing server code"
    assert llm.prompts  # the LLM was actually consulted to abstract the rule
    # every source row is marked 'synthesized' (the idempotency guard)
    from sqlmodel import select
    from lib.memory.engine import MemorySessionLocal
    from lib.memory.models import MemoryValidation
    with MemorySessionLocal() as session:
        marked = session.exec(select(MemoryValidation.memory_id).where(
            MemoryValidation.action == "synthesized")).all()
    assert len(set(marked)) == 3


def test_reflect_synthesis_is_idempotent():
    _seed_cluster()
    embedder = StubEmbedder(_CLUSTER_VECS)
    llm = StubLLM(_SYNTHESIS_JSON)

    first = reflect(memory.get_store(), embedder=embedder, llm=llm)
    assert first.synthesized == 1
    # second pass: the cluster's members are already marked, so no fresh
    # cluster forms and nothing new is synthesised
    second = reflect(memory.get_store(), embedder=embedder, llm=llm)
    assert second.synthesized == 0
    synth = [r for r in memory.get_store().list_memories(include_tests=True)
             if "synthesis" in (r["tags"] or [])]
    assert len(synth) == 1  # still exactly one synthesis row


def test_reflect_proposes_authoritative_topic_when_enabled(monkeypatch):
    """With `reflect_proposes_authoritative_topics` on, synthesis feeds the
    authoritative proposal queue (and links the rule to the merged node)
    instead of minting an orphan `memory_topic`."""
    from lib.settings import settings
    from lib.orm.engine import SessionLocal
    from lib.orm.models.proposals import ProposalRun
    monkeypatch.setattr(settings.agent_memory,
                        "reflect_proposes_authoritative_topics", True)
    graph = {"topics": {"backend": {
        "label": "Backend", "intent": "MARKER-A server process",
        "refs": [{"path": "web/app.py", "role": "entrypoint"}]}}}
    monkeypatch.setattr("lib.topics.route.load_authoritative_graph",
                        lambda repo: graph)
    _seed_cluster()
    # The draft body carries MARKER-A, so the stub embeds the rule summary
    # onto the same vector as the backend node's identity text → a merge.
    synth_json = ('{"title": "Restart the backend after server edits", '
                  '"body": "MARKER-A restart the backend so tests hit new '
                  'code rather than the cached old process."}')
    embedder = StubEmbedder(_CLUSTER_VECS)
    llm = StubLLM(synth_json)

    result = reflect(memory.get_store(), embedder=embedder, llm=llm)

    assert result.synthesized == 1
    assert result.topics == 1                       # a proposal was emitted
    assert memory.get_store().list_topics() == []   # no orphan memory_topic
    synth = [r for r in memory.get_store().list_memories(include_tests=True)
             if "synthesis" in (r["tags"] or [])][0]
    # merge target already exists → the rule is linked to it now
    assert memory.get_store().authoritative_topics_of(synth["id"]) == ["backend"]
    with SessionLocal() as s:
        run = s.get(ProposalRun, f"memory-reflect-{synth['id']}")
    assert run is not None and run.provider == "memory-reflect"


def test_reflect_synthesis_skipped_without_llm():
    """Synthesis needs an LLM to abstract; with an embedder but no LLM the
    pass is a no-op (clustering alone never writes a memory)."""
    _seed_cluster()
    embedder = StubEmbedder(_CLUSTER_VECS)

    result = reflect(memory.get_store(), embedder=embedder)

    assert result.synthesized == 0
    synth = [r for r in memory.get_store().list_memories(include_tests=True)
             if "synthesis" in (r["tags"] or [])]
    assert synth == []


def test_reflect_no_synthesis_below_min_cluster():
    """Two related rows are below the minimum cluster size — no synthesis."""
    _episodic("MARKER-A reused backend served stale code until restart")
    _episodic("MARKER-B the dev server kept old routes after an edit")
    embedder = StubEmbedder(_CLUSTER_VECS)
    llm = StubLLM(_SYNTHESIS_JSON)

    result = reflect(memory.get_store(), embedder=embedder, llm=llm)

    assert result.synthesized == 0


# ── re-verification: flag memories whose named file paths no longer resolve ──


def _register_repo(tmp_path, monkeypatch, *, verify=True):
    """Point the repo registry at `tmp_path` and (optionally) enable the
    stale-reference pass. Returns the `repo:<name>` scope string."""
    from lib.settings import settings
    monkeypatch.setattr(settings, "repo_paths", [tmp_path])
    monkeypatch.setattr(settings.agent_memory, "verify_stale_refs", verify)
    return f"repo:{tmp_path.name}"


def test_reflect_flags_stale_file_reference(tmp_path, monkeypatch):
    """A memory naming a repo path that no longer resolves is flagged
    (stale_ref validation + veracity demote true→unknown); a sibling whose
    path still exists is left untouched."""
    from lib.memory.reflect import _validation_action_counts
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "live.py").write_text("x = 1\n")
    scope = _register_repo(tmp_path, monkeypatch)

    good = _remember("The recall entry point lives in lib/live.py today.",
                     scope=scope)
    stale = _remember("The old shim was at lib/gone.py before the refactor.",
                      scope=scope)
    store = memory.get_store()
    store.update(good, veracity="true")
    store.update(stale, veracity="true")

    result = reflect(store)

    assert result.flagged_stale == 1
    assert _validation_action_counts(store, stale).get("stale_ref") == 1
    assert "stale_ref" not in _validation_action_counts(store, good)
    assert store.get_dict(stale)["veracity"] == "unknown"   # demoted
    assert store.get_dict(good)["veracity"] == "true"        # untouched


def test_reflect_stale_ref_is_idempotent(tmp_path, monkeypatch):
    """A row flagged once is not re-flagged on the next reflect pass."""
    from lib.memory.reflect import _validation_action_counts
    scope = _register_repo(tmp_path, monkeypatch)
    stale = _remember("Patched in lib/missing/thing.py per the note.",
                      scope=scope)

    assert reflect(memory.get_store()).flagged_stale == 1
    assert reflect(memory.get_store()).flagged_stale == 0
    assert _validation_action_counts(
        memory.get_store(), stale).get("stale_ref") == 1


def test_reflect_stale_ref_disabled_by_default(tmp_path, monkeypatch):
    """With verify_stale_refs off (the default), a dangling path reference is
    never flagged — the pass is opt-in and filesystem-touching."""
    scope = _register_repo(tmp_path, monkeypatch, verify=False)
    _remember("References lib/gone.py which does not exist.", scope=scope)
    assert reflect(memory.get_store()).flagged_stale == 0


def test_reflect_stale_ref_skips_unverifiable_scope(tmp_path, monkeypatch):
    """Global-scoped memories can't be resolved to a repo root, so their path
    references are never verified — no false flags on unverifiable scopes."""
    _register_repo(tmp_path, monkeypatch)
    _remember("Global note mentioning lib/gone.py somewhere.", scope="global")
    assert reflect(memory.get_store()).flagged_stale == 0


# ── digest: the maintained per-scope structure layer (opt-in) ───────────────

_DIGEST_JSON = (
    '{"title": "Project briefing: build & test discipline", '
    '"body": "Restart the backend after server edits or tests hit stale code. '
    'Schema changes must also land in db/schema.sql. Use the .venv interpreter '
    'for every CLI command rather than the system python."}')


def _enable_digest(monkeypatch, **overrides):
    from lib.settings import settings
    monkeypatch.setattr(settings.agent_memory, "digest_enabled", True)
    for key, value in overrides.items():
        monkeypatch.setattr(settings.agent_memory, key, value)


def _seed_digest_sources(n=3):
    for i in range(n):
        _episodic(f"DIGEST-SRC-{i} a durable convention worth remembering #{i}")


def test_reflect_digest_disabled_by_default():
    """The digest stage is off unless `digest_enabled` is set — an LLM alone
    is not enough to opt in."""
    _seed_digest_sources()
    result = reflect(memory.get_store(), llm=StubLLM(_DIGEST_JSON))
    assert result.digests == 0
    assert memory.get_store().get_digest("global") is None


def test_reflect_writes_digest_when_enabled(monkeypatch):
    """With the flag on and an LLM, reflect rolls a scope's episodic rows into
    one `kind="digest"` memory — and that digest never appears in recall."""
    _enable_digest(monkeypatch)
    _seed_digest_sources()
    result = reflect(memory.get_store(), llm=StubLLM(_DIGEST_JSON))

    assert result.digests == 1
    digest = memory.get_store().get_digest("global")
    assert digest is not None
    assert digest["kind"] == "digest" and digest["tier"] == "episodic"
    assert digest["status"] == "active" and digest["scope"] == "global"
    assert digest["title"] == "Project briefing: build & test discipline"
    assert "digest" in (digest["tags"] or [])

    # Excluded from similarity recall: it exists (get_digest found it) yet
    # never surfaces as a recall hit, even on its own vocabulary.
    hits = memory.get_store().recall(
        "backend schema venv discipline", top_k=10, include_tests=True)
    assert all(h.memory["kind"] != "digest" for h in hits)


def test_reflect_digest_skipped_without_llm(monkeypatch):
    """The digest needs an LLM to write the briefing; enabled but LLM-less is
    a no-op."""
    _enable_digest(monkeypatch)
    _seed_digest_sources()
    assert reflect(memory.get_store()).digests == 0


def test_reflect_digest_needs_minimum_sources(monkeypatch):
    """A scope with fewer than the minimum sources isn't worth a digest."""
    _enable_digest(monkeypatch)
    _seed_digest_sources(n=2)
    assert reflect(memory.get_store(), llm=StubLLM(_DIGEST_JSON)).digests == 0


def test_reflect_digest_current_skips_regeneration(monkeypatch):
    """Once written, a fresh digest with no newer sources is left alone — the
    cadence guard keeps the per-scope LLM call off the hot path."""
    _enable_digest(monkeypatch)
    _seed_digest_sources()
    llm = StubLLM(_DIGEST_JSON)
    first = reflect(memory.get_store(), llm=llm)
    assert first.digests == 1
    original = memory.get_store().get_digest("global")

    second = reflect(memory.get_store(), llm=llm)
    assert second.digests == 0
    assert memory.get_store().get_digest("global")["id"] == original["id"]


def test_reflect_digest_refreshes_via_supersede(monkeypatch):
    """Enough newer sources trip the cadence guard: the digest is regenerated
    in place — the old row retired and chained to the new one — so exactly one
    stays active per scope."""
    _enable_digest(monkeypatch)
    _seed_digest_sources()
    llm = StubLLM(_DIGEST_JSON)
    reflect(memory.get_store(), llm=llm)
    original = memory.get_store().get_digest("global")

    # Three newer sources clear `digest_min_new_cards` (default 3).
    for i in range(3):
        _episodic(f"DIGEST-NEW-{i} a freshly learned convention #{i}")
    result = reflect(memory.get_store(), llm=llm)

    assert result.digests == 1
    current = memory.get_store().get_digest("global")
    assert current["id"] != original["id"]
    retired = memory.get(original["id"])
    assert retired.status == "retired"
    assert retired.superseded_by == current["id"]
    actives = memory.get_store().list_memories(
        kind="digest", status="active", include_tests=True)
    assert len(actives) == 1
