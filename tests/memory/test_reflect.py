"""reflect() v4: mechanical pre-pass, the single dream LLM stage, lifecycle."""

from __future__ import annotations

import json

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


class PlanLLM:
    """Fake dream agent: returns one JSON plan (dict → serialized; string →
    verbatim, for the unparseable case) and records every prompt."""

    def __init__(self, plan):
        self.plan = plan
        self.prompts = []

    def complete(self, prompt, *, max_tokens=1024, surface_id=None):
        self.prompts.append(prompt)
        return self.plan if isinstance(self.plan, str) else json.dumps(self.plan)


def _remember(body, **kw):
    kw.setdefault("is_test", True)
    kw.setdefault("title", body[:80])  # lessons now require a (unique) title
    return memory.remember(body, **kw)


def _episodic(body, **kw):
    """Insert an already-consolidated (episodic, active) test memory directly,
    bypassing the working→episodic promotion."""
    from lib.memory.models import MemoryInput
    kw.setdefault("is_test", True)
    kw.setdefault("title", body[:80])  # lessons now require a (unique) title
    return memory.get_store().remember(MemoryInput(
        body=body, tier="episodic", status="active", **kw))


def _shared_ref_pair():
    """Two episodic rows naming the same repo path but otherwise dissimilar
    (unique content FIRST, structurally different bodies), so they never
    reach the dedup band yet qualify as a suspect pair. Returns
    (older_id, newer_id) by insertion order."""
    older = _episodic("BEFORE the refactor, recall settings were read "
                      "by a shim; the entry point sat in "
                      "lib/memory/store.py behind a wrapper.")
    newer = _episodic("Ownership moved: lib/memory/store.py now owns "
                      "recall directly and the wrapper shim is gone.")
    return older, newer


# ── mechanical pre-pass (no model) ───────────────────────────────────────────


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


def test_reflect_retires_legacy_digest_rows():
    """The digest stage is gone; the pre-pass retires any still-active
    legacy digest row (idempotent) and no stage ever sees it."""
    from lib.memory.models import MemoryInput
    from lib.memory.reflect import _validation_action_counts
    did = memory.get_store().remember(MemoryInput(
        body="Legacy standing briefing body.", title="Old digest",
        kind="digest", tier="episodic", status="active",
        tags=["digest"], is_test=True))

    result = reflect(memory.get_store())
    row = memory.get_store().get_dict(did)
    assert row["status"] == "retired"
    assert _validation_action_counts(
        memory.get_store(), did).get("retired") == 1
    # Nothing else happened to it: no merge/promote/decay counted.
    assert result.examined == 0 and result.promoted == 0

    second = reflect(memory.get_store())
    assert _validation_action_counts(
        memory.get_store(), did).get("retired") == 1  # not re-retired
    assert second.actions == []


# ── dream: the single agentic LLM stage ─────────────────────────────────────


def test_dream_makes_exactly_one_llm_call_for_rows_and_pairs():
    w1 = _remember("Fresh lesson: restart vite after proxy config edits.")
    w2 = _remember("Fresh lesson: usePage breaks when total is zero items.")
    older, newer = _shared_ref_pair()
    llm = PlanLLM({"actions": [
        {"action": "promote", "id": w1},
        {"action": "hold", "id": w2},
        {"action": "distinct", "older": older, "newer": newer},
    ]})
    result = reflect(memory.get_store(), llm=llm)
    assert len(llm.prompts) == 1          # one call covers rows AND pairs
    assert w1 in llm.prompts[0] and w2 in llm.prompts[0]
    assert older in llm.prompts[0] and newer in llm.prompts[0]
    assert result.promoted == 1 and result.held == 1
    assert result.pairs_checked == 1 and result.dream_skipped == 0
    assert memory.get_store().get_dict(w1)["tier"] == "episodic"
    assert memory.get_store().get_dict(w2)["tier"] == "working"


def test_dream_skips_llm_entirely_with_empty_pack():
    _episodic("Lone episodic note with no path references whatsoever.")
    llm = PlanLLM({"actions": []})
    result = reflect(memory.get_store(), llm=llm)
    assert llm.prompts == []
    assert result.promoted == 0 and result.pairs_checked == 0


