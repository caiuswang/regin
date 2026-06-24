"""`regin gate <name> --session <SID>` — trace-derived pass/fail gates.

A gate turns an *unenforced* skill step into a checkable invariant: the step's
tool emits spans, and the gate asserts they exist for THIS session. Exit code
0 = pass, 1 = fail, so a skill can wire it as a hard stop instead of inlining
raw SQL:

    regin gate recall-ran --session "$SID" || { echo "walk the tree first"; exit 1; }

The span fingerprints each gate checks live in `lib/trace/span_gates.py`.
"""

from __future__ import annotations

import json as _json

import typer

from lib.trace.span_gates import GATES, span_count


gate_app = typer.Typer(
    name="gate",
    help="Trace-derived pass/fail gates for unenforced skill steps",
    no_args_is_help=True,
)


@gate_app.command(
    "recall-ran",
    help="PASS iff this session emitted memory-tree-nav/recall spans "
         "(goal-verified-treenav step 1b anti-skip).",
)
def cmd_recall_ran(
    session: str = typer.Option(
        ..., "--session", "-s",
        help="Session/trace id to check (the goal-verified-treenav $SID)."),
    json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    _run_gate("recall-ran", session, json)


def _run_gate(key: str, session: str, json: bool) -> None:
    """Shared body: count the gate's spans, report, exit non-zero on fail."""
    from lib.activity_log import get_activity_logger

    gate = GATES[key]
    n = span_count(session, gate)
    passed = n > 0
    get_activity_logger("gate").read(
        "gate_checked", gate=key, session=session, spans=n, passed=passed)

    if json:
        print(_json.dumps(
            {"gate": key, "session": session, "spans": n, "pass": passed}))
    else:
        print(f"{gate.describe} spans this session: {n}")
        print("GATE PASS — arm ran" if passed else
              "GATE FAIL — no spans for this gate; you skipped the step. "
              "Go back and run it.")

    raise typer.Exit(0 if passed else 1)


def register(app: typer.Typer) -> None:
    app.add_typer(gate_app)
