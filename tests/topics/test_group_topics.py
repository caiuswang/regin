"""Tests for `regin topics group`: pure plan assembly, the DB-free gate
(`check_group`), the agentic proposer's parse/fail-loud contract, and the CLI
dry-run (fake LLM, nothing written).

`check_group` is pure — pass a synthetic graph, no DB — mirroring
`test_split_gate`. The autouse `tmp_db` / `tmp_memory_db` fixtures isolate the
stores for the CLI test's `load_authoritative_graph`.
"""

from __future__ import annotations

import json

import pytest

from lib.topics.group_topics import (
    BucketCluster, ClusterProposerUnavailable, GroupPlan, apply_group,
    build_group_plan, check_group, propose_buckets,
)


class FakeLLM:
    def __init__(self, answer):
        self.answer = answer

    def complete(self, prompt, *, max_tokens=1024, surface_id=None):
        return self.answer


def _leaf(label: str, *, parent=None, kind=None) -> dict:
    node = {"label": label, "intent": f"about {label}", "status": "active",
            "parent_id": parent, "aliases": [], "refs": [], "edges": [],
            "commands": [], "include_globs": [], "exclude_globs": []}
    if kind:
        node["kind"] = kind
    return node


def _flat_graph(n: int = 4) -> dict:
    """`n` flat (unbucketed) leaf topics — no kind, no parent."""
    return {"version": 1, "repo": "test",
            "topics": {f"t{i}": _leaf(f"Topic {i}") for i in range(n)}}


def _bucket_body(label: str) -> dict:
    return {"kind": "bucket", "label": label, "intent": f"drill into {label}",
            "blurb": f"drill into {label}", "status": "active", "parent_id": None,
            "aliases": [], "refs": [], "edges": [], "commands": [],
            "include_globs": [], "exclude_globs": []}


def _good_plan() -> GroupPlan:
    return GroupPlan(
        new_buckets={"alpha": _bucket_body("Alpha"), "beta": _bucket_body("Beta")},
        assignment={"t0": "alpha", "t1": "alpha", "t2": "beta", "t3": "beta"})


# ── pure plan assembly ──────────────────────────────────────────

def test_build_group_plan_mints_buckets_and_maps_members():
    g = _flat_graph(4)
    clusters = [BucketCluster("Data Model", "card", ["t0", "t1"]),
                BucketCluster("Rule Engines", "card", ["t2", "t3"])]
    plan = build_group_plan(clusters, set(g["topics"]), g)

    assert set(plan.new_buckets) == {"data-model", "rule-engines"}
    assert all(b["kind"] == "bucket" for b in plan.new_buckets.values())
    assert all(b["parent_id"] is None for b in plan.new_buckets.values())
    assert plan.assignment == {"t0": "data-model", "t1": "data-model",
                               "t2": "rule-engines", "t3": "rule-engines"}


def test_build_group_plan_omitted_topic_not_in_assignment():
    g = _flat_graph(4)
    clusters = [BucketCluster("Data Model", "card", ["t0", "t1"])]  # t2,t3 omitted
    plan = build_group_plan(clusters, set(g["topics"]), g)
    assert set(plan.assignment) == {"t0", "t1"}
    assert "t2" not in plan.assignment and "t3" not in plan.assignment


def test_build_group_plan_unique_ids_on_slug_collision():
    g = _flat_graph(2)
    clusters = [BucketCluster("Rules!", "x", ["t0"]),
                BucketCluster("rules", "y", ["t1"])]
    plan = build_group_plan(clusters, set(g["topics"]), g)
    assert set(plan.new_buckets) == {"rules", "rules-2"}


def test_build_group_plan_ignores_unoffered_member_ids():
    g = _flat_graph(2)
    clusters = [BucketCluster("Data", "x", ["t0", "ghost"])]
    plan = build_group_plan(clusters, set(g["topics"]), g)
    assert plan.assignment == {"t0": "data"}   # 'ghost' was never offered


def test_build_group_plan_reuses_existing_same_label_bucket():
    # A second grouping pass re-proposes a bucket that already exists: fold into
    # it, don't mint an `agent-runtime-core-2` twin (the reported bug).
    g = {"version": 1, "repo": "test", "topics": {
        "agent-runtime-core": _bucket_body("Agent Runtime Core"),
        "t0": _leaf("Topic 0"),
    }}
    clusters = [BucketCluster("Agent Runtime Core", "x", ["t0"])]
    plan = build_group_plan(clusters, {"t0"}, g)
    assert plan.new_buckets == {}                       # nothing minted
    assert plan.assignment == {"t0": "agent-runtime-core"}