def test_dream_disabled_blind_promotes_without_llm_call(monkeypatch):
    from lib.settings import settings
    monkeypatch.setattr(settings.agent_memory, "dream_enabled", False)
    w = _remember("A fresh lesson the disabled dream never sees.")
    _shared_ref_pair()
    llm = PlanLLM({"actions": [{"action": "drop", "id": w}]})
    result = reflect(memory.get_store(), llm=llm)
    assert llm.prompts == []
    assert result.promoted == 1 and result.dropped == 0
    assert memory.get_store().get_dict(w)["tier"] == "episodic"


def test_dream_no_llm_blind_promotes():
    w = _remember("A fresh lesson with no model configured.")
    result = reflect(memory.get_store())        # no llm
    assert result.promoted == 1 and result.pairs_checked == 0
    assert memory.get_store().get_dict(w)["tier"] == "episodic"


def test_dream_unparseable_plan_blind_promotes():
    w = _remember("A fresh lesson the model answers incoherently about.")
    llm = PlanLLM("sorry, I cannot produce a plan today")
    result = reflect(memory.get_store(), llm=llm)
    assert len(llm.prompts) == 1
    assert result.promoted == 1 and result.dream_skipped == 0
    assert memory.get_store().get_dict(w)["tier"] == "episodic"


def test_dream_unknown_or_bogus_actions_skip_and_fall_back():
    w = _remember("A fresh lesson the plan never mentions by its real id.")
    llm = PlanLLM({"actions": [
        {"action": "promote", "id": "no-such-id"},
        {"action": "levitate"},
    ]})
    result = reflect(memory.get_store(), llm=llm)
    assert result.dream_skipped == 2
    assert result.promoted == 1           # unmentioned row → blind promote
    assert memory.get_store().get_dict(w)["tier"] == "episodic"


def test_dream_drop_degrades_to_hold_by_default():
    w = _remember("A low-value one-off note.")
    llm = PlanLLM({"actions": [{"action": "drop", "id": w}]})
    result = reflect(memory.get_store(), llm=llm)
    assert result.held == 1 and result.dropped == 0
    row = memory.get_store().get_dict(w)
    assert row["status"] == "active" and row["tier"] == "working"


def test_dream_drop_retires_when_opted_in(monkeypatch):
    from lib.settings import settings
    monkeypatch.setattr(settings.agent_memory, "promote_allow_retire", True)
    w = _remember("A low-value one-off note.")
    llm = PlanLLM({"actions": [{"action": "drop", "id": w}]})
    result = reflect(memory.get_store(), llm=llm)
    assert result.dropped == 1 and result.held == 0
    assert memory.get_store().get_dict(w)["status"] == "retired"


def test_dream_merge_folds_into_pack_keeper(monkeypatch):
    from lib.settings import settings
    monkeypatch.setattr(settings.agent_memory, "promote_allow_retire", True)
    keeper = _episodic("Restart the backend after edits or tests hit "
                       "stale code paths.")
    w = _remember("Backend needs a restart following code edits; otherwise "
                  "stale behaviour shows in tests.")
    llm = PlanLLM({"actions": [{"action": "merge", "id": w, "keeper": keeper}]})
    result = reflect(memory.get_store(), llm=llm)
    assert result.merged == 1
    row = memory.get_store().get_dict(w)
    assert row["status"] == "retired" and row["superseded_by"] == keeper


def test_dream_merge_with_unknown_keeper_holds_the_row():
    """Uniform invalid-row-action rule: the model addressed the row, so a
    bad keeper means hold — never blind-promote what it wanted retired."""
    w = _remember("A lesson whose merge target does not exist in the pack.")
    llm = PlanLLM({"actions": [{"action": "merge", "id": w, "keeper": "nope"}]})
    result = reflect(memory.get_store(), llm=llm)
    assert result.dream_skipped == 1
    assert result.held == 1 and result.promoted == 0
    row = memory.get_store().get_dict(w)
    assert row["tier"] == "working" and row["status"] == "active"


def test_dream_circular_merge_holds_both_rows(monkeypatch):
    """`merge w1→w2; merge w2→w1` must not retire both rows into a circular
    supersede — a keeper that is itself merged away in the same plan is
    invalid, so both rows are held."""
    from lib.settings import settings
    monkeypatch.setattr(settings.agent_memory, "promote_allow_retire", True)
    w1 = _remember("First circular lesson about tokens in the settings loader.")
    w2 = _remember("Second circular lesson about flags in the router config.")
    llm = PlanLLM({"actions": [
        {"action": "merge", "id": w1, "keeper": w2},
        {"action": "merge", "id": w2, "keeper": w1},
    ]})
    result = reflect(memory.get_store(), llm=llm)
    assert result.merged == 0 and result.held == 2
    assert result.dream_skipped == 2 and result.promoted == 0
    for mid in (w1, w2):
        row = memory.get_store().get_dict(mid)
        assert row["status"] == "active" and row["tier"] == "working"
        assert row["superseded_by"] is None


