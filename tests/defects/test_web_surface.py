"""The defect tab's control surface — the rules that live in the browser.

Three of this feature's acceptance items are assertions about the *client*: a
row has an address, a round-bumping write is asked for out loud, and a field
the store will refuse is refused where the operator can still fix it. Two of
them cannot be observed from a request at all — a confirm that is never shown
and a post that is never sent both look like silence on the wire — so they are
read off the source, the way `tests/web/test_session_switcher.py` reads the
client's own constants.

The source assertions are deliberately narrow: each one names the ordering
that carries the rule (confirm *before* post, guard *before* post), not the
wording around it, so rephrasing a message does not fail a test about
behaviour.
"""

from __future__ import annotations

import pathlib
import re

from lib.auth import create_token
from lib.defects import store

EDITOR = {"Authorization": f"Bearer {create_token(2, 'ed', 'editor')}"}

#: Anchored on this file so the assertions hold from any working directory.
ROOT = pathlib.Path(__file__).resolve().parents[2]
ROUTER = ROOT / "frontend/src/router.js"
ROW = ROOT / "frontend/src/components/defects/DefectRow.vue"
ACTIONS = ROOT / "frontend/src/components/defects/DefectActions.vue"
FORMS = ROOT / "frontend/src/components/defects/defectForms.js"
FORM = ROOT / "frontend/src/components/defects/DefectActionForm.vue"


def _body(source: str, name: str) -> str:
    """The text of `async function <name>() { … }`, to its closing brace."""
    start = source.index(f"function {name}(")
    depth = 0
    for index in range(source.index("{", start), len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise AssertionError(f"{name}() is not closed")


def test_defect_has_a_permalink(flask_client):
    """A row's id is an address: a route, a read behind it, and a link to it.

    A list is a predicate plus a page position and neither survives being sent
    to somebody else, so a defect that cannot be linked to can only be pointed
    at by describing it.
    """
    row = store.record("the rail clips")

    found = flask_client.get(f"/api/defects/{row.id}", headers=EDITOR)
    assert found.status_code == 200
    assert found.get_json()["defect"]["id"] == row.id
    assert found.get_json()["defect"]["symptom"] == "the rail clips"

    # An id that names no row is a 404, not an empty page that looks like a
    # defect with nothing in it.
    assert flask_client.get("/api/defects/999999", headers=EDITOR).status_code == 404

    assert "'/defects/:id'" in ROUTER.read_text(), \
        "the permalink needs a route, or the link renders a blank pane"
    assert "`/defects/${defect.id}`" in ROW.read_text(), \
        "every row must offer its own address"


def test_reopen_asks_for_confirmation():
    """Reopening is not undo — it bumps a round its feature keeps for good.

    The confirm has to gate the post, not follow it: a dialog shown after the
    write has already landed is a notification, and there is nothing left to
    cancel.
    """
    source = ACTIONS.read_text()
    assert "useConfirm" in source, "the shared confirm path, not a bespoke one"

    body = _body(source, "reopen")
    gate = body.index("confirm(")
    post = body.index("api.post(")
    assert gate < post, "the confirm must gate the reopen, not report it"
    assert re.search(r"if \(!ok\) return", body), \
        "declining the dialog must stop the write"


def test_close_without_missing_check_is_refused_client_side(flask_client):
    """An empty check is refused before the post, not by the 400 after it.

    The store owns the rule and still enforces it — this is the same rule
    stated where the operator can act on it, rather than a second rulebook.
    """
    close = FORMS.read_text().split("  close: {")[1].split("\n  },")[0]
    assert "invalid:" in close, "each form states what it cannot send"

    body = _body(ACTIONS.read_text(), "submit")
    guard = body.index("spec.invalid(raw)")
    post = body.index("spec.send(")
    assert guard < post, "the guard must precede the post it prevents"

    # The submit is unreachable while the field cannot be sent, so an empty
    # close cannot be posted by clicking either.
    assert ':disabled="busy || !!problem"' in FORM.read_text()

    # And the server has not stopped refusing it: the client guard is a
    # courtesy, never the control.
    row = store.record("the gutter folds")
    refused = flask_client.post(
        f"/api/defects/{row.id}/close",
        json={"missing_check": "   "}, headers=EDITOR)
    assert refused.status_code == 400