def test_build_group_plan_folds_within_pass_same_label():
    # Two clusters the LLM labelled identically in ONE pass → one bucket.
    g = _flat_graph(2)
    clusters = [BucketCluster("Agent Runtime Core", "x", ["t0"]),
                BucketCluster("Agent Runtime Core", "y", ["t1"])]
    plan = build_group_plan(clusters, set(g["topics"]), g)
    assert set(plan.new_buckets) == {"agent-runtime-core"}
    assert plan.assignment == {"t0": "agent-runtime-core",
                               "t1": "agent-runtime-core"}


def test_check_group_rejects_duplicate_label_of_existing_bucket():
    # Safety net: a plan from any source that mints a same-label twin is rejected.
    g = {"version": 1, "repo": "test", "topics": {
        "agent-runtime-core": _bucket_body("Agent Runtime Core"),
        "t0": _leaf("Topic 0"),
    }}
    plan = GroupPlan(
        new_buckets={"agent-runtime-core-2": _bucket_body("Agent Runtime Core")},
        assignment={"t0": "agent-runtime-core-2"})
    res = check_group(plan, g)
    assert not res.ok
    assert any("duplicates the label" in e for e in res.errors)


# ── gate: check_group (pure / DB-free) ──────────────────────────

def test_check_group_passes_valid_plan():
    res = check_group(_good_plan(), _flat_graph(4))
    assert res.ok, res.errors
    assert res.errors == []
    assert res.stats["new_buckets"] == 2
    assert res.stats["grouped"] == 4


def test_check_group_fails_bucket_missing_kind():
    plan = _good_plan()
    del plan.new_buckets["alpha"]["kind"]        # no longer a bucket
    res = check_group(plan, _flat_graph(4))
    assert not res.ok
    assert any("kind:'bucket'" in e for e in res.errors)


def test_check_group_fails_bucket_with_parent():
    plan = _good_plan()
    plan.new_buckets["alpha"]["parent_id"] = "beta"
    res = check_group(plan, _flat_graph(4))
    assert not res.ok
    assert any("parent_id=None" in e for e in res.errors)


def test_check_group_rejects_reparenting_an_existing_bucket():
    g = _flat_graph(3)
    g["topics"]["realbucket"] = _bucket_body("Real")   # a pre-existing bucket
    plan = GroupPlan(
        new_buckets={"alpha": _bucket_body("Alpha")},
        assignment={"t0": "alpha", "realbucket": "alpha"})  # bucket → bucket = illegal
    res = check_group(plan, g)
    assert not res.ok
    assert any("existing bucket" in e for e in res.errors)


def test_check_group_fails_target_not_a_bucket():
    g = _flat_graph(4)
    plan = GroupPlan(new_buckets={"alpha": _bucket_body("Alpha")},
                     assignment={"t0": "alpha", "t1": "t2"})  # t2 is a leaf, not a bucket
    res = check_group(plan, g)
    assert not res.ok
    assert any("not a kind:'bucket' node" in e for e in res.errors)


def test_check_group_fails_grouped_topic_routes_to_unclassified():
    """The 2-level trap: an assignment target that is not in the graph leaves
    the grouped topic with a dangling parent → build_tree quarantines it."""
    g = _flat_graph(2)
    plan = GroupPlan(new_buckets={"alpha": _bucket_body("Alpha")},
                     assignment={"t0": "alpha", "t1": "ghost-bucket"})
    res = check_group(plan, g)
    assert not res.ok
    assert any("unclassified" in e for e in res.errors)


def test_check_group_fails_nonexistent_assigned_topic():
    plan = GroupPlan(new_buckets={"alpha": _bucket_body("Alpha")},
                     assignment={"nope": "alpha"})
    res = check_group(plan, _flat_graph(4))
    assert not res.ok
    assert any("not in the graph" in e for e in res.errors)


def test_check_group_rejects_regrouping_an_already_placed_leaf():
    """A leaf already nested under a real bucket must not be re-homed by
    grouping — the gate enforces the flat-only precondition itself."""
    g = _flat_graph(2)
    g["topics"]["realbucket"] = _bucket_body("Real")
    g["topics"]["placed"] = _leaf("Placed", parent="realbucket")  # already bucketed
    plan = GroupPlan(new_buckets={"alpha": _bucket_body("Alpha")},
                     assignment={"t0": "alpha", "placed": "alpha"})
    res = check_group(plan, g)
    assert not res.ok
    assert any("only reparents flat topics" in e for e in res.errors)


def test_check_group_rejects_empty_bucket():
    """A minted bucket that ends up with no members is a hard error, not a
    warning — never write a childless top-level root."""
    plan = GroupPlan(
        new_buckets={"alpha": _bucket_body("Alpha"), "empty": _bucket_body("Empty")},
        assignment={"t0": "alpha", "t1": "alpha"})  # 'empty' claims nothing
    res = check_group(plan, _flat_graph(4))
    assert not res.ok
    assert any("no member topics" in e for e in res.errors)