def test_dream_merge_into_plan_corpse_holds_the_row(monkeypatch):
    """A keeper retired by an earlier action of the SAME plan is a corpse —
    the merge is skipped and the row held."""
    from lib.settings import settings
    monkeypatch.setattr(settings.agent_memory, "promote_allow_retire", True)
    older, newer = _shared_ref_pair()
    w = _remember("A lesson that tries to merge into a row this plan retires.")
    llm = PlanLLM({"actions": [
        {"action": "obsolete", "older": older, "newer": newer},
        {"action": "merge", "id": w, "keeper": older},
    ]})
    result = reflect(memory.get_store(), llm=llm)
    assert result.obsoleted == 1
    assert result.merged == 0 and result.held == 1
    assert result.dream_skipped == 1
    row = memory.get_store().get_dict(w)
    assert row["status"] == "active" and row["tier"] == "working"


def test_dream_duplicate_row_actions_first_wins():
    w = _remember("A lesson the plan decides about twice.")
    llm = PlanLLM({"actions": [
        {"action": "hold", "id": w},
        {"action": "promote", "id": w},
    ]})
    result = reflect(memory.get_store(), llm=llm)
    assert result.held == 1 and result.promoted == 0
    assert result.dream_skipped == 1
    assert memory.get_store().get_dict(w)["tier"] == "working"


def test_dream_contradict_sets_veracity_false():
    older, newer = _shared_ref_pair()
    llm = PlanLLM({"actions": [
        {"action": "contradict", "older": older, "newer": newer}]})
    result = reflect(memory.get_store(), llm=llm)
    assert result.contradictions == 1 and result.obsoleted == 0
    old_row = memory.get_store().get_dict(older)
    assert old_row["status"] == "retired"
    assert old_row["veracity"] == "false"
    assert old_row["superseded_by"] == newer


def test_dream_obsolete_retires_older_without_falsifying():
    older, newer = _shared_ref_pair()
    llm = PlanLLM({"actions": [
        {"action": "obsolete", "older": older, "newer": newer}]})
    result = reflect(memory.get_store(), llm=llm)
    assert result.obsoleted == 1 and result.contradictions == 0
    old_row = memory.get_store().get_dict(older)
    assert old_row["status"] == "retired"
    assert old_row["superseded_by"] == newer
    assert old_row["veracity"] == "unknown"          # untouched
    assert memory.get_store().get_dict(newer)["status"] == "active"


def test_dream_pair_order_is_created_at_not_model_claim():
    older, newer = _shared_ref_pair()
    llm = PlanLLM({"actions": [                       # swapped on purpose
        {"action": "obsolete", "older": newer, "newer": older}]})
    result = reflect(memory.get_store(), llm=llm)
    assert result.obsoleted == 1
    old_row = memory.get_store().get_dict(older)
    assert old_row["status"] == "retired"
    assert old_row["superseded_by"] == newer          # created_at decided
    assert memory.get_store().get_dict(newer)["status"] == "active"


def test_dream_pair_action_with_out_of_pack_ids_is_skipped():
    w = _remember("A working row so the pack is non-empty and the "
                  "dream actually runs.")
    llm = PlanLLM({"actions": [
        {"action": "promote", "id": w},
        {"action": "contradict", "older": "ghost-a", "newer": "ghost-b"}]})
    result = reflect(memory.get_store(), llm=llm)
    assert result.dream_skipped == 1 and result.contradictions == 0
    assert result.pairs_checked == 0


def test_dream_pair_verdict_requires_same_scope():
    """Two pack entries from different scopes can never be a pair — even
    when both are visible as neighbours."""
    w = _remember("A worker row mentioning cache directory naming rules.")
    a = _episodic("Alpha take: cache directory naming rules differ by host.",
                  scope="repo:alpha")
    b = _episodic("Beta take: cache directory naming rules got simplified.",
                  scope="repo:beta")
    llm = PlanLLM({"actions": [
        {"action": "promote", "id": w},
        {"action": "contradict", "older": a, "newer": b}]})
    result = reflect(memory.get_store(), llm=llm)
    assert result.dream_skipped == 1 and result.contradictions == 0
    assert memory.get_store().get_dict(a)["status"] == "active"
    assert memory.get_store().get_dict(b)["status"] == "active"


