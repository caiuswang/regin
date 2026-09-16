"""The spans the pipeline leaves, and its gate.

A skill step nobody can verify is a step that quietly stops happening. These
spans are what make a skip visible in the trace rather than silently absent.
"""

from __future__ import annotations

from typer.testing import CliRunner

from cli.app import app
from cli.commands.defects import defects_app
from lib.trace import span_gates

runner = CliRunner()
TRACE = "trace-gate"


def _spans_posted(monkeypatch) -> list:
    posted = []

    def fake(trace_id, name, attributes=None, **kw):
        posted.append({"trace_id": trace_id, "name": name,
                       "attributes": attributes or {}})
        return True

    import lib.hook_plugin as hp
    monkeypatch.setattr(hp, "post_span", fake)
    return posted


def test_defects_open_emits_the_gate_span(monkeypatch, feature_row):
    feature = feature_row()
    posted = _spans_posted(monkeypatch)

    runner.invoke(defects_app, ["open", "it broke",
                                "--feature", str(feature.id),
                                "--session", TRACE])

    assert [p["name"] for p in posted] == ["defect.declared"]
    assert posted[0]["trace_id"] == TRACE


def test_feature_stage_emits_the_gate_span(monkeypatch, feature_row):
    feature = feature_row()
    posted = _spans_posted(monkeypatch)

    # Through the root app, which is the path a user takes: Typer collapses a
    # single-command sub-app when it is invoked standalone, so testing the
    # sub-app directly would exercise a parse that never happens in practice.
    result = runner.invoke(app, ["feature", "stage", str(feature.id),
                                 "--session", TRACE])

    assert result.exit_code == 0, result.output
    assert "feature.stage" in [p["name"] for p in posted]


def test_the_pipeline_gate_is_registered_and_matches_both_spans():
    gate = span_gates.GATES["pipeline-ran"]

    assert "feature.stage" in gate.exact
    assert "defect.declared" in gate.exact
    # Both spans come from the regin CLI, so 0 is a real skip, never a
    # missing instrument — the distinction that retired `ui-verified`.
    assert gate.capability_self_evident is True


def test_the_pipeline_gate_fails_cleanly_with_no_spans():
    gate = span_gates.GATES["pipeline-ran"]

    status, _ = span_gates.verdict(gate, 0, capability_proven=True)

    assert status == span_gates.FAIL


def test_every_registered_gate_has_a_cli_command():
    """`lib/trace/span_gates.py` promises "add a gate by appending a SpanGate
    to GATES". That was only true of the counting path — the CLI hardcoded one
    command per gate, so a new gate was silently unreachable from the shell."""
    from cli.commands.gate import gate_app  # noqa: PLC0415

    registered = {c.name for c in gate_app.registered_commands}

    assert set(span_gates.GATES) <= registered
