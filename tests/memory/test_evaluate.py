"""Tests for the recall-quality eval harness (`lib.memory.evaluate`).

Seeds a temp store (the autouse `tmp_memory_db` fixture isolates the DB)
with a handful of memories, then asserts: cases that should pass at k=3,
a case that should miss, exact metric math (hit@1 / MRR), and the
`regin memory eval` CLI surface.

`mode='fts'` everywhere — lexical-only recall is deterministic and never
loads the SkillRouter models (the dense leg is pinned off by the fixture
anyway). `reinforce=False` so scoring a case doesn't perturb a later one.
"""

from __future__ import annotations

import json

import pytest

import lib.memory as memory
from lib.memory.evaluate import (
    EvalCase, EvalReport, evaluate_recall, load_cases,
)


def _seed(body, **kw):
    kw.setdefault("is_test", True)
    kw.setdefault("title", body[:80])  # lessons now require a (unique) title
    return memory.remember(body, **kw)


@pytest.fixture
def seeded_store():
    """5 distinctly-worded memories so FTS recall is unambiguous."""
    store = memory.get_store()
    _seed("Vue scoped styles do not reach child components; shared "
          "classes must be declared global.",
          kind="lesson", title="Vue scoped styles gotcha")
    _seed("radon counts each assert statement toward cyclomatic "
          "complexity; collapse repeated asserts into a loop.",
          kind="lesson", title="radon asserts toward CC")
    _seed("mnemopi is the ports-and-adapters design reference for the "
          "agent memory engine.",
          kind="fact", title="mnemopi design reference")
    _seed("The serve-time trace path calls merge_spans, not "
          "_graft_orphans, when reproducing the UI render.",
          kind="fact", title="serve-time trace path")
    _seed("A pending span must carry flat attribute keys like "
          "command_preview, not a nested tool_input dump.",
          kind="gotcha", title="pending span flat attrs")
    return store


# ── case running ──────────────────────────────────────────────

def test_cases_pass_at_k(seeded_store):
    cases = [
        EvalCase("Vue child component renders unstyled, what's wrong?",
                 ["scoped styles", "shared classes must be declared global"]),
        EvalCase("asserts are tripping the complexity gate in my test",
                 ["radon counts", "collapse repeated asserts"]),
        EvalCase("what design reference does the memory engine borrow from?",
                 ["mnemopi", "ports-and-adapters"]),
    ]
    report = evaluate_recall(cases, store=seeded_store, top_k=3, mode="fts")
    assert report.passed == 3
    assert report.hit_at_k == 1.0
    assert all(v.hit_rank is not None for v in report.verdicts)


def test_missing_case_fails(seeded_store):
    case = EvalCase("how do I configure kubernetes ingress for the cluster?",
                    ["istio sidecar", "helm chart values"])
    report = evaluate_recall([case], store=seeded_store, top_k=3, mode="fts")
    assert report.passed == 0
    assert report.hit_at_k == 0.0
    assert report.verdicts[0].hit_rank is None
    assert report.verdicts[0].matched_title is None


# ── metric math ───────────────────────────────────────────────

def test_metric_math_exact():
    """Hand-built verdicts so the aggregate formulas are pinned exactly,
    independent of any recall behavior."""
    from lib.memory.evaluate import CaseVerdict

    def _v(rank):
        return CaseVerdict(query="q", passed=rank is not None, hit_rank=rank,
                           top_id="x", top_title="t", matched_title="t")

    report = EvalReport(verdicts=[_v(1), _v(2), _v(None)], top_k=5)
    assert report.total == 3
    assert report.passed == 2
    # hit@1: one case ranked first of three.
    assert report.hit_at_1 == pytest.approx(1 / 3)
    # hit@k: two of three matched somewhere in window.
    assert report.hit_at_k == pytest.approx(2 / 3)
    # MRR: (1/1 + 1/2 + 0) / 3.
    assert report.mrr == pytest.approx((1.0 + 0.5 + 0.0) / 3)


def test_empty_report_metrics_are_zero():
    report = EvalReport(verdicts=[], top_k=5)
    assert report.hit_at_1 == 0.0
    assert report.hit_at_k == 0.0
    assert report.mrr == 0.0


# ── loading + serialization ───────────────────────────────────

def test_load_cases_round_trip(tmp_path):
    path = tmp_path / "cases.jsonl"
    path.write_text(
        json.dumps({"query": "q1", "expect_any": ["a", "b"]}) + "\n"
        "\n"  # blank line is skipped
        + json.dumps({"query": "q2", "expect_any": ["c"],
                      "note": "n"}) + "\n")
    cases = load_cases(path)
    assert [c.query for c in cases] == ["q1", "q2"]
    assert cases[1].note == "n"


def test_bad_case_rejected():
    with pytest.raises(ValueError):
        EvalCase.from_dict({"query": "", "expect_any": ["x"]})
    with pytest.raises(ValueError):
        EvalCase.from_dict({"query": "q", "expect_any": []})


def test_report_to_dict(seeded_store):
    case = EvalCase("mnemopi design reference", ["mnemopi"])
    report = evaluate_recall([case], store=seeded_store, top_k=3, mode="fts")
    d = report.to_dict()
    assert d["total"] == 1 and d["passed"] == 1
    assert d["hit_at_k"] == 1.0
    assert d["cases"][0]["query"] == "mnemopi design reference"


# ── CLI ───────────────────────────────────────────────────────

def test_cli_eval_passes(seeded_store, tmp_path):
    from typer.testing import CliRunner
    from cli.commands.memory import memory_app

    path = tmp_path / "cases.jsonl"
    path.write_text(
        json.dumps({"query": "mnemopi design reference",
                    "expect_any": ["mnemopi"]}) + "\n")
    result = CliRunner().invoke(
        memory_app, ["eval", str(path), "--mode", "fts"])
    assert result.exit_code == 0
    assert "hit@1=1.000" in result.stdout


def test_cli_eval_gates_on_miss(seeded_store, tmp_path):
    from typer.testing import CliRunner
    from cli.commands.memory import memory_app

    path = tmp_path / "cases.jsonl"
    path.write_text(
        json.dumps({"query": "kubernetes ingress helm chart",
                    "expect_any": ["istio sidecar"]}) + "\n")
    result = CliRunner().invoke(
        memory_app, ["eval", str(path), "--mode", "fts"])
    assert result.exit_code == 1