def test_dream_pair_verdict_spans_working_and_neighbour():
    """A pair verdict is not locked to the pre-offered suspect pairs: any
    two distinct same-scope pack entries qualify — here a working row
    obsoletes the episodic neighbour recall surfaced next to it."""
    ep = _episodic("The recall hook reads its config from the env block today.")
    w = _remember("Update: recall hook config moved; the env block is "
                  "removed now.")
    llm = PlanLLM({"actions": [
        {"action": "obsolete", "older": ep, "newer": w},
        {"action": "promote", "id": w},
    ]})
    result = reflect(memory.get_store(), llm=llm)
    assert result.obsoleted == 1 and result.pairs_checked == 1
    assert result.promoted == 1
    old_row = memory.get_store().get_dict(ep)
    assert old_row["status"] == "retired"
    assert old_row["superseded_by"] == w
    assert old_row["veracity"] == "unknown"


def test_dream_pair_retiring_working_row_marks_it_handled():
    """When the retired side of a pair verdict is a WORKING row, it is
    settled — the blind-promote fallback must not resurrect it."""
    w = _remember("Old belief: the settings loader tolerates a missing "
                  "config file silently.")
    ep = _episodic("Correction: the settings loader now raises when the "
                   "config file is missing.")
    llm = PlanLLM({"actions": [
        {"action": "contradict", "older": w, "newer": ep}]})
    result = reflect(memory.get_store(), llm=llm)
    assert result.contradictions == 1
    assert result.promoted == 0           # never blind-promoted afterwards
    row = memory.get_store().get_dict(w)
    assert row["status"] == "retired"
    assert row["veracity"] == "false" and row["superseded_by"] == ep


def test_dream_accepts_top_level_array_plan():
    w = _remember("A lesson delivered in a bare-array plan.")
    llm = PlanLLM([{"action": "hold", "id": w}])
    result = reflect(memory.get_store(), llm=llm)
    assert result.held == 1 and result.promoted == 0
    assert memory.get_store().get_dict(w)["tier"] == "working"


def test_dream_defers_working_overflow_and_keeps_pairs():
    """≥ the working cap: the pack holds the newest 25, suspect pairs are
    still offered, and overflow rows are deferred untouched (working, not
    blind-promoted)."""
    import uuid
    for i in range(50):
        _remember(f"unique-{uuid.uuid4().hex} standalone observation {i}")
    _shared_ref_pair()
    llm = PlanLLM({"actions": []})
    result = reflect(memory.get_store(), llm=llm)
    assert len(llm.prompts) == 1
    assert result.promoted == 25          # only the packed rows
    assert "OLDER:" in llm.prompts[0]     # a suspect pair was still offered
    assert any(a.startswith("defer 25 working") for a in result.actions)
    working_left = memory.get_store().list_memories(
        tier="working", status="active", include_tests=True, limit=100)
    assert len(working_left) == 25


def test_dream_pair_ledger_prevents_re_presentation():
    older, newer = _shared_ref_pair()
    llm = PlanLLM({"actions": [
        {"action": "distinct", "older": older, "newer": newer}]})
    first = reflect(memory.get_store(), llm=llm)
    assert first.pairs_checked == 1 and len(llm.prompts) == 1
    second = reflect(memory.get_store(), llm=llm)
    assert second.pairs_checked == 0
    assert len(llm.prompts) == 1          # empty pack → no second call


def test_dream_pack_caps_suspect_pairs_at_budget(monkeypatch):
    from lib.settings import settings
    monkeypatch.setattr(settings.agent_memory, "contradiction_budget", 2)
    bodies = [
        "ALPHA fact: the doctor endpoint lives beside cli/regin.py options.",
        "BETA gotcha: cli/regin.py must run under the venv interpreter.",
        "GAMMA decision: cli/regin.py subcommands stay flat, no groups.",
        "DELTA note: rebuild wipes what cli/regin.py init created earlier.",
    ]
    for body in bodies:
        _episodic(body)
    llm = PlanLLM({"actions": []})        # 6 candidate pairs > budget of 2
    result = reflect(memory.get_store(), llm=llm)
    assert len(llm.prompts) == 1
    assert llm.prompts[0].count("OLDER:") == 2
    assert result.pairs_checked == 0      # offered, but the plan judged none


