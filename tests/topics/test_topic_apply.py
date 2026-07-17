"""Apply layer: GraphDiff composition, atomic apply, provenance.

Exercises the new write path that all accept/merge/replace calls flow
through after Phase A8. The legacy file-path API (accept_proposed_topic
etc.) is covered by `tests/test_topic_proposals.py`; this file focuses
on the lower-level `apply_diff(repo_id, ...)` contract.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlmodel import func, select

from lib.orm import SessionLocal
from lib.orm.models import GraphSnapshot, Repo, TopicAudit
from lib.topics.apply import ApplyOptions, apply_diff, resolve_diff_with_options
from lib.topics.core import load_graph_merged, topic_local_path, write_split_graph
from lib.topics.diff import GraphDiff, compute_topic_delta, diff_against_graph
from lib.topics.snapshots import latest_snapshot, resolve_or_create_repo


@pytest.fixture
def fresh_repo(fake_git_repo) -> Repo:
    """Repo registered in the ORM, pointing at the fake_git_repo tmp."""
    (fake_git_repo / ".regin" / "topics").mkdir(parents=True, exist_ok=True)
    return resolve_or_create_repo(str(fake_git_repo))


def _base_graph(name: str = "demo") -> dict:
    return {"version": 1, "repo": name,
            "updated_at": "2026-01-01T00:00:00Z", "topics": {}}


def _seed_base(repo_path, graph: dict) -> None:
    """Write `graph` verbatim as the git-tracked base split layout.

    apply now writes only the gitignored `topic.local.json` overlay, so the
    effective graph is `merge(base, overlay)`. Seeding the same in-memory
    `base` these diffs are built from (mirrors `bootstrap` + curation in a
    real repo) keeps the merged disk hash-equal to the stored snapshot.
    """
    write_split_graph(repo_path, graph)


def _topic(tid: str, *, aliases=(), refs=(), edges=()) -> dict:
    return {
        "label": tid.title(),
        "intent": f"{tid} intent",
        "status": "active",
        "aliases": list(aliases),
        "refs": list(refs),
        "edges": list(edges),
        "commands": [],
        "include_globs": [],
        "exclude_globs": [],
    }


def test_apply_diff_creates_snapshot_and_provenance(fake_git_repo, fresh_repo):
    base = _base_graph()
    fresh = {"id": "demo", **_topic("demo", aliases=["demo alias"])}
    fresh["id"] = "demo"
    diff = diff_against_graph(fresh, base, strategy="create")

    result = apply_diff(
        fresh_repo.id, diff,
        reason="accept",
        triggering_run_id="run-1",
    )

    assert result.snapshot_id is not None
    assert result.snapshot.is_latest == 1
    assert result.snapshot.reason == "accept"
    # Two rows: one baseline `topic_create` row (always emitted so the
    # downgrade origin-lookup can find topics with no aliases/refs/edges)
    # plus one `alias_added_by_create` row for the seeded alias.
    assert result.provenance_count == 2

    snap_graph = json.loads(result.snapshot.graph_json)
    assert "demo" in snap_graph["topics"]
    # Overlay export ran — apply writes the local overlay, not the base.
    assert topic_local_path(fake_git_repo).exists()
    assert not (fake_git_repo / ".regin/topics/topic.json").exists()


def test_apply_diff_second_apply_flips_prior_is_latest(fake_git_repo, fresh_repo):
    """The partial unique index requires that the prior is_latest row
    flips to 0 BEFORE the new row inserts. Cover the invariant: at the
    end of two consecutive applies, exactly one is_latest=1 row exists.
    """
    base = _base_graph()
    _seed_base(fake_git_repo, base)
    first = {"id": "a", **_topic("a")}
    first["id"] = "a"
    apply_diff(fresh_repo.id, diff_against_graph(first, base, strategy="create"),
               reason="accept")

    # Second apply over the new graph state (base + overlay).
    after_first = load_graph_merged(fake_git_repo)
    second = {"id": "b", **_topic("b")}
    second["id"] = "b"
    apply_diff(fresh_repo.id, diff_against_graph(second, after_first, strategy="create"),
               reason="accept")

    with SessionLocal() as s:
        n_latest = s.exec(
            select(func.count(GraphSnapshot.id))
            .where(GraphSnapshot.repo_id == fresh_repo.id)
            .where(GraphSnapshot.is_latest == 1)
        ).first()
        total = s.exec(
            select(func.count(GraphSnapshot.id))
            .where(GraphSnapshot.repo_id == fresh_repo.id)
        ).first()
    assert n_latest == 1
    assert total == 2


def test_apply_diff_bg_reindex_passes_applied_graph(monkeypatch, fake_git_repo, fresh_repo):
    """Regression: `_bg_reindex` MUST pass `diff.prospective_graph` to
    `index_wikis_best_effort` so the bg thread skips
    `load_authoritative_graph`.

    Without this, two consecutive applies race: the bg thread from
    apply #1 reads the snapshot, then reads `topic.json` *after* apply
    #2's disk write has landed. The hash mismatch triggers
    `_auto_seed_snapshot`, which silently inserts a 3rd `is_latest=1`
    snapshot and breaks the per-repo count invariant covered above.

    The drift-detect short-circuit in tests (DependencyError raised by
    `ensure_deps` before the drift check) is what hid the bug; assert
    the contract directly so a future refactor that drops `graph=` is
    caught even when the embedding deps aren't installed.
    """
    import time
    from lib.patterns import wiki_indexer

    captured: list[dict | None] = []

    def fake_index(repo, *, progress=None, graph=None):
        captured.append(graph)
        return None

    monkeypatch.setattr(wiki_indexer, "index_wikis_best_effort", fake_index)

    base = _base_graph()
    fresh = {"id": "x", **_topic("x")}
    fresh["id"] = "x"
    apply_diff(
        fresh_repo.id,
        diff_against_graph(fresh, base, strategy="create"),
        reason="accept",
    )

    deadline = time.monotonic() + 2.0
    while not captured and time.monotonic() < deadline:
        time.sleep(0.01)

    assert captured, "bg wiki reindex never ran"
    assert captured[0] is not None, (
        "_bg_reindex must pass the just-applied graph; otherwise the bg "
        "thread calls load_authoritative_graph which can race a second "
        "apply_diff and trigger _auto_seed_snapshot"
    )
    assert "x" in captured[0].get("topics", {})

    # Wrapper boundary: stubbing `index_wikis_best_effort` above means we
    # never actually call its real signature. A previous fix passed
    # `graph=` from apply.py but forgot to forward it through the wrapper
    # — the bg thread crashed with TypeError and the surrounding
    # try/except swallowed it. Pin the wrapper signature too so the next
    # editor of apply.py can't reintroduce that silent failure.
    import inspect
    sig = inspect.signature(wiki_indexer.index_wikis_best_effort)
    assert "graph" in sig.parameters, (
        "index_wikis_best_effort must accept `graph=` so callers can skip "
        "the drift-detecting load_authoritative_graph"
    )


def test_apply_diff_provenance_codes_match_strategy(fake_git_repo, fresh_repo):
    """Provenance rows tag each artefact with the strategy that
    introduced it. The bulk-fix tool (Phase F) queries by code.
    """
    base = _base_graph()
    base["topics"]["x"] = _topic("x", aliases=["alpha"])

    # Replace: alpha removed, gamma added.
    proposed = {"id": "x", **_topic("x", aliases=["gamma"])}
    proposed["id"] = "x"
    apply_diff(
        fresh_repo.id,
        diff_against_graph(proposed, base, strategy="replace"),
        reason="replace",
        triggering_run_id="run-replace",
    )

    with SessionLocal() as s:
        rows = list(s.exec(
            select(TopicAudit)
            .where(TopicAudit.repo_id == fresh_repo.id)
            .where(TopicAudit.kind == "provenance")
        ))
    codes = sorted(r.code for r in rows)
    assert "alias_added_by_replace" in codes
    assert "alias_removed_by_replace" in codes
    # Run id flows through.
    assert all(r.triggering_run_id == "run-replace" for r in rows)


def test_apply_diff_rejects_diff_with_introduced_errors(fake_git_repo, fresh_repo):
    """A diff that would introduce a new alias collision is_applyable=False.
    apply_diff refuses such diffs eagerly to prevent partial commits.
    """
    base = _base_graph()
    base["topics"]["existing"] = _topic("existing", aliases=["shared"])

    # Proposed new topic that introduces a colliding alias.
    bad = {"id": "newer", **_topic("newer", aliases=["shared"])}
    bad["id"] = "newer"
    diff = diff_against_graph(bad, base, strategy="create")
    assert not diff.is_applyable

    with pytest.raises(ValueError):
        apply_diff(fresh_repo.id, diff, reason="accept")


def test_apply_diff_allows_preexisting_rot(fake_git_repo, fresh_repo):
    """The defining property of the new diff layer: pre-existing rot
    in UNRELATED topics is surfaced as `graph_warnings` but does NOT
    block apply. Reproduces the recurring pain that triggered the
    refactor (cross-topic alias duplication blocking accept).
    """
    base = _base_graph()
    # Pre-seed two topics that share an alias — this is "rot".
    base["topics"]["a"] = _topic("a", aliases=["shared"])
    base["topics"]["b"] = _topic("b", aliases=["shared"])

    # Apply a new topic that's clean against both.
    fresh = {"id": "c", **_topic("c", aliases=["uniq"])}
    fresh["id"] = "c"
    diff = diff_against_graph(fresh, base, strategy="create")

    assert diff.is_applyable
    assert any(w.code == "graph.duplicate_alias" for w in diff.graph_warnings)
    assert not diff.introduced_errors

    # Apply succeeds despite the rot.
    result = apply_diff(fresh_repo.id, diff, reason="accept")
    assert result.snapshot.is_latest == 1


def test_apply_diff_merge_unions_aliases_and_refs(fake_git_repo, fresh_repo):
    base = _base_graph()
    base["topics"]["target"] = _topic(
        "target",
        aliases=["t-alias"],
        refs=[{"path": "README.md", "role": "overview"}],
    )
    _seed_base(fake_git_repo, base)

    incoming = {"id": "incoming", **_topic(
        "incoming",
        aliases=["i-alias"],
        refs=[{"path": "README.md", "role": "overview"},
              {"path": "docs/extra.md", "role": "docs"}],
    )}
    incoming["id"] = "incoming"

    diff = diff_against_graph(
        incoming, base, strategy="merge", target_topic_id="target",
    )
    assert diff.is_applyable
    assert len(diff.topic_deltas) == 1
    delta = diff.topic_deltas[0]
    assert delta.kind == "merge"
    assert delta.topic_id == "target"
    assert "i-alias" in delta.alias_adds
    # Existing alias is preserved, not duplicated.
    assert "t-alias" not in delta.alias_adds
    # New ref added, existing one not duplicated. Ref-delta keys carry tier
    # (defaulting to "primary") so a tier-only change surfaces in the diff.
    assert ("docs/extra.md", "docs", "primary") in delta.ref_adds
    assert ("README.md", "overview", "primary") not in delta.ref_adds

    apply_diff(fresh_repo.id, diff, reason="merge")
    after = load_graph_merged(fake_git_repo)
    after_target = after["topics"]["target"]
    # incoming is NOT inserted by merge — only target mutates.
    assert "incoming" not in after["topics"]
    assert sorted(after_target["aliases"]) == ["i-alias", "t-alias"]


def test_apply_diff_export_matches_snapshot(fake_git_repo, fresh_repo):
    """The merged disk (base topic.json + topic.local.json overlay) must
    match the latest snapshot's graph_json after each apply — the
    reconciliation invariant that keeps the drift detector quiet.
    """
    base = _base_graph()
    _seed_base(fake_git_repo, base)
    fresh = {"id": "demo", **_topic("demo")}
    fresh["id"] = "demo"
    diff = diff_against_graph(fresh, base, strategy="create")
    result = apply_diff(fresh_repo.id, diff, reason="accept")

    merged = load_graph_merged(fake_git_repo)
    snap = json.loads(result.snapshot.graph_json)
    assert merged == snap


def test_cross_store_consistency_holds_across_multiple_applies(fake_git_repo, fresh_repo):
    """The Phase D source-of-truth flip relies on
    sha256(merge(topic.json, topic.local.json)) == sha256(latest
    snapshot.graph_json) at every quiescent state.

    Pin the invariant explicitly: after each apply, hash the merged disk
    and the latest snapshot row, assert equal. If this test fails, the
    cross-store atomicity protocol is broken and the flip can NOT ship
    safely.
    """
    import hashlib

    def _hash(payload: dict) -> str:
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    base = _base_graph()
    _seed_base(fake_git_repo, base)
    for i, label in enumerate(["alpha", "beta", "gamma"]):
        topic_in = {"id": label, **_topic(label, aliases=[f"{label}-alias"])}
        topic_in["id"] = label
        graph_now = load_graph_merged(fake_git_repo) if i > 0 else base
        diff = diff_against_graph(topic_in, graph_now, strategy="create")
        apply_diff(fresh_repo.id, diff, reason="accept")

        disk_hash = _hash(load_graph_merged(fake_git_repo))
        snap = latest_snapshot(fresh_repo.id)
        snap_hash = _hash(json.loads(snap.graph_json))
        assert disk_hash == snap_hash, (
            f"after apply #{i+1} ({label}): "
            f"disk hash {disk_hash[:12]} != snapshot hash {snap_hash[:12]}"
        )


def test_apply_diff_raw_construction_with_compute_topic_delta(fake_git_repo, fresh_repo):
    """Verify the public `compute_topic_delta` helper produces the same
    delta shape that the Phase A8 shim builds. Lets the legacy shim and
    the new endpoint share the math.
    """
    _seed_base(fake_git_repo, _base_graph())
    before = _topic("z", aliases=["alpha"])
    after = _topic("z", aliases=["alpha", "beta"])
    delta = compute_topic_delta(
        topic_id_after="z", kind="replace", before=before, after=after,
    )
    assert delta.alias_adds == ("beta",)
    assert delta.alias_removes == ()

    prospective = _base_graph()
    prospective["topics"]["z"] = after
    diff = GraphDiff(
        topic_deltas=(delta,),
        graph_warnings=(),
        introduced_errors=(),
        valid_strategies_by_topic={"z": ("replace",)},
        strategy="replace",
        target_topic_id=None,
        proposed_topic_id="z",
        prospective_graph=prospective,
    )

    # Seed the existing topic first so replace has something to replace.
    pre_create = compute_topic_delta(
        topic_id_after="z", kind="create", before=None, after=before,
    )
    pre_diff = GraphDiff(
        topic_deltas=(pre_create,),
        graph_warnings=(),
        introduced_errors=(),
        valid_strategies_by_topic={"z": ("create",)},
        strategy="create",
        target_topic_id=None,
        proposed_topic_id="z",
        prospective_graph={"version": 1, "repo": "demo", "topics": {"z": before}},
    )
    apply_diff(fresh_repo.id, pre_diff, reason="seed")

    apply_diff(fresh_repo.id, diff, reason="replace")
    after_disk = load_graph_merged(fake_git_repo)
    assert sorted(after_disk["topics"]["z"]["aliases"]) == ["alpha", "beta"]


def test_resolve_diff_preserves_replace_target_missing_error():
    """Regression: resolve_diff_with_options must not strip strategy
    preconditions like `topic.replace_target_missing` from the diff's
    introduced_errors. Otherwise `/apply` with strategy=replace against
    a topic that's NOT in the approved graph silently "succeeds" with
    zero deltas — the proposal stamps `review_status=accepted` but the
    topic never re-enters the graph (reproduced from snapshot 78 on
    run 20260521T141950Z).
    """
    base = _base_graph()
    base["topics"]["other"] = _topic("other")

    proposed = {"id": "missing-topic", **_topic("missing-topic")}
    proposed["id"] = "missing-topic"

    raw = diff_against_graph(proposed, base, strategy="replace")
    assert not raw.is_applyable
    assert any(e.code == "topic.replace_target_missing" for e in raw.introduced_errors)

    # `/apply` defaults `prune_orphan_edges=True`, which is the trigger
    # condition: resolution must run, and it currently drops the
    # strategy error.
    resolved, _dropped = resolve_diff_with_options(
        raw, ApplyOptions(prune_orphan_edges=True),
    )
    assert any(
        e.code == "topic.replace_target_missing" for e in resolved.introduced_errors
    ), "strategy precondition error must survive resolution"
    assert not resolved.is_applyable


def test_resolve_diff_preserves_create_collision_error():
    """Companion to the replace_target_missing case: the same `create`
    precondition (`topic.id_collides_with_approved`) is also dropped by
    resolve_diff_with_options today. Cover the symmetric path so a fix
    has to handle both.
    """
    base = _base_graph()
    base["topics"]["dupe"] = _topic("dupe")

    proposed = {"id": "dupe", **_topic("dupe")}
    proposed["id"] = "dupe"

    raw = diff_against_graph(proposed, base, strategy="create")
    assert not raw.is_applyable
    assert any(
        e.code == "topic.id_collides_with_approved" for e in raw.introduced_errors
    )

    resolved, _dropped = resolve_diff_with_options(
        raw, ApplyOptions(prune_orphan_edges=True),
    )
    assert any(
        e.code == "topic.id_collides_with_approved" for e in resolved.introduced_errors
    ), "strategy precondition error must survive resolution"
    assert not resolved.is_applyable


def test_compute_topic_delta_surfaces_tier_only_ref_change():
    """A ref whose only change is its tier must show as a remove + add, so the
    apply-time DiffPanel is not silent about a tier reclassification."""
    before = {"refs": [{"path": "lib/x.py", "role": "impl", "tier": "primary"}]}
    after = {"refs": [{"path": "lib/x.py", "role": "impl", "tier": "reference"}]}
    delta = compute_topic_delta(topic_id_after="t", kind="replace", before=before, after=after)
    assert ("lib/x.py", "impl", "reference") in delta.ref_adds
    assert ("lib/x.py", "impl", "primary") in delta.ref_removes

    # A missing tier defaults to "primary" — no false positive against an
    # explicit primary.
    delta2 = compute_topic_delta(
        topic_id_after="t", kind="replace",
        before={"refs": [{"path": "lib/x.py", "role": "impl"}]},
        after={"refs": [{"path": "lib/x.py", "role": "impl", "tier": "primary"}]},
    )
    assert delta2.ref_adds == ()
    assert delta2.ref_removes == ()
