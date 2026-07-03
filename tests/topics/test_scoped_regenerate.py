"""Scoped content-drift regenerate: only the drifted topics' wikis are
re-derived; every other topic is preserved byte-identical, and the wiki_range
auto-close diffs per-topic so it never closes an untouched topic's note."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from lib.topics import topic_dir
from lib.topics.content_drift import _append_drift_note_to_origin
from lib.topics.proposal_external import (
    _apply_regenerate_scope,
    _pick_topic,
    _prior_reference_block,
    _regenerate_scope_topic_ids,
    _scoped_refresh_directive,
    _splice_scoped_topics,
    write_status,
)
from lib.topics.proposal_orm.feedback import _wiki_range_changed
from lib.topics.proposal_orm.runs import orm_save_proposal
from lib.topics.proposals.core_io import load_proposal
from lib.topics.proposals.external_jobs import _resolve_drift_scope
from lib.topics.snapshots import resolve_or_create_repo


def _prior():
    return {
        "version": 1,
        "topics": [
            {"id": "alpha", "label": "Alpha", "refs": [], "wiki": "## Alpha\nprior alpha"},
            {"id": "beta", "label": "Beta", "refs": [], "wiki": "## Beta\nprior beta"},
            {"id": "gamma", "label": "Gamma", "refs": [], "wiki": "## Gamma\nprior gamma"},
        ],
    }


# ── splice ────────────────────────────────────────────────────────────────

def test_splice_swaps_only_drifted_and_keeps_the_rest_verbatim():
    prior = _prior()
    drafted = {"version": 1, "topics": [
        {"id": "beta", "label": "Beta", "refs": [], "wiki": "## Beta\nNEW beta"},
    ]}
    prior_wiki = "shared intro\n\n## Alpha\nprior alpha\n\n## Beta\nprior beta\n\n## Gamma\nprior gamma"

    merged, wiki = _splice_scoped_topics(prior, drafted, prior_wiki, ["beta"])

    by_id = {t["id"]: t for t in merged["topics"]}
    # every topic is retained — no forward-edge loss on later per-doc apply
    assert list(by_id) == ["alpha", "beta", "gamma"]
    assert by_id["beta"]["wiki"] == "## Beta\nNEW beta"          # drifted → new body
    assert by_id["alpha"]["wiki"] == "## Alpha\nprior alpha"     # untouched → verbatim
    assert by_id["gamma"]["wiki"] == "## Gamma\nprior gamma"     # untouched → verbatim
    # combined wiki rebuilt: prior intro + new beta + verbatim alpha/gamma
    assert "shared intro" in wiki
    assert "NEW beta" in wiki
    assert "prior alpha" in wiki and "prior gamma" in wiki
    assert "prior beta" not in wiki


def test_splice_preserves_topic_when_agent_omits_it():
    # A scoped agent may return ONLY the drifted topics; a scoped id it did not
    # emit must still survive from the prior draft, never vanish.
    prior = _prior()
    drafted = {"version": 1, "topics": []}  # agent produced nothing

    merged, _ = _splice_scoped_topics(prior, drafted, "", ["beta"])

    assert [t["id"] for t in merged["topics"]] == ["alpha", "beta", "gamma"]
    assert all(t["wiki"].startswith(f"## {t['label']}") for t in merged["topics"])


def test_pick_topic_prefers_drafted_only_for_scoped_ids():
    scope = {"beta"}
    drafted_by_id = {"beta": {"id": "beta", "wiki": "new"}, "alpha": {"id": "alpha", "wiki": "leak"}}
    # alpha is drafted but NOT in scope → prior wins (drafted alpha ignored)
    assert _pick_topic({"id": "alpha", "wiki": "old"}, scope, drafted_by_id)["wiki"] == "old"
    assert _pick_topic({"id": "beta", "wiki": "old"}, scope, drafted_by_id)["wiki"] == "new"
    # scoped but absent from drafted → prior verbatim
    assert _pick_topic({"id": "delta", "wiki": "old"}, {"delta"}, {})["wiki"] == "old"


# ── scoped prompt directive ────────────────────────────────────────────────

def test_scoped_directive_empty_without_scope():
    assert _scoped_refresh_directive({}) == ""
    assert _scoped_refresh_directive({"scope_topic_ids": []}) == ""


def test_scoped_directive_lists_topics_and_paths():
    directive = _scoped_refresh_directive({
        "scope_topic_ids": ["beta", "gamma"],
        "scope_drifted_paths": {"beta": ["lib/b.py", "lib/b2.py"], "gamma": []},
    })
    assert "Scoped refresh" in directive
    assert "**beta**" in directive and "`lib/b.py`" in directive and "`lib/b2.py`" in directive
    assert "**gamma**" in directive and "(this topic's refs)" in directive  # no paths → fallback
    assert "ONLY the topics listed above" in directive


def test_prior_reference_block_embeds_scoped_directive():
    block = _prior_reference_block({
        "proposal": {"version": 1, "topics": []},
        "wiki": "prior wiki",
        "scope_topic_ids": ["beta"],
        "scope_drifted_paths": {"beta": ["lib/b.py"]},
    })
    assert "Scoped refresh" in block and "**beta**" in block


# ── per-topic wiki_range auto-close ─────────────────────────────────────────

def _thread(topic_id="beta", kind="wiki_range"):
    return SimpleNamespace(
        proposal_topic_id=topic_id,
        anchor_kind=kind,
        anchor_json=json.dumps({"topic_id": topic_id}),
    )


def test_wiki_range_diff_is_per_topic_not_combined():
    prev_topic = {"id": "beta", "wiki": "old beta"}
    next_topic = {"id": "beta", "wiki": "old beta"}  # this topic unchanged
    # combined blobs differ (a sibling topic changed) but THIS topic didn't →
    # the note must stay open.
    assert _wiki_range_changed(
        prev_topic, next_topic,
        {"wiki": "combined v1"}, {"wiki": "combined v2 with sibling change"},
    ) is False
    # same topic actually changed → addressed
    assert _wiki_range_changed(
        {"id": "beta", "wiki": "old"}, {"id": "beta", "wiki": "new"},
        {"wiki": "x"}, {"wiki": "x"},
    ) is True


def test_wiki_range_falls_back_to_combined_when_topic_absent_from_both():
    # The anchored topic is absent from both revisions (its topic was dropped)
    # → nothing per-topic to diff, so fall back to the combined blob. A present
    # topic never hits this path (the serializer emits "" for its wiki).
    assert _wiki_range_changed(None, None, {"wiki": "a"}, {"wiki": "b"}) is True
    assert _wiki_range_changed(None, None, {"wiki": "a"}, {"wiki": "a"}) is False


# ── integration: DB wiring (resolve → persist → splice → reset) ─────────────

def _topic_row(topic_id: str, wiki: str) -> dict:
    return {"id": topic_id, "label": topic_id.title(), "aliases": [], "intent": "i",
            "status": "active", "refs": [], "edges": [], "commands": [],
            "include_globs": [], "exclude_globs": [], "evidence_paths": [],
            "wiki": wiki}


def _seed_multi_topic_run(repo: Path, run_id: str) -> str:
    """A two-topic run whose combined wiki.md is on disk — the state a
    regenerate reads its prior draft from."""
    resolve_or_create_repo(str(repo))
    combined = ("intro line\n\n## Alpha\nprior alpha body\n\n## Beta\nprior beta body")
    orm_save_proposal(str(repo), run_id, {
        "provider": "external-agent", "scope": "all", "status": "pending_review",
        "generated_at": "2020-01-01T00:00:00Z", "metadata": {},
        "topics": [
            _topic_row("alpha", "## Alpha\nprior alpha body"),
            _topic_row("beta", "## Beta\nprior beta body"),
        ],
    }, wiki=combined)
    out_dir = topic_dir(repo) / "proposals" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "wiki.md").write_text(combined)
    return str(out_dir)


def test_scope_resolves_persists_splices_and_resets(fake_git_repo):
    repo = fake_git_repo
    run_id = "run-scoped-1"
    out_dir_str = _seed_multi_topic_run(repo, run_id)
    out_dir = Path(out_dir_str)

    # Prior draft actually persisted per-topic wiki (else the whole splice is moot)
    prior = load_proposal(repo, run_id)
    assert {t["id"]: t["wiki"] for t in prior["topics"]} == {
        "alpha": "## Alpha\nprior alpha body", "beta": "## Beta\nprior beta body"}

    # Only beta drifted → open a content-drift note anchored to beta
    _append_drift_note_to_origin(repo, run_id, "beta", ["lib/b.py"])

    # 1. resolve scope from the open drift thread
    scope = _resolve_drift_scope(repo, run_id)
    assert scope == {"topic_ids": ["beta"], "drifted_paths": {"beta": ["lib/b.py"]}}

    # 2. persist it on the run status and read it back across the boundary
    write_status(out_dir, {"state": "queued", "regenerate_drift_scope": scope})
    assert _regenerate_scope_topic_ids(out_dir) == ["beta"]

    # 3. splice a scoped redraft (agent returned ONLY beta, with a new body)
    drafted = {"version": 1, "repo": repo.name, "scope": "all", "status": "draft",
               "topics": [_topic_row("beta", "## Beta\nFRESH beta body")], "notes": []}
    merged, wiki = _apply_regenerate_scope(repo, out_dir, drafted, "## Beta\nFRESH beta body")

    by_id = {t["id"]: t for t in merged["topics"]}
    assert list(by_id) == ["alpha", "beta"]                       # nothing dropped
    assert by_id["alpha"]["wiki"] == "## Alpha\nprior alpha body"  # untouched verbatim
    assert by_id["beta"]["wiki"] == "## Beta\nFRESH beta body"     # drifted → new
    assert "intro line" in wiki and "FRESH beta body" in wiki and "prior alpha body" in wiki
    assert "prior beta body" not in wiki
    # timestamp not inherited from the prior revision (stamped fresh on append)
    assert "generated_at" not in merged

    # 4. a later full/manual regenerate writes empty scope → splice is a no-op
    write_status(out_dir, {"state": "queued",
                           "regenerate_drift_scope": {"topic_ids": [], "drifted_paths": {}}})
    assert _regenerate_scope_topic_ids(out_dir) == []
    same, same_wiki = _apply_regenerate_scope(repo, out_dir, drafted, "## Beta\nFRESH beta body")
    assert [t["id"] for t in same["topics"]] == ["beta"]           # drafted returned unchanged


def test_drift_thread_without_topic_id_does_not_poison_scope(fake_git_repo):
    repo = fake_git_repo
    run_id = "run-scoped-2"
    _seed_multi_topic_run(repo, run_id)
    # no drift notes at all → empty scope → full regenerate (unchanged behaviour)
    assert _resolve_drift_scope(repo, run_id) == {"topic_ids": [], "drifted_paths": {}}