def test_dream_skips_cross_scope_pairs():
    """Sharing a path string across scopes (two repos both naming
    docs/README.md) is not the same referent — never a candidate pair."""
    a = _episodic("Alpha-repo note: docs/README.md documents the fish caveats.",
                  scope="repo:alpha")
    b = _episodic("Beta-repo note: docs/README.md build badge is stale.",
                  scope="repo:beta")
    llm = PlanLLM({"actions": []})
    result = reflect(memory.get_store(), llm=llm)
    assert llm.prompts == [] and result.pairs_checked == 0
    assert memory.get_store().get_dict(a)["status"] == "active"
    assert memory.get_store().get_dict(b)["status"] == "active"


def test_dream_dry_run_reports_plan_but_applies_nothing(monkeypatch):
    from lib.settings import settings
    monkeypatch.setattr(settings.agent_memory, "promote_allow_retire", True)
    w = _remember("A fresh lesson the dry run would drop.")
    older, newer = _shared_ref_pair()
    llm = PlanLLM({"actions": [
        {"action": "drop", "id": w},
        {"action": "contradict", "older": older, "newer": newer},
    ]})
    result = reflect(memory.get_store(), llm=llm, dry_run=True)
    assert len(llm.prompts) == 1
    assert result.dropped == 1 and result.contradictions == 1   # reported
    assert result.actions                                        # …in actions
    row = memory.get_store().get_dict(w)
    assert row["status"] == "active" and row["tier"] == "working"
    old_row = memory.get_store().get_dict(older)
    assert old_row["status"] == "active" and old_row["veracity"] == "unknown"
    # The ledger was not written either: a second dry run re-presents it.
    second = reflect(memory.get_store(), llm=llm, dry_run=True)
    assert second.pairs_checked == 1


def test_forget_cascades_pair_checks():
    a, b = _shared_ref_pair()
    store = memory.get_store()
    store.record_pair_check(a, b, "DISTINCT")
    assert store.pair_checked(b, a)                  # order-insensitive
    store.forget(a)
    assert not store.pair_checked(a, b)


# ── dream synthesize: code-enforced constraints ──────────────────────────────

_SYNTH_TITLE = "Restart long-lived processes after editing their code"
_SYNTH_BODY = ("When a long-lived process serves stale behaviour after a "
               "code edit, restart it before trusting any test or UI check; "
               "a stale process mimics a real bug.")


def _seed_synth_sources():
    """Three episodic rows sharing a referent path, so all three land in
    the dream pack deterministically (as suspect-pair members). Returns
    their ids, oldest first."""
    e1 = _episodic("Case one: lib/proc/restart.py showed the dev server "
                   "kept old routes after an edit.", importance=0.3)
    e2 = _episodic("Case two: lib/proc/restart.py again — playwright hit a "
                   "not-yet-reloaded backend.", importance=0.9)
    e3 = _episodic("Case three: a reused backend from lib/proc/restart.py "
                   "served stale code until restart.", importance=0.5)
    return e1, e2, e3


def _synth_action(ids, body=_SYNTH_BODY):
    return {"action": "synthesize", "source_ids": list(ids),
            "title": _SYNTH_TITLE, "body": body}


def test_dream_synthesize_uses_median_importance_and_proposed_gate():
    e1, e2, e3 = _seed_synth_sources()
    llm = PlanLLM({"actions": [
        _synth_action([e1, e2, e3]),
        {"action": "distinct", "older": e1, "newer": e2},
    ]})
    result = reflect(memory.get_store(), llm=llm)
    assert result.synthesized == 1 and result.dream_skipped == 0
    synth = [r for r in memory.get_store().list_memories(
        status="proposed", include_tests=True)
        if "synthesis" in (r["tags"] or [])]
    assert len(synth) == 1
    s = synth[0]
    assert s["title"] == _SYNTH_TITLE and s["tier"] == "episodic"
    # median(0.3, 0.9, 0.5) = 0.5 — never above the sources' max.
    assert s["importance"] == 0.5
    assert s["status"] == "proposed"      # 0.5 < auto_approve_importance
    from sqlmodel import select
    from lib.memory.engine import MemorySessionLocal
    from lib.memory.models import MemoryValidation
    with MemorySessionLocal() as session:
        marked = session.exec(select(MemoryValidation.memory_id).where(
            MemoryValidation.action == "synthesized")).all()
    assert set(marked) == {e1, e2, e3}


