"""Verdicts for a topic ref absent from the working tree.

The branch-tip lookup fails open by design (CAI-25): a ref git cannot answer
for must never become a commit-blocking error. What these tests pin is that
"git could not answer" is reported as itself rather than as "the file lives on
another branch" — an assertion that is simply false in a directory with no
branches — and that one ref git refuses as a pathspec no longer drags every
other absent ref in the graph into the same verdict.
"""

from __future__ import annotations

from lib import topics
from lib.topics import branch_refs
from lib.topics.branch_refs import (
    ABSENT_DEAD,
    ABSENT_ELSEWHERE,
    ABSENT_UNPROVABLE,
    classify_absent_paths,
)
from lib.topics.bulk_fix import AUTO_FIXABLE_CODES, compose_fix
from lib.topics.validation import (
    BRANCH_OWNED_REF_CODE,
    UNPROVABLE_REF_CODE,
    ValidationIssue,
    audit_graph,
    count_by_display_severity,
    display_severity,
)

MISSING_TIP_OID = f"{'0' * 39}1"


def _graph_with_refs(*paths):
    return {
        "version": 1, "repo": "demo", "topics": {
            "alpha": {
                "label": "A", "intent": "a", "status": "active",
                "aliases": [],
                "refs": [{"path": p, "role": "implementation"} for p in paths],
                "edges": [], "commands": [], "include_globs": [],
                "exclude_globs": [],
            },
        },
    }


def _break_a_tip(repo):
    """Point a branch at an object that is not in the store — what a pruned
    remote ref or a shallow clone leaves behind. Named to sort first, so the
    sweep hits it before any healthy tip."""
    (repo / ".git" / "refs" / "heads" / "aaa-broken").write_text(
        f"{MISSING_TIP_OID}\n")


def _topic_with_ref(path):
    return {
        "label": "A", "aliases": [], "intent": "A", "status": "active",
        "refs": [{"path": path, "role": "implementation"}],
        "edges": [], "commands": [], "include_globs": [], "exclude_globs": [],
    }


def test_a_repo_git_cannot_answer_for_yields_unprovable_not_elsewhere(tmp_path):
    """`tmp_path` is not a git repo, so nothing can be found on a branch tip —
    reporting the ref as branch-owned told the user about a branch that does
    not exist."""
    assert classify_absent_paths(tmp_path, {"lib/x.py"}) == {
        "lib/x.py": ABSENT_UNPROVABLE,
    }


def test_an_unusable_pathspec_is_the_only_path_it_condemns(
    fake_git_repo, branch_owned_ref,
):
    """git refuses `../outside.py` and fails the batched lookup; the retry
    isolates it, so its two innocent neighbours still get real verdicts instead
    of all three being reported as living on another branch."""
    verdicts = classify_absent_paths(
        fake_git_repo, {"../outside.py", branch_owned_ref, "gone.py"},
    )

    assert verdicts["../outside.py"] == ABSENT_UNPROVABLE
    assert verdicts[branch_owned_ref] == ABSENT_ELSEWHERE
    assert verdicts["gone.py"] == ABSENT_DEAD


def test_a_tip_git_cannot_read_does_not_condemn_the_other_tips(
    fake_git_repo, branch_owned_ref,
):
    """A pruned or missing tip object fails the batch too, but that is the
    tip's fault, not the paths' — the branch that really carries the anchor
    must still get to answer, or a corrupt ref turns every ref in the graph
    unverifiable."""
    _break_a_tip(fake_git_repo)

    verdicts = classify_absent_paths(
        fake_git_repo, {branch_owned_ref, "gone.py"},
    )

    assert verdicts[branch_owned_ref] == ABSENT_ELSEWHERE
    # Not dead: the tip that could not be read might have been the one
    # carrying it, so its silence proves nothing.
    assert verdicts["gone.py"] == ABSENT_UNPROVABLE


def test_a_ref_no_readable_tip_could_answer_for_is_never_called_dead(
    fake_git_repo,
):
    """The uncovered case is a broken tip and *no* healthy one: every git call
    failed, so nothing answered. Defaulting to dead there would both wedge the
    commit (CAI-25) and hand the strip button an anchor nothing verified
    (CAI-30) — the two failures this whole classification exists to prevent."""
    _break_a_tip(fake_git_repo)
    topics.bootstrap(fake_git_repo)
    graph = topics.load_graph(fake_git_repo)
    graph["topics"] = {"a": _topic_with_ref("gone.py")}
    topics.save_graph(fake_git_repo, graph)

    assert classify_absent_paths(fake_git_repo, {"gone.py"}) == {
        "gone.py": ABSENT_UNPROVABLE,
    }

    result = topics.validate(fake_git_repo)
    assert result.ok
    assert not result.errors

    audit = _graph_with_refs("gone.py")
    issues = audit_graph(audit, repo_path=fake_git_repo)
    assert {i.code for i in issues} & {UNPROVABLE_REF_CODE}
    assert compose_fix(audit, issues, codes_to_fix=AUTO_FIXABLE_CODES) == []


