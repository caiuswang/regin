"""`GET /api/features/<id>/next-step` — the scoped Tasks head (F155 R1).

The head shows a feature's name, stage and next step on every arrival at
`/tasks?feature_id=N`; the full ledger (cost, sessions, tokens) is never shown
there, so this route must answer from the feature row and the next-step rules
alone and agree with what the ledger says.
"""

from __future__ import annotations

from lib.auth import create_token
from lib.features import events

VIEWER = {"Authorization": f"Bearer {create_token(3, 'vw', 'viewer')}"}
SLICED_SPEC = """## Spec

| # | Slice | Delivers | Spec refs | Tests | Schema | Depends on | Base | Skill |
|---|---|---|---|---|---|---|---|---|
| S1 | one | a thing | R1 | test_a | — | — | `master` | goal-verified |
"""


def test_feature_next_step_endpoint(flask_client, feature_row):
    row = feature_row("scoped head")
    flask_client.patch(f"/api/features/{row.id}", json={"body": SLICED_SPEC},
                       headers={"Authorization":
                                f"Bearer {create_token(2, 'ed', 'editor')}"})

    resp = flask_client.get(f"/api/features/{row.id}/next-step",
                            headers=VIEWER)

    assert resp.status_code == 200, resp.get_json()
    head = resp.get_json()
    assert set(head) >= {"id", "title", "stage", "next_step"}
    assert (head["id"], head["title"], head["stage"]) == (
        row.id, "scoped head", events.STAGE_SPEC)
    assert head["next_step"]["kind"] == "slice"
    ledger = flask_client.get(f"/api/features/{row.id}", headers=VIEWER)
    assert head["next_step"] == ledger.get_json()["next_step"]


def test_next_step_endpoint_names_write_spec_for_a_bare_feature(
        flask_client, feature_row):
    row = feature_row("no spec yet")

    head = flask_client.get(f"/api/features/{row.id}/next-step",
                            headers=VIEWER).get_json()

    assert head["task_count"] == 0
    assert head["next_step"] == {
        "kind": "write_spec", "label": "Write the spec",
        "target": f"/features/{row.id}#feature-spec"}


def test_next_step_endpoint_404s_an_unknown_feature(flask_client):
    resp = flask_client.get("/api/features/9999/next-step", headers=VIEWER)

    assert resp.status_code == 404
    assert "9999" in resp.get_json()["error"]
