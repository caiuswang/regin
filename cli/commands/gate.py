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

from lib.trace.span_gates import GATES, PASS, STATUS_EXIT, span_count, verdict


gate_app = typer.Typer(
    name="gate",
    help="Trace-derived pass/fail gates for unenforced skill steps",
    no_args_is_help=True,
)


def _register(gate) -> None:
    """One CLI command per registered gate.

    Generated rather than hand-written so that `span_gates`\' own instruction —
    "add a gate by appending a `SpanGate` to `GATES`" — is actually true. It
    was not: the counting path picked a new gate up immediately while the CLI
    did not, leaving it unreachable from the shell with a "No such command"
    that looks like a typo rather than a missing registration.
    """

    @gate_app.command(gate.key, help=f"PASS iff this session emitted spans for "
                                     f"{gate.describe}.")
    def _cmd(
        session: str = typer.Option(
            ..., "--session", "-s", help="Session/trace id to check."),
        json: bool = typer.Option(False, "--json",
                                  help="Emit machine-readable JSON."),
        _key: str = gate.key,
    ) -> None:
        _run_gate(_key, session, json)


for _gate in GATES.values():
    _register(_gate)


def _run_gate(key: str, session: str, json: bool) -> None:
    """Shared body: count the gate's spans, report, exit non-zero on fail."""
    from lib.activity_log import get_activity_logger
    from lib.trace.span_gates import INCONCLUSIVE

    from lib.trace.span_gates import unresolved_session_id

    gate = GATES[key]
    unusable = unresolved_session_id(session)
    if unusable:
        # An id that cannot address a trace means no spans can be attributed,
        # so a 0 count says nothing about whether the step ran. Accusing the
        # caller of skipping here is the unfollowable-gate failure that retired
        # `ui-verified`: the only way past it would be to argue around a red
        # gate.
        message = (
            f"GATE INCONCLUSIVE — {unusable}, so this session's spans cannot "
            "be counted. Resolve one with `regin session-id` (export "
            "$REGIN_SESSION_ID from your harness, or try "
            "`regin session-id --from-trace`) and re-run. Do NOT record this "
            "as a pass.")
        if json:
            print(_json.dumps({"gate": key, "session": session, "spans": None,
                               "pass": False, "status": INCONCLUSIVE,
                               "capability_proven": gate.capability_self_evident}))
        else:
            print(message)
        raise typer.Exit(STATUS_EXIT[INCONCLUSIVE])
    n = span_count(session, gate)
    # The CLI can only vouch for capabilities that running regin itself
    # demonstrates; it cannot see which MCP servers the caller's session
    # loaded. Gates whose tool is an MCP server therefore resolve to
    # INCONCLUSIVE here rather than a false accusation of skipping.
    status, message = verdict(gate, n, gate.capability_self_evident)
    passed = status == PASS
    get_activity_logger("gate").read(
        "gate_checked", gate=key, session=session, spans=n,
        passed=passed, status=status)

    if json:
        print(_json.dumps({
            "gate": key, "session": session, "spans": n,
            "pass": passed, "status": status,
            "capability_proven": gate.capability_self_evident,
        }))
    else:
        print(f"{gate.describe} spans this session: {n}")
        print(message)

    raise typer.Exit(STATUS_EXIT[status])


def register(app: typer.Typer) -> None:
    app.add_typer(gate_app)