def test_a_failed_batch_does_not_cost_a_subprocess_per_absent_ref(
    fake_git_repo, branch_owned_ref, monkeypatch,
):
    """The whole point of the batched lookup is that N absent refs cost what
    one does. A batch that fails must not undo that by probing every path — on
    a graph big enough to overflow argv, that is thousands of git calls per
    branch tip."""
    calls: list[int] = []
    real = branch_refs._ls_tree

    def counted(repo, oid, paths):
        calls.append(len(paths))
        return real(repo, oid, paths)

    monkeypatch.setattr(branch_refs, "_ls_tree", counted)
    absent = {"../outside.py", branch_owned_ref} | {
        f"gone{i}.py" for i in range(branch_refs._MAX_PROBED_PATHS + 1)
    }

    verdicts = classify_absent_paths(fake_git_repo, absent)

    assert verdicts[branch_owned_ref] == ABSENT_ELSEWHERE
    # Per tip: the failed batch, then one pathspec-free listing that answers
    # it. The per-path probe is off above the cap — which is what the batch
    # costs, and also what it gives up: with nothing probed, the ref git
    # refuses is indistinguishable from the ones simply not there. It can only
    # cost accuracy once ≥`_MAX_PROBED_PATHS` refs are dead anyway, and each of
    # those is already an error on its own.
    assert len(calls) <= 4
    assert verdicts["../outside.py"] == ABSENT_DEAD


def test_validate_warns_honestly_when_the_branch_check_could_not_run(tmp_path):
    """Still a warning, never an error — the CAI-25 guarantee — but worded for
    what actually happened."""
    topics.bootstrap(tmp_path)
    graph = topics.load_graph(tmp_path)
    graph["topics"] = {"a": _topic_with_ref("lib/x.py")}
    topics.save_graph(tmp_path, graph)

    result = topics.validate(tmp_path)

    assert result.ok
    assert any("could not be verified against branch tips" in w
               for w in result.warnings)
    assert not any("present on another branch" in w for w in result.warnings)


def test_audit_codes_an_unverifiable_ref_apart_from_a_branch_owned_one(
    tmp_path,
):
    issues = audit_graph(_graph_with_refs("lib/x.py"), repo_path=tmp_path)
    by_code = {i.code: i for i in issues}

    assert UNPROVABLE_REF_CODE in by_code
    assert BRANCH_OWNED_REF_CODE not in by_code
    assert "graph.dead_ref" not in by_code
    assert "could not be verified against branch tips" \
        in by_code[UNPROVABLE_REF_CODE].message


def test_an_unverifiable_ref_is_never_auto_fixable(tmp_path):
    """The strip button must refuse it for the same reason it refuses a
    branch-owned anchor: nothing proved the file is gone, and the UI cannot put
    the ref back (CAI-30)."""
    graph = _graph_with_refs("lib/x.py")
    issues = audit_graph(graph, repo_path=tmp_path)

    assert UNPROVABLE_REF_CODE not in AUTO_FIXABLE_CODES
    assert compose_fix(graph, issues, codes_to_fix=AUTO_FIXABLE_CODES) == []
    assert compose_fix(graph, issues, codes_to_fix={UNPROVABLE_REF_CODE}) == []


def test_an_undeletable_ref_keeps_error_severity_but_displays_as_info(tmp_path):
    """The authoring gate is unchanged (CAI-30); only the readout softens
    (CAI-35). A panel that colours these red shows an error group its own tag
    calls unfixable."""
    issues = audit_graph(_graph_with_refs("lib/x.py"), repo_path=tmp_path)
    undeletable = [i for i in issues if i.code == UNPROVABLE_REF_CODE]

    assert undeletable
    assert all(i.severity == "error" for i in undeletable)
    assert all(display_severity(i) == "info" for i in undeletable)


def test_display_counts_move_undeletable_refs_out_of_the_error_tally():
    issues = [
        ValidationIssue(severity="error", code="graph.dead_ref",
                        message="", topic_ids=("a",), paths=("gone.py",)),
        ValidationIssue(severity="error", code=BRANCH_OWNED_REF_CODE,
                        message="", topic_ids=("a",), paths=("b.py",)),
        ValidationIssue(severity="error", code=UNPROVABLE_REF_CODE,
                        message="", topic_ids=("a",), paths=("c.py",)),
        ValidationIssue(severity="warning", code="graph.shared_primary_ref",
                        message="", topic_ids=("a", "b"), paths=("d.py",)),
    ]

    assert count_by_display_severity(issues) == {
        "error": 1, "warning": 1, "info": 2,
    }


def test_an_unknown_severity_is_counted_not_dropped():
    """Dropping it would total zero over a non-empty issue list — a readout
    saying the graph is clean above the groups it is rendering."""
    issues = [ValidationIssue(severity="critical", code="graph.whatever",
                              message="", topic_ids=("a",))]

    assert count_by_display_severity(issues) == {
        "error": 1, "warning": 0, "info": 0,
    }


def test_the_display_bucket_is_not_the_bulk_fix_waiver_set():
    """Two policies, two sets: a code added to `UNDELETABLE_REF_CODES` because
    it needs a human decision must not silently leave the error tally."""
    from lib.topics.validation import (
        INFORMATIONAL_DISPLAY_CODES,
        UNDELETABLE_REF_CODES,
    )

    assert INFORMATIONAL_DISPLAY_CODES is not UNDELETABLE_REF_CODES
