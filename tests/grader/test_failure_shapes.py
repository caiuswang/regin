"""Both failure capture shapes must drive the failure-polarity logic.

Hook-captured sessions (the primary data source) record a failed call as
a `tool.failure` span with the real tool in attrs; workflow-ingested
sessions record `tool.<Name>` with status ERROR. The review found the
hook shape was invisible to the grader — these tests pin the fix.
"""

from __future__ import annotations

from conftest import bash_span, failure_span, prompt_span, response_span

from lib.grader.correctness import grade_correctness
from lib.grader.models import CONTRADICTED, GROUNDED
from lib.grader.process import grade_process


def test_hook_failure_spans_land_in_bash_evidence(make_evidence):
    evidence = make_evidence([
        prompt_span("fix the bug"),
        failure_span("f1", "Bash", command="pytest -x"),
    ])
    assert len(evidence.bash) == 1
    assert evidence.bash[0].is_error
    assert evidence.bash[0].command == "pytest -x"
    assert "Exit code 1" in evidence.bash[0].stderr


def test_positive_claim_contradicted_by_hook_failure(make_evidence):
    evidence = make_evidence([
        prompt_span("fix the bug"),
        failure_span("f1", "Bash", command="pytest -x"),
        response_span("All tests pass."),
    ])
    grade = grade_correctness(evidence)
    verdicts = grade.detail["verdicts"]
    contradicted = [v for v in verdicts.values()
                    if v["verdict"] == CONTRADICTED]
    assert contradicted, grade.report
    assert grade.verdict == "fail"   # load-bearing CONTRADICTED gates


def test_negative_claim_grounded_by_hook_failure(make_evidence):
    evidence = make_evidence([
        prompt_span("reproduce the bug"),
        failure_span("f1", "Bash", command="pytest -x tests/test_login.py"),
        response_span("The test fails, reproducing the reported bug."),
    ])
    grade = grade_correctness(evidence)
    verdicts = grade.detail["verdicts"]
    assert any(v["verdict"] == GROUNDED and v["source_kind"] == "bash"
               for v in verdicts.values()), grade.report


def test_thrash_detected_on_hook_failure_shape(make_evidence):
    spans = [prompt_span("fix it")]
    spans += [failure_span(f"f{i}", "Bash", command="pytest -x")
              for i in range(3)]
    spans.append(response_span("Still working on it."))
    evidence = make_evidence(spans)
    grade = grade_process(evidence)
    assert grade.scoreboard["redundancy"]["thrash_episodes"] == 1


def test_workflow_error_shape_still_works(make_evidence):
    evidence = make_evidence([
        prompt_span("fix the bug"),
        bash_span("b1", "pytest -x", status="ERROR", stderr="Exit code 1"),
        response_span("All tests pass."),
    ])
    grade = grade_correctness(evidence)
    verdicts = grade.detail["verdicts"]
    assert any(v["verdict"] == CONTRADICTED for v in verdicts.values())
