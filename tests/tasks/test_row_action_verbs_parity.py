"""`frontend/tests/helpers/rowAction.js` and `lib/tasks/verbs.py` must state
the same verb table.

The helper hands mocked rows the `action`/`menu` the server would have sent,
so a spec that serves its own rows keeps passing against a table the server
no longer uses. This reads the helper's `PRIMARY` and `MENU` literals and
compares them with the server's, state for state. The armed-and-blocked state
is server-only: it is derived from dependencies, not a status a mocked row
carries.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from lib.tasks import verbs

_HELPER = (Path(__file__).resolve().parents[2]
           / "frontend" / "tests" / "helpers" / "rowAction.js")


def _literal(source: str, name: str):
    """The object literal assigned to `const <name>`, read as JSON."""
    start = source.index(f"const {name} = ") + len(f"const {name} = ")
    depth = 0
    for end, ch in enumerate(source[start:], start):
        depth += ch == "{"
        depth -= ch == "}"
        if depth == 0:
            break
    body = source[start:end + 1].replace("'", '"')
    body = re.sub(r"([{,]\s*)(\w+)\s*:", r'\1"\2":', body)
    body = re.sub(r",\s*([}\]])", r"\1", body)
    return json.loads(body)


def test_primary_matches_the_server():
    helper = _literal(_HELPER.read_text(), "PRIMARY")
    server = {state: verb for state, verb in verbs._PRIMARY.items()
              if verb is not None and state != verbs._BLOCKED}
    assert helper == server


def test_menu_matches_the_server():
    helper = _literal(_HELPER.read_text(), "MENU")
    server = {state: list(items) for state, items in verbs._MENU.items()
              if state != verbs._BLOCKED}
    assert helper == server