def test_check_group_warns_on_singleton_bucket():
    plan = GroupPlan(new_buckets={"alpha": _bucket_body("Alpha")},
                     assignment={"t0": "alpha"})
    res = check_group(plan, _flat_graph(4))
    assert res.ok, res.errors
    assert any("< 2" in w for w in res.warnings)


# ── proposer parse / fail-loud ──────────────────────────────────

def test_propose_buckets_parses_and_filters_unknown_ids():
    flat = [{"id": "t0", "label": "A", "intent": "x"},
            {"id": "t1", "label": "B", "intent": "y"}]
    answer = ('[{"label": "Data", "intent": "card", "topic_ids": ["t0", "bogus"]}, '
              '{"label": "", "intent": "", "topic_ids": ["t1"]}]')
    clusters = propose_buckets(flat, FakeLLM(answer))
    assert len(clusters) == 1                     # empty-label cluster dropped
    assert clusters[0].label == "Data"
    assert clusters[0].topic_ids == ["t0"]        # unknown id filtered out


def test_propose_buckets_fail_loud_without_llm():
    with pytest.raises(ClusterProposerUnavailable):
        propose_buckets([{"id": "t0", "label": "A", "intent": ""}], FakeLLM(None))


# ── gated apply (end-to-end) ────────────────────────────────────

def _seed_repo(tmp_path, graph) -> str:
    d = tmp_path / ".regin" / "topics"
    d.mkdir(parents=True)
    (d / "topic.json").write_text(json.dumps(graph, indent=2, sort_keys=True) + "\n")
    return str(tmp_path)


def test_apply_group_end_to_end(tmp_path):
    g = _flat_graph(4)
    repo = _seed_repo(tmp_path, g)
    plan = _good_plan()

    result = apply_group(repo, plan, g)

    disk = json.loads((tmp_path / ".regin" / "topics" / "topic.json").read_text())
    assert disk["topics"]["alpha"]["kind"] == "bucket"
    assert disk["topics"]["t0"]["parent_id"] == "alpha"
    assert disk["topics"]["t2"]["parent_id"] == "beta"
    from lib.topics.tree import build_tree
    tree = build_tree(disk)
    assert "alpha" in tree["roots"] and "beta" in tree["roots"]
    assert set(tree["children"]["alpha"]) == {"t0", "t1"}
    assert result["grouped"] == 4


def test_apply_group_refuses_ungated_plan(tmp_path):
    g = _flat_graph(2)
    repo = _seed_repo(tmp_path, g)
    plan = GroupPlan(new_buckets={"alpha": _bucket_body("Alpha")},
                     assignment={"t0": "alpha", "t1": "ghost"})  # dangling ⇒ gate fails

    with pytest.raises(ValueError, match="gate failed"):
        apply_group(repo, plan, g)
    disk = json.loads((tmp_path / ".regin" / "topics" / "topic.json").read_text())
    assert "alpha" not in disk["topics"]              # nothing written
    assert disk["topics"]["t0"]["parent_id"] is None  # not reparented


# ── CLI dry-run (fake LLM, nothing written) ─────────────────────

def test_cli_group_dry_run_writes_nothing(tmp_path, monkeypatch, capsys):
    from cli.commands import topics as topics_cmd

    g = _flat_graph(4)
    repo = _seed_repo(tmp_path, g)
    before = (tmp_path / ".regin" / "topics" / "topic.json").read_text()

    answer = ('[{"label": "Data", "intent": "c", "topic_ids": ["t0", "t1"]}, '
              '{"label": "Rules", "intent": "c", "topic_ids": ["t2", "t3"]}]')
    monkeypatch.setattr("lib.memory.adapters.resolve_topic_classifier",
                        lambda: FakeLLM(answer))

    topics_cmd.cmd_topics_group(apply=False, repo=repo,
                                min_buckets=2, max_buckets=8)

    out = capsys.readouterr().out
    assert "gate: PASS" in out
    assert "(dry-run" in out
    after = (tmp_path / ".regin" / "topics" / "topic.json").read_text()
    assert after == before                            # dry-run wrote nothing


def test_cli_group_no_flat_topics_clean_exit(tmp_path, monkeypatch, capsys):
    import typer

    from cli.commands import topics as topics_cmd

    g = {"version": 1, "repo": "test", "topics": {"b": _bucket_body("B")}}
    repo = _seed_repo(tmp_path, g)

    with pytest.raises(typer.Exit) as exc:
        topics_cmd.cmd_topics_group(apply=False, repo=repo,
                                    min_buckets=3, max_buckets=8)
    assert exc.value.exit_code == 0
    assert "already bucketed" in capsys.readouterr().out