def test_dream_synthesize_auto_approves_high_median():
    e1, e2, e3 = _seed_synth_sources()
    store = memory.get_store()
    for mid in (e1, e2, e3):
        store.update(mid, importance=0.9)
    llm = PlanLLM({"actions": [_synth_action([e1, e2, e3])]})
    result = reflect(store, llm=llm)
    assert result.synthesized == 1
    synth = [r for r in store.list_memories(status="active",
                                            include_tests=True)
             if "synthesis" in (r["tags"] or [])]
    assert len(synth) == 1
    assert synth[0]["importance"] == 0.9  # median, not max + bonus
    assert synth[0]["status"] == "active"  # 0.9 >= auto_approve_importance


def test_dream_synthesize_requires_three_distinct_pack_sources():
    e1, e2, _e3 = _seed_synth_sources()
    llm = PlanLLM({"actions": [_synth_action([e1, e1, e2])]})
    result = reflect(memory.get_store(), llm=llm)
    assert result.synthesized == 0 and result.dream_skipped == 1


def test_dream_synthesize_rejects_out_of_pack_sources():
    from lib.memory.models import MemoryInput
    e1, e2, _e3 = _seed_synth_sources()
    # A `proposed` row exists in the store but can never enter the pack:
    # recall only surfaces active rows and it shares no referent path.
    outsider = memory.get_store().remember(MemoryInput(
        body="A row that never entered the pack at all.",
        title="Out-of-pack row", tier="episodic", status="proposed",
        is_test=True))
    llm = PlanLLM({"actions": [_synth_action([e1, e2, outsider])]})
    result = reflect(memory.get_store(), llm=llm)
    assert result.synthesized == 0 and result.dream_skipped == 1


def test_dream_synthesize_rejects_working_sources():
    """A pending working row's fate belongs to its own row action — citing
    it as a synthesis source is invalid."""
    e1, e2, _e3 = _seed_synth_sources()
    w = _remember("Case four: another stale-process sighting during the "
                  "trace work, restart fixed it.")
    llm = PlanLLM({"actions": [
        _synth_action([w, e1, e2]),
        {"action": "promote", "id": w},
    ]})
    result = reflect(memory.get_store(), llm=llm)
    assert result.synthesized == 0 and result.dream_skipped == 1
    assert result.promoted == 1


def test_dream_synthesize_is_idempotent_across_runs():
    """Sources already folded into a synthesis (the `synthesized`
    validation) can't be re-cited — an identical plan next run mints no
    duplicate card."""
    e1, e2, e3 = _seed_synth_sources()
    llm = PlanLLM({"actions": [_synth_action([e1, e2, e3])]})
    first = reflect(memory.get_store(), llm=llm)
    assert first.synthesized == 1
    second = reflect(memory.get_store(), llm=llm)
    assert second.synthesized == 0 and second.dream_skipped == 1
    cards = [r for r in memory.get_store().list_memories(
        status="proposed", include_tests=True)
        if "synthesis" in (r["tags"] or [])]
    assert len(cards) == 1                # still exactly one card


def test_dream_synthesis_proposes_authoritative_topic(monkeypatch):
    """With `reflect_proposes_authoritative_topics` on, a dream synthesis
    feeds the authoritative proposal queue (and links the rule to the
    merged node) instead of minting an orphan `memory_topic`."""
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
    e1, e2, e3 = _seed_synth_sources()
    embedder = StubEmbedder({"MARKER-A": [1.0, 0.0, 0.0]})
    llm = PlanLLM({"actions": [_synth_action(
        [e1, e2, e3],
        body="MARKER-A restart the backend so tests hit new code "
             "rather than the cached old process.")]})
    result = reflect(memory.get_store(), embedder=embedder, llm=llm)
    assert result.synthesized == 1
    assert result.topics == 1                       # a proposal was emitted
    assert memory.get_store().list_topics() == []   # no orphan memory_topic
    synth = [r for r in memory.get_store().list_memories(
        status="proposed", include_tests=True)
        if "synthesis" in (r["tags"] or [])][0]
    assert memory.get_store().authoritative_topics_of(
        synth["id"]) == ["backend"]
    with SessionLocal() as s:
        run = s.get(ProposalRun, f"memory-reflect-{synth['id']}")
    assert run is not None and run.provider == "memory-reflect"


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
