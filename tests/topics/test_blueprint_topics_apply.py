"""Phase B endpoints: /diff, /apply, /audit, /snapshots.

Covers the new endpoints added alongside the legacy
/accept, /merge, /replace endpoints in `web/blueprints/topics/apply.py`.
"""

from __future__ import annotations

import json
import subprocess
import types

from lib.orm import SessionLocal
from lib.orm.models import GraphSnapshot, Repo
from lib.settings import settings
from lib.topics import bootstrap, load_graph_merged
from lib.topics.proposals import load_proposal


def _seed_repo(path, name="apply-repo") -> str:
    """Register a Repo row pointing at `path`. Returns the canonical name."""
    with SessionLocal() as s:
        s.add(Repo(name=name, path=str(path), default_branch="main", is_active=1))
        s.commit()
    return name


def _create_proposal(flask_client, name, fake_git_repo) -> tuple[str, str]:
    """Create a stubbed proposal and return (proposal_id, topic_id).

    Picks the FIRST topic in the resulting proposal so the test doesn't
    depend on a stable id schema. Callers must activate the
    `stub_proposal_provider` fixture so the langchain request resolves
    to the in-test stub.
    """
    (fake_git_repo / "service").mkdir(exist_ok=True)
    (fake_git_repo / "service" / "api.py").write_text("import os\n")
    (fake_git_repo / "service" / "model.py").write_text("import sys\n")
    subprocess.check_call(["git", "-C", str(fake_git_repo), "add", "."])
    subprocess.check_call(["git", "-C", str(fake_git_repo), "commit", "-q", "-m", "fixture"])
    bootstrap(fake_git_repo)

    resp = flask_client.post(
        f"/api/repos/{name}/topics/proposals",
        json={"scope": "all", "provider": "langchain"},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    proposal_id = resp.get_json()["proposal"]["id"]
    ready = flask_client.post(
        f"/api/repos/{name}/topics/proposals/{proposal_id}/review-state",
        json={"review_state": "ready_to_apply"},
    )
    assert ready.status_code == 200, ready.get_data(as_text=True)
    proposal = load_proposal(str(fake_git_repo), proposal_id)
    assert proposal["topics"], "stub drafter produced no topics"
    topic_id = proposal["topics"][0]["id"]
    return proposal_id, topic_id


# ── /diff ──────────────────────────────────────────────────────────


def test_diff_endpoint_returns_serialized_graphdiff(stub_proposal_provider, flask_client, fake_git_repo):
    name = _seed_repo(fake_git_repo)
    proposal_id, topic_id = _create_proposal(flask_client, name, fake_git_repo)

    resp = flask_client.post(
        f"/api/repos/{name}/topics/proposals/{proposal_id}/topics/{topic_id}/diff",
        json={"strategy": "create"},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    diff = body["diff"]
    assert diff["strategy"] == "create"
    assert diff["proposed_topic_id"] == topic_id
    assert isinstance(diff["topic_deltas"], list)
    assert "dropped_items" in body  # always present, possibly empty


def test_diff_endpoint_rejects_unknown_strategy(stub_proposal_provider, flask_client, fake_git_repo):
    name = _seed_repo(fake_git_repo)
    proposal_id, topic_id = _create_proposal(flask_client, name, fake_git_repo)

    resp = flask_client.post(
        f"/api/repos/{name}/topics/proposals/{proposal_id}/topics/{topic_id}/diff",
        json={"strategy": "bogus"},
    )
    assert resp.status_code == 400


def test_diff_endpoint_surfaces_pre_existing_warnings_not_errors(stub_proposal_provider, flask_client, fake_git_repo):
    """The defining property: a diff sees pre-existing rot as
    graph_warnings (advisory) rather than introduced_errors (blocking).
    """
    name = _seed_repo(fake_git_repo)
    proposal_id, topic_id = _create_proposal(flask_client, name, fake_git_repo)

    # Plant a duplicate-alias collision in the approved graph that has
    # NOTHING to do with the proposal we're diffing.
    graph_path = fake_git_repo / ".regin/topics/topic.json"
    graph = json.loads(graph_path.read_text())
    graph["topics"]["existing-a"] = {
        "label": "A", "intent": "a", "status": "active",
        "aliases": ["shared"], "refs": [], "edges": [],
        "commands": [], "include_globs": [], "exclude_globs": [],
    }
    graph["topics"]["existing-b"] = {
        "label": "B", "intent": "b", "status": "active",
        "aliases": ["shared"], "refs": [], "edges": [],
        "commands": [], "include_globs": [], "exclude_globs": [],
    }
    graph_path.write_text(json.dumps(graph))

    resp = flask_client.post(
        f"/api/repos/{name}/topics/proposals/{proposal_id}/topics/{topic_id}/diff",
        json={"strategy": "create"},
    )
    body = resp.get_json()
    assert body["diff"]["is_applyable"]  # pre-existing rot doesn't block
    warning_codes = [w["code"] for w in body["diff"]["graph_warnings"]]
    assert "graph.duplicate_alias" in warning_codes


# ── /apply ─────────────────────────────────────────────────────────


def test_apply_endpoint_commits_snapshot_and_provenance(stub_proposal_provider, flask_client, fake_git_repo):
    name = _seed_repo(fake_git_repo)
    proposal_id, topic_id = _create_proposal(flask_client, name, fake_git_repo)

    resp = flask_client.post(
        f"/api/repos/{name}/topics/proposals/{proposal_id}/topics/{topic_id}/apply",
        json={"strategy": "create"},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["ok"] is True
    assert body["snapshot_id"] is not None

    with SessionLocal() as s:
        from sqlmodel import select
        snaps = list(s.exec(select(GraphSnapshot).where(GraphSnapshot.is_latest == 1)))
    assert len(snaps) == 1
    # Disk export ran.
    assert (fake_git_repo / ".regin/topics/topic.json").exists()


def test_apply_endpoint_requires_ready_review_state(stub_proposal_provider, flask_client, fake_git_repo):
    name = _seed_repo(fake_git_repo)
    (fake_git_repo / "service").mkdir(exist_ok=True)
    (fake_git_repo / "service" / "api.py").write_text("import os\n")
    subprocess.check_call(["git", "-C", str(fake_git_repo), "add", "."])
    subprocess.check_call(["git", "-C", str(fake_git_repo), "commit", "-q", "-m", "fixture"])
    bootstrap(fake_git_repo)

    resp = flask_client.post(
        f"/api/repos/{name}/topics/proposals",
        json={"scope": "all", "provider": "langchain"},
    )
    proposal_id = resp.get_json()["proposal"]["id"]
    proposal = load_proposal(str(fake_git_repo), proposal_id)
    topic_id = proposal["topics"][0]["id"]

    apply_resp = flask_client.post(
        f"/api/repos/{name}/topics/proposals/{proposal_id}/topics/{topic_id}/apply",
        json={"strategy": "create"},
    )

    assert apply_resp.status_code == 400
    assert "marked ready" in apply_resp.get_json()["error"]


def test_apply_endpoint_returns_400_on_unresolvable_errors(stub_proposal_provider, flask_client, fake_git_repo):
    """When the post-resolution diff is STILL not applyable, return 400
    with the resolved diff so the UI can render unresolved errors."""
    name = _seed_repo(fake_git_repo)
    proposal_id, topic_id = _create_proposal(flask_client, name, fake_git_repo)

    # Plant a topic that owns one of the proposed topic's aliases — that
    # collision can't be auto-resolved by the default options (no
    # dedupe_aliases) so apply should 400.
    proposal = load_proposal(str(fake_git_repo), proposal_id)
    proposed = next(t for t in proposal["topics"] if t["id"] == topic_id)
    aliases = proposed.get("aliases") or []
    if not aliases:
        # The stub drafter sometimes produces topics without aliases —
        # add one ourselves to force a collision.
        proposal["topics"][0]["aliases"] = ["forced-alias"]
        from lib.topics.proposals import save_proposal
        save_proposal(str(fake_git_repo), proposal_id, proposal)
        clashing_alias = "forced-alias"
    else:
        clashing_alias = aliases[0]

    # Sanity: confirm the alias survives `_approved_topic_from_proposal` —
    # if a future change to that helper filtered it, this test name lies.
    from lib.topics.proposals import _approved_topic_from_proposal
    refreshed = load_proposal(str(fake_git_repo), proposal_id)
    refreshed_topic = next(t for t in refreshed["topics"] if t["id"] == topic_id)
    approved_shape = _approved_topic_from_proposal(refreshed_topic, existing_topic_ids=set())
    assert clashing_alias in approved_shape["aliases"]

    graph_path = fake_git_repo / ".regin/topics/topic.json"
    graph = json.loads(graph_path.read_text())
    graph["topics"]["clasher"] = {
        "label": "C", "intent": "c", "status": "active",
        "aliases": [clashing_alias], "refs": [], "edges": [],
        "commands": [], "include_globs": [], "exclude_globs": [],
    }
    graph_path.write_text(json.dumps(graph))

    resp = flask_client.post(
        f"/api/repos/{name}/topics/proposals/{proposal_id}/topics/{topic_id}/apply",
        json={"strategy": "create"},
    )
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["ok"] is False
    assert body["error"] == "unresolvable_errors"
    assert "diff" in body
    assert any(e["code"] == "graph.duplicate_alias" for e in body["diff"]["introduced_errors"])


def test_apply_endpoint_resolves_with_options(stub_proposal_provider, flask_client, fake_git_repo):
    """Same collision as above; passing `dedupe_aliases=True` clears it."""
    name = _seed_repo(fake_git_repo)
    proposal_id, topic_id = _create_proposal(flask_client, name, fake_git_repo)

    proposal = load_proposal(str(fake_git_repo), proposal_id)
    proposal["topics"][0]["aliases"] = ["forced-alias"]
    from lib.topics.proposals import save_proposal, _approved_topic_from_proposal
    save_proposal(str(fake_git_repo), proposal_id, proposal)
    # Same insurance: forced-alias must survive the legacy shape-coercer.
    refreshed_topic = next(t for t in load_proposal(str(fake_git_repo), proposal_id)["topics"] if t["id"] == topic_id)
    assert "forced-alias" in _approved_topic_from_proposal(refreshed_topic, existing_topic_ids=set())["aliases"]

    graph_path = fake_git_repo / ".regin/topics/topic.json"
    graph = json.loads(graph_path.read_text())
    graph["topics"]["clasher"] = {
        "label": "C", "intent": "c", "status": "active",
        "aliases": ["forced-alias"], "refs": [], "edges": [],
        "commands": [], "include_globs": [], "exclude_globs": [],
    }
    graph_path.write_text(json.dumps(graph))

    resp = flask_client.post(
        f"/api/repos/{name}/topics/proposals/{proposal_id}/topics/{topic_id}/apply",
        json={"strategy": "create", "options": {"dedupe_aliases": True}},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["ok"] is True
    assert any(d["alias"] == "forced-alias" for d in body["dropped_items"]["duplicate_aliases"])


def test_apply_collapses_within_topic_duplicate_aliases(stub_proposal_provider, flask_client, fake_git_repo):
    """Regression: two aliases on the SAME proposed topic that normalize
    identically (the hyphenated id-form and its spaced form — exactly what
    the drafter emitted for `debug-missing-trace-span`) must not block
    apply. They're zero-information, so the shape layer collapses them
    first-wins; no `dedupe_aliases` option, no meaningful alias dropped.
    """
    name = _seed_repo(fake_git_repo)
    proposal_id, topic_id = _create_proposal(flask_client, name, fake_git_repo)

    # `alpha-beta` and `alpha beta` both normalize to `alpha beta`.
    proposal = load_proposal(str(fake_git_repo), proposal_id)
    proposal["topics"][0]["aliases"] = ["alpha-beta", "alpha beta", "gamma"]
    from lib.topics.proposals import save_proposal
    save_proposal(str(fake_git_repo), proposal_id, proposal)

    resp = flask_client.post(
        f"/api/repos/{name}/topics/proposals/{proposal_id}/topics/{topic_id}/apply",
        json={"strategy": "create"},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["ok"] is True
    # Silent collapse: the within-topic dup is NOT surfaced as a dropped
    # item — that channel is reserved for the destructive cross-topic case.
    assert body["dropped_items"]["duplicate_aliases"] == []

    # Persisted graph keeps exactly the first of the colliding pair.
    graph = load_graph_merged(fake_git_repo)
    assert graph["topics"][topic_id]["aliases"] == ["alpha-beta", "gamma"]


def test_apply_endpoint_is_idempotent_after_partial_crash(stub_proposal_provider, flask_client, fake_git_repo):
    """If apply_diff committed but save_proposal didn't get to update
    review_status (crash window), a re-click of Apply must not 400 on
    'topic already exists' — it should detect the prior snapshot via
    provenance and return 200 with already_applied=True.
    """
    name = _seed_repo(fake_git_repo)
    proposal_id, topic_id = _create_proposal(flask_client, name, fake_git_repo)

    r1 = flask_client.post(
        f"/api/repos/{name}/topics/proposals/{proposal_id}/topics/{topic_id}/apply",
        json={"strategy": "create"},
    )
    assert r1.status_code == 200
    snap_id = r1.get_json()["snapshot_id"]

    # Simulate the crash window: roll the proposal's review_status back
    # to pending so the UI's idempotency claim isn't covered by it.
    proposal = load_proposal(str(fake_git_repo), proposal_id)
    proposal["topics"][0].pop("review_status", None)
    proposal["topics"][0].pop("accepted_topic", None)
    proposal["topics"][0].pop("accepted_at", None)
    from lib.topics.proposals import save_proposal
    save_proposal(str(fake_git_repo), proposal_id, proposal)

    r2 = flask_client.post(
        f"/api/repos/{name}/topics/proposals/{proposal_id}/topics/{topic_id}/apply",
        json={"strategy": "create"},
    )
    assert r2.status_code == 200, r2.get_data(as_text=True)
    body = r2.get_json()
    assert body.get("already_applied") is True
    assert body["snapshot_id"] == snap_id


def test_apply_writes_regenerated_version_over_prior_apply(stub_proposal_provider, flask_client, fake_git_repo):
    """A regenerated draft (changed content) for an already-applied topic
    must be written on re-apply via replace — NOT short-circuited as
    already_applied. The idempotency guard keys only on (proposal, topic)
    + prior provenance; without a content check it falsely reported
    already_applied and silently dropped the new version.
    """
    name = _seed_repo(fake_git_repo)
    proposal_id, topic_id = _create_proposal(flask_client, name, fake_git_repo)

    # First apply lands the topic in the graph and writes provenance.
    r1 = flask_client.post(
        f"/api/repos/{name}/topics/proposals/{proposal_id}/topics/{topic_id}/apply",
        json={"strategy": "create"},
    )
    assert r1.status_code == 200, r1.get_data(as_text=True)
    assert r1.get_json().get("already_applied") is not True

    # Simulate a regenerate: same topic id, different content; the run is
    # ready again and the topic's accept markers are cleared (a fresh draft
    # has not been re-applied yet).
    new_intent = "REGENERATED INTENT — differs from the applied version"
    proposal = load_proposal(str(fake_git_repo), proposal_id)
    proposed = next(t for t in proposal["topics"] if t["id"] == topic_id)
    proposed["intent"] = new_intent
    for k in ("review_status", "accepted_topic", "accepted_at"):
        proposed.pop(k, None)
    proposal["status"] = "ready_to_apply"
    from lib.topics.proposals import save_proposal
    save_proposal(str(fake_git_repo), proposal_id, proposal)

    # Re-apply with replace (the id now collides with the approved topic).
    r2 = flask_client.post(
        f"/api/repos/{name}/topics/proposals/{proposal_id}/topics/{topic_id}/apply",
        json={"strategy": "replace"},
    )
    assert r2.status_code == 200, r2.get_data(as_text=True)
    body = r2.get_json()
    assert body["ok"] is True
    # The bug: this returned already_applied=True and never wrote the diff.
    assert body.get("already_applied") is not True

    # The live graph now reflects the regenerated content.
    graph = load_graph_merged(fake_git_repo)
    assert graph["topics"][topic_id]["intent"] == new_intent


def _post_apply(flask_client, name, proposal_id, topic_id, strategy):
    return flask_client.post(
        f"/api/repos/{name}/topics/proposals/{proposal_id}/topics/{topic_id}/apply",
        json={"strategy": strategy},
    )


def _reset_topic_review_markers(repo_path, proposal_id, topic_id, status="ready_to_apply"):
    """Simulate a byte-identical regenerate: accept markers cleared (the
    same field set production resets), topic content unchanged."""
    from lib.topics.proposals import save_proposal
    from lib.topics.proposals._common import _REGENERATE_RESET_TOPIC_FIELDS
    proposal = load_proposal(repo_path, proposal_id)
    proposed = next(t for t in proposal["topics"] if t["id"] == topic_id)
    for k in _REGENERATE_RESET_TOPIC_FIELDS:
        proposed.pop(k, None)
    proposal["status"] = status
    save_proposal(repo_path, proposal_id, proposal)


def _proposal_topic(repo_path, proposal_id, topic_id):
    proposal = load_proposal(repo_path, proposal_id)
    return proposal, next(t for t in proposal["topics"] if t["id"] == topic_id)


def test_noop_reapply_after_regenerate_advances_review_status(stub_proposal_provider, flask_client, fake_git_repo):
    """A regenerate whose redraft comes back byte-identical to the applied
    version hits the no-op short-circuit — which must still advance the
    topic's review_status, or the topic can never leave pending and the
    run is wedged at partially_applied forever.
    """
    name = _seed_repo(fake_git_repo)
    proposal_id, topic_id = _create_proposal(flask_client, name, fake_git_repo)
    assert _post_apply(flask_client, name, proposal_id, topic_id, "create").status_code == 200
    _reset_topic_review_markers(str(fake_git_repo), proposal_id, topic_id)

    r2 = _post_apply(flask_client, name, proposal_id, topic_id, "replace")
    assert r2.status_code == 200, r2.get_data(as_text=True)
    assert r2.get_json().get("already_applied") is True

    proposal, proposed = _proposal_topic(str(fake_git_repo), proposal_id, topic_id)
    assert proposed.get("review_status") == "accepted"
    assert proposed.get("accepted_topic") == topic_id
    assert proposed.get("replaced_existing") is True
    assert proposal["status"] == "applied"


def test_noop_reapply_skips_stamp_when_not_ready(stub_proposal_provider, flask_client, fake_git_repo):
    """The no-op short-circuit must stay read-only for proposals a real
    apply would refuse (pending_review / changes_requested) — stamping
    there would bypass the mark-ready review gate."""
    name = _seed_repo(fake_git_repo)
    proposal_id, topic_id = _create_proposal(flask_client, name, fake_git_repo)
    assert _post_apply(flask_client, name, proposal_id, topic_id, "create").status_code == 200
    _reset_topic_review_markers(str(fake_git_repo), proposal_id, topic_id, status="pending_review")

    r2 = _post_apply(flask_client, name, proposal_id, topic_id, "replace")
    assert r2.status_code == 200, r2.get_data(as_text=True)
    assert r2.get_json().get("already_applied") is True

    proposal, proposed = _proposal_topic(str(fake_git_repo), proposal_id, topic_id)
    assert proposed.get("review_status") is None
    assert proposal["status"] == "pending_review"


def test_noop_reapply_stamps_downgrade_pending_marker(stub_proposal_provider, flask_client, fake_git_repo):
    """Downgrade-created proposal topics carry the literal review_status
    'pending' (lib/topics/proposals/downgrade.py); the no-op path must
    treat that as un-reviewed, not as an already-stamped marker."""
    from lib.topics.proposals import save_proposal
    name = _seed_repo(fake_git_repo)
    proposal_id, topic_id = _create_proposal(flask_client, name, fake_git_repo)
    assert _post_apply(flask_client, name, proposal_id, topic_id, "create").status_code == 200
    _reset_topic_review_markers(str(fake_git_repo), proposal_id, topic_id)
    proposal, proposed = _proposal_topic(str(fake_git_repo), proposal_id, topic_id)
    proposed["review_status"] = "pending"
    save_proposal(str(fake_git_repo), proposal_id, proposal)

    r2 = _post_apply(flask_client, name, proposal_id, topic_id, "replace")
    assert r2.status_code == 200, r2.get_data(as_text=True)
    assert r2.get_json().get("already_applied") is True

    _, proposed = _proposal_topic(str(fake_git_repo), proposal_id, topic_id)
    assert proposed.get("review_status") == "accepted"


def test_noop_reapply_does_not_restamp_existing_markers(stub_proposal_provider, flask_client, fake_git_repo):
    """Once the no-op path has stamped a topic accepted, a further re-click
    must not rewrite the markers (accepted_at stays put)."""
    name = _seed_repo(fake_git_repo)
    proposal_id, topic_id = _create_proposal(flask_client, name, fake_git_repo)
    assert _post_apply(flask_client, name, proposal_id, topic_id, "create").status_code == 200
    _reset_topic_review_markers(str(fake_git_repo), proposal_id, topic_id)
    assert _post_apply(flask_client, name, proposal_id, topic_id, "replace").status_code == 200

    _, proposed = _proposal_topic(str(fake_git_repo), proposal_id, topic_id)
    accepted_at = proposed["accepted_at"]

    r3 = _post_apply(flask_client, name, proposal_id, topic_id, "replace")
    assert r3.status_code == 200, r3.get_data(as_text=True)
    assert r3.get_json().get("already_applied") is True
    _, proposed = _proposal_topic(str(fake_git_repo), proposal_id, topic_id)
    assert proposed["accepted_at"] == accepted_at


# ── /audit ─────────────────────────────────────────────────────────


def test_audit_endpoint_returns_graph_issues_grouped_by_code(stub_proposal_provider, flask_client, fake_git_repo):
    name = _seed_repo(fake_git_repo)
    bootstrap(fake_git_repo)

    # Plant a duplicate-alias collision.
    graph_path = fake_git_repo / ".regin/topics/topic.json"
    graph = json.loads(graph_path.read_text())
    graph["topics"]["x"] = {
        "label": "X", "intent": "x", "status": "active",
        "aliases": ["dup"], "refs": [], "edges": [],
        "commands": [], "include_globs": [], "exclude_globs": [],
    }
    graph["topics"]["y"] = {
        "label": "Y", "intent": "y", "status": "active",
        "aliases": ["dup"], "refs": [], "edges": [],
        "commands": [], "include_globs": [], "exclude_globs": [],
    }
    graph_path.write_text(json.dumps(graph))

    resp = flask_client.get(f"/api/repos/{name}/topics/audit")
    assert resp.status_code == 200
    body = resp.get_json()
    assert "graph.duplicate_alias" in body["by_code"]
    assert body["error_count"] >= 1


# ── /snapshots ─────────────────────────────────────────────────────


def test_snapshots_endpoint_lists_history(stub_proposal_provider, flask_client, fake_git_repo):
    name = _seed_repo(fake_git_repo)
    proposal_id, topic_id = _create_proposal(flask_client, name, fake_git_repo)

    # Apply once to produce a snapshot.
    flask_client.post(
        f"/api/repos/{name}/topics/proposals/{proposal_id}/topics/{topic_id}/apply",
        json={"strategy": "create"},
    )

    resp = flask_client.get(f"/api/repos/{name}/topics/snapshots")
    assert resp.status_code == 200
    body = resp.get_json()
    # Phase E3 auto-seeds a snapshot on first read, then the apply
    # commits a second one. The latest=apply, the prior=auto_seed.
    assert len(body["snapshots"]) >= 1
    latest = body["snapshots"][0]
    assert latest["is_latest"]
    assert body["latest_id"] == latest["id"]


def test_snapshots_pin_unpin_round_trip(stub_proposal_provider, flask_client, fake_git_repo):
    name = _seed_repo(fake_git_repo)
    proposal_id, topic_id = _create_proposal(flask_client, name, fake_git_repo)
    flask_client.post(
        f"/api/repos/{name}/topics/proposals/{proposal_id}/topics/{topic_id}/apply",
        json={"strategy": "create"},
    )
    list_resp = flask_client.get(f"/api/repos/{name}/topics/snapshots")
    snap_id = list_resp.get_json()["snapshots"][0]["id"]

    pin_resp = flask_client.post(f"/api/repos/{name}/topics/snapshots/{snap_id}/pin")
    assert pin_resp.status_code == 200
    assert pin_resp.get_json()["snapshot"]["pinned"] is True

    unpin_resp = flask_client.post(f"/api/repos/{name}/topics/snapshots/{snap_id}/unpin")
    assert unpin_resp.status_code == 200
    assert unpin_resp.get_json()["snapshot"]["pinned"] is False


def test_snapshots_restore_clones_and_flips_latest(stub_proposal_provider, flask_client, fake_git_repo):
    """Apply A, apply B, restore A — the restore creates a NEW snapshot
    pointing at A's state, and exactly one is_latest row remains."""
    name = _seed_repo(fake_git_repo)
    proposal_id, topic_id = _create_proposal(flask_client, name, fake_git_repo)

    r1 = flask_client.post(
        f"/api/repos/{name}/topics/proposals/{proposal_id}/topics/{topic_id}/apply",
        json={"strategy": "create"},
    ).get_json()
    snap_a = r1["snapshot_id"]

    # Tweak the graph to differ between A and B, then apply B by manually
    # writing a fresh topic to the graph and creating another snapshot.
    # Simpler path: just call /restore on snap_a after a second apply.
    # The stub proposal usually has multiple topics — apply
    # another one to land snapshot B.
    proposal = load_proposal(str(fake_git_repo), proposal_id)
    other_topic = next((t for t in proposal["topics"] if t["id"] != topic_id), None)
    if other_topic is not None:
        r2 = flask_client.post(
            f"/api/repos/{name}/topics/proposals/{proposal_id}/topics/{other_topic['id']}/apply",
            json={"strategy": "create"},
        ).get_json()
        assert r2["ok"]

    restore_resp = flask_client.post(f"/api/repos/{name}/topics/snapshots/{snap_a}/restore")
    assert restore_resp.status_code == 200
    restored = restore_resp.get_json()["snapshot"]
    assert restored["is_latest"]
    assert restored["reason"] == "undo"

    with SessionLocal() as s:
        from sqlmodel import select, func
        latest_count = s.exec(
            select(func.count(GraphSnapshot.id)).where(GraphSnapshot.is_latest == 1)
        ).first()
    assert latest_count == 1


# ── multi-doc proposal forward-edge staging ─────────────────────────
#
# A proposal can hold several topics that reference one another. When the
# user approves them one at a time, applying doc1 (which edges to a
# not-yet-applied doc3) prunes the doc1→doc3 edge to keep the graph valid.
# `_stage_forward_sibling_edges` stashes that edge so applying doc3 later
# re-attaches it — otherwise the inter-doc edges are silently lost.


def _proposal_with_forward_edge() -> dict:
    """doc1 --related--> doc3; doc2 stands alone. None applied yet."""
    return {
        "topics": [
            {"id": "doc1", "label": "Doc 1",
             "edges": [{"type": "related", "target": "doc3"}]},
            {"id": "doc2", "label": "Doc 2", "edges": []},
            {"id": "doc3", "label": "Doc 3", "edges": []},
        ],
    }


def test_stage_forward_sibling_edges_records_dropped_forward_ref(monkeypatch):
    from lib.topics.proposals import apply_service as apply_mod

    # Live graph holds only doc1 (just applied); doc3 not yet present.
    monkeypatch.setattr(
        apply_mod, "load_authoritative_graph",
        lambda _p: {"topics": {"doc1": {"id": "doc1", "edges": []}}},
    )
    proposal = _proposal_with_forward_edge()
    proposed = proposal["topics"][0]            # doc1
    approved = {"id": "doc1", "edges": []}      # post-prune shape

    apply_mod._stage_forward_sibling_edges(
        "/repo", proposal, proposed, approved, strategy="create",
    )

    # Keyed by the not-yet-applied TARGET, valued by {source: [edge]} —
    # the exact format _restore_pruned_inbound_edges_after_apply consumes.
    bucket = proposal["metadata"]["pruned_inbound_edges"]
    assert bucket == {"doc3": {"doc1": [{"type": "related", "target": "doc3"}]}}


def test_stage_forward_sibling_edges_skips_already_present_target(monkeypatch):
    from lib.topics.proposals import apply_service as apply_mod

    # doc3 is ALREADY in the graph, so the edge survives the normal apply
    # and must NOT be staged (it would double-restore).
    monkeypatch.setattr(
        apply_mod, "load_authoritative_graph",
        lambda _p: {"topics": {"doc1": {}, "doc3": {}}},
    )
    proposal = _proposal_with_forward_edge()
    apply_mod._stage_forward_sibling_edges(
        "/repo", proposal, proposal["topics"][0], {"id": "doc1"}, strategy="create",
    )
    assert not proposal.get("metadata", {}).get("pruned_inbound_edges")


def test_stage_forward_sibling_edges_ignores_non_sibling_and_merge(monkeypatch):
    from lib.topics.proposals import apply_service as apply_mod

    monkeypatch.setattr(
        apply_mod, "load_authoritative_graph", lambda _p: {"topics": {}},
    )
    # Edge target is not a sibling in this proposal → not our concern.
    proposal = {
        "topics": [{"id": "doc1", "edges": [{"type": "related", "target": "stranger"}]}],
    }
    apply_mod._stage_forward_sibling_edges(
        "/repo", proposal, proposal["topics"][0], {"id": "doc1"}, strategy="create",
    )
    assert not proposal.get("metadata", {}).get("pruned_inbound_edges")

    # merge folds edges into a different target id — out of scope, skip.
    proposal2 = _proposal_with_forward_edge()
    apply_mod._stage_forward_sibling_edges(
        "/repo", proposal2, proposal2["topics"][0], {"id": "doc1"}, strategy="merge",
    )
    assert not proposal2.get("metadata", {}).get("pruned_inbound_edges")


def test_forward_edge_survives_stage_then_restore_round_trip(monkeypatch):
    """End-to-end: stage the edge when doc1 is applied first, then prove the
    existing restore helper re-attaches it once doc3 lands."""
    from lib.topics.proposals import apply_service as apply_mod
    from lib.topics.proposals import _restore_pruned_edges

    monkeypatch.setattr(
        apply_mod, "load_authoritative_graph",
        lambda _p: {"topics": {"doc1": {"id": "doc1", "edges": []}}},
    )
    proposal = _proposal_with_forward_edge()
    apply_mod._stage_forward_sibling_edges(
        "/repo", proposal, proposal["topics"][0], {"id": "doc1"}, strategy="create",
    )

    # Now doc3 is applied: the live graph has doc1 and doc3. Replay the
    # restore the apply route runs (bucket keyed by the just-applied id).
    live_topics = {"doc1": {"id": "doc1", "edges": []}, "doc3": {"id": "doc3", "edges": []}}
    pruned = proposal["metadata"]["pruned_inbound_edges"]["doc3"]
    _restore_pruned_edges(live_topics, pruned)

    assert live_topics["doc1"]["edges"] == [{"type": "related", "target": "doc3"}]


# ── content-drift baseline advance on apply ─────────────────────────
#
# The modern /apply path commits via apply_diff directly, bypassing the
# legacy accept/replace shims that re-fingerprint a topic's refs. Without
# `_advance_drift_baseline_after_apply` the stored TopicRefDigest stays at
# the pre-refresh hash, so `regin topics evolve` re-detects the SAME drift
# on every subsequent pass — the reported bug. These cover the helper
# (deterministic) and the live endpoint.


def _drift_graph(repo, topic_id="t1", path="svc.py") -> None:
    """Write an approved graph with one topic referencing `path`."""
    from lib.topics.core import topic_path
    p = topic_path(repo)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"version": 1, "repo": repo.name, "topics": {
        topic_id: {
            "label": "T", "intent": "t", "status": "active", "aliases": [],
            "refs": [{"path": path}], "edges": [], "commands": [],
            "include_globs": [], "exclude_globs": [],
        },
    }}))


def _resolved_for(topic_id: str):
    """Minimal stand-in for a resolved GraphDiff — the helper only reads
    `resolved.topic_deltas[0].topic_id`."""
    return types.SimpleNamespace(
        topic_deltas=[types.SimpleNamespace(topic_id=topic_id)]
    )


def test_advance_baseline_resolves_redetected_drift(fake_git_repo, monkeypatch):
    """The exact reported bug: a topic whose ref drifted, then whose refresh
    is applied through the modern path, must NOT be re-detected as drifted."""
    from lib.topics.content_drift import detect_drifted_topics
    from lib.topics.ref_digest import capture_ref_digests
    from lib.topics.snapshots import resolve_or_create_repo
    from lib.topics.proposals import apply_service as apply_mod

    monkeypatch.setattr(settings.topic_evolution, "evolution_enabled", True)
    repo = fake_git_repo
    (repo / "svc.py").write_text("v1\n")
    _drift_graph(repo)
    resolve_or_create_repo(str(repo))
    capture_ref_digests(repo, "t1")                    # baseline at v1
    (repo / "svc.py").write_text("v2 — code moved out from under the wiki\n")
    assert detect_drifted_topics(repo) == [
        {"topic_id": "t1", "drifted_paths": ["svc.py"]}
    ]

    # Apply the refresh through the same seam the /apply endpoint uses.
    apply_mod._advance_drift_baseline_after_apply(
        str(repo), _resolved_for("t1"), strategy="replace",
    )

    assert detect_drifted_topics(repo) == []           # baseline advanced to v2


def test_advance_baseline_is_noop_when_evolution_disabled(fake_git_repo, monkeypatch):
    """Gated off (default): the apply path must not touch digests, so the
    drift signal is left exactly as it was — zero behaviour change."""
    from lib.topics.content_drift import detect_drifted_topics
    from lib.topics.ref_digest import capture_ref_digests
    from lib.topics.snapshots import resolve_or_create_repo
    from lib.topics.proposals import apply_service as apply_mod

    monkeypatch.setattr(settings.topic_evolution, "evolution_enabled", False)
    repo = fake_git_repo
    (repo / "svc.py").write_text("v1\n")
    _drift_graph(repo)
    resolve_or_create_repo(str(repo))
    capture_ref_digests(repo, "t1")
    (repo / "svc.py").write_text("v2\n")
    before = detect_drifted_topics(repo)
    assert before == [{"topic_id": "t1", "drifted_paths": ["svc.py"]}]

    apply_mod._advance_drift_baseline_after_apply(
        str(repo), _resolved_for("t1"), strategy="replace",
    )

    assert detect_drifted_topics(repo) == before       # still drifted — untouched


def test_apply_endpoint_captures_drift_baseline(
    stub_proposal_provider, flask_client, fake_git_repo, monkeypatch,
):
    """End-to-end through the real /apply endpoint: with evolution on, applying
    fingerprints the topic's refs so a later code change is detectable as
    drift. Pre-fix the modern path captured nothing — drift could never be
    judged, nor a refresh ever resolve it."""
    from lib.topics.content_drift import detect_drifted_topics
    from lib.topics.ref_digest import digests_for_topic, repo_id_for_path

    monkeypatch.setattr(settings.topic_evolution, "evolution_enabled", True)
    name = _seed_repo(fake_git_repo)
    proposal_id, topic_id = _create_proposal(flask_client, name, fake_git_repo)

    resp = flask_client.post(
        f"/api/repos/{name}/topics/proposals/{proposal_id}/topics/{topic_id}/apply",
        json={"strategy": "create"},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)

    repo_id = repo_id_for_path(str(fake_git_repo))
    digests = digests_for_topic(repo_id, topic_id)
    assert digests, "apply did not fingerprint the topic's refs"

    # The captured baseline is real: mutate a ref and it registers as drift.
    ref_path = digests[0]["path"]
    (fake_git_repo / ref_path).write_text("MUTATED AFTER APPLY\n")
    drifted = [d["topic_id"] for d in detect_drifted_topics(fake_git_repo)]
    assert topic_id in drifted
