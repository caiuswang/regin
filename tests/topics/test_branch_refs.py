"""Verdicts for a topic ref absent from the working tree.

The branch-tip lookup fails open by design (CAI-25): a ref git cannot answer
for must never become a commit-blocking error. What these tests pin is that
"git could not answer" is reported as itself rather than as "the file lives on
another branch" — an assertion that is simply false in a directory with no
branches — and that one ref git refuses as a pathspec no longer drags every
other absent ref in the graph into the same verdict.
"""

from __future__ import annotations

import subprocess
import threading

import pytest

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


@pytest.fixture(autouse=True)
def _cold_tip_cache():
    """The tip scan memoises across calls (CAI-36), and every test here asserts
    on what git was asked — so each starts from a cold cache."""
    branch_refs._TIP_SCAN_CACHE.clear()
    branch_refs._LANDED_CACHE.clear()
    yield
    branch_refs._TIP_SCAN_CACHE.clear()
    branch_refs._LANDED_CACHE.clear()


def _commit_on_branch(repo, branch, files):
    """Commit `files` (path -> contents) on a new branch, then come back."""
    here = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    subprocess.check_call(["git", "-C", str(repo), "checkout", "-q", "-b", branch])
    for path, body in files.items():
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body)
        subprocess.check_call(["git", "-C", str(repo), "add", "-f", path])
    subprocess.check_call(
        ["git", "-C", str(repo), "commit", "-q", "-m", f"add on {branch}"])
    subprocess.check_call(["git", "-C", str(repo), "checkout", "-q", here])


def _merge_branch(repo, branch, strategy=None):
    """Merge `branch` into the checked-out one and *leave the branch in place*
    — the un-pruned merged tip CAI-37 is about. `--no-ff` so the tip is a
    distinct oid from HEAD's, which is what makes it a merged-branch test
    rather than a re-run of the "a tip that is HEAD" filter."""
    subprocess.check_call([
        "git", "-C", str(repo), "merge", "-q", "--no-ff",
        *(("-s", strategy) if strategy else ()),
        "-m", f"merge {branch}", branch,
    ])


def _commit_all(repo, message):
    subprocess.check_call(
        ["git", "-C", str(repo), "commit", "-q", "-a", "-m", message])


def _rev_parse(repo, spec):
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", spec],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def _delete_object(repo, oid):
    """Drop one object from the store, leaving the tree entry pointing at it —
    what an interrupted repack or a broken-link corruption leaves behind."""
    loose = repo / ".git" / "objects" / oid[:2] / oid[2:]
    loose.chmod(0o644)
    loose.unlink()


def _count_calls(monkeypatch, name):
    """Replace `branch_refs.<name>` with a counting passthrough."""
    calls: list[tuple] = []
    real = getattr(branch_refs, name)

    def counted(*args, **kwargs):
        calls.append(args)
        return real(*args, **kwargs)

    monkeypatch.setattr(branch_refs, name, counted)
    return calls


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


def test_the_batched_lookup_and_the_sweep_agree_on_every_verdict(
    fake_git_repo, branch_owned_ref, monkeypatch,
):
    """The batch is an optimisation, not a new policy (CAI-36) — so it has to
    return what the per-tip sweep it replaced would have, on every shape of
    path the classifier distinguishes: a file on another tip, a directory ref
    on another tip, and both of those with nothing carrying them."""
    _commit_on_branch(fake_git_repo, "feat/dir", {"pkg/mod.py": "x\n"})
    absent = {branch_owned_ref, "pkg/", "gone.py", "nowhere/"}

    batched = classify_absent_paths(fake_git_repo, absent)

    branch_refs._TIP_SCAN_CACHE.clear()
    monkeypatch.setattr(branch_refs, "_batched_tip_lookup", lambda *a: None)
    swept = classify_absent_paths(fake_git_repo, absent)

    assert batched == swept
    assert batched[branch_owned_ref] == ABSENT_ELSEWHERE
    assert batched["pkg/"] == ABSENT_ELSEWHERE
    assert batched["gone.py"] == ABSENT_DEAD
    assert batched["nowhere/"] == ABSENT_DEAD


def test_proving_a_ref_dead_costs_one_git_call_not_one_per_branch_tip(
    fake_git_repo, branch_owned_ref, monkeypatch,
):
    """The sweep stops early once every path is accounted for, so a path *no*
    tip carries is the case that defeats it — one `ls-tree` per tip, ~1s on a
    repo with 120 of them, which is exactly the state the audit panel exists to
    resolve (CAI-36)."""
    for i in range(6):
        _commit_on_branch(fake_git_repo, f"feat/b{i}", {f"b{i}.py": "x\n"})
    ls_tree = _count_calls(monkeypatch, "_ls_tree")
    batches = _count_calls(monkeypatch, "_cat_file_batch")

    verdicts = classify_absent_paths(
        fake_git_repo, {branch_owned_ref, "gone.py"})

    assert verdicts["gone.py"] == ABSENT_DEAD
    assert verdicts[branch_owned_ref] == ABSENT_ELSEWHERE
    assert len(batches) == 1
    assert ls_tree == []


def test_an_unreadable_tip_withholds_dead_in_the_batched_path_too(
    fake_git_repo, branch_owned_ref, monkeypatch,
):
    """A batch cannot tell "this tip lacks the path" from "this tip's object is
    missing" — both read as `missing` — so it asks each tip for its own tree as
    well. Drop that and a corrupt ref silently downgrades every unaccounted ref
    to `dead`, which *is* auto-fixable and *is* stripped."""
    _break_a_tip(fake_git_repo)
    ls_tree = _count_calls(monkeypatch, "_ls_tree")

    verdicts = classify_absent_paths(
        fake_git_repo, {branch_owned_ref, "gone.py"})

    assert ls_tree == []
    assert verdicts[branch_owned_ref] == ABSENT_ELSEWHERE
    assert verdicts["gone.py"] == ABSENT_UNPROVABLE


def test_a_second_audit_answers_from_the_first_without_reading_a_tree(
    fake_git_repo, branch_owned_ref, monkeypatch,
):
    """`diff._classify_issues` audits the current and prospective graphs back
    to back, on the same repo and largely the same absent set — the second pass
    used to re-run the identical scan (CAI-36)."""
    absent = {branch_owned_ref, "gone.py"}
    classify_absent_paths(fake_git_repo, absent)

    ls_tree = _count_calls(monkeypatch, "_ls_tree")
    batches = _count_calls(monkeypatch, "_cat_file_batch")
    again = classify_absent_paths(fake_git_repo, absent)

    assert again[branch_owned_ref] == ABSENT_ELSEWHERE
    assert again["gone.py"] == ABSENT_DEAD
    assert batches == []
    assert ls_tree == []


def test_a_second_audit_does_not_repeat_the_reachability_walk(
    fake_git_repo, branch_owned_ref, monkeypatch,
):
    """Retiring landed tips costs a `--merged` listing, and that one is a
    reachability walk rather than a listing — ~12 ms against regin's 121 tips.
    Run on every call it becomes the entire cost of the second audit of a
    request, which is the cost CAI-36 exists to have removed."""
    absent = {branch_owned_ref, "gone.py"}
    classify_absent_paths(fake_git_repo, absent)

    calls = _count_calls(monkeypatch, "_git_out")
    classify_absent_paths(fake_git_repo, absent)

    assert not [call for call in calls if "--merged" in call]


def test_the_memo_is_keyed_on_the_tips_so_a_moved_ref_re_answers(
    fake_git_repo,
):
    """Keyed by the resolved oids rather than a timestamp: the whole point of a
    ref moving is that the answer changed, and serving the old one would report
    an anchor dead after the branch that carries it was pushed."""
    assert classify_absent_paths(fake_git_repo, {"later.py"}) == {
        "later.py": ABSENT_DEAD,
    }

    _commit_on_branch(fake_git_repo, "feat/later", {"later.py": "x\n"})

    assert classify_absent_paths(fake_git_repo, {"later.py"}) == {
        "later.py": ABSENT_ELSEWHERE,
    }


def test_a_ref_deleted_after_its_branch_merged_is_no_longer_branch_owned(
    fake_git_repo,
):
    """The state CAI-37 is about: `feat/landed` merged, nobody pruned the
    branch, and the file it brought was deleted afterwards. Its tip still
    carries the path, so a scan over every tip called the anchor branch-owned —
    "nothing to fix, clears when it merges" — about a file that had merged and
    then died, and no remediation would strip it. A tip that has *not* landed
    still vouches, which is the protection this must not trade away."""
    _commit_on_branch(fake_git_repo, "feat/landed", {"landed.py": "x\n"})
    _commit_on_branch(fake_git_repo, "feat/open", {"open.py": "x\n"})
    _merge_branch(fake_git_repo, "feat/landed")
    (fake_git_repo / "landed.py").unlink()
    _commit_all(fake_git_repo, "delete the merged file")

    assert classify_absent_paths(fake_git_repo, {"landed.py", "open.py"}) == {
        "landed.py": ABSENT_DEAD,
        "open.py": ABSENT_ELSEWHERE,
    }


def test_a_path_head_still_tracks_is_alive_however_it_left_the_worktree(
    fake_git_repo,
):
    """A sparse checkout, a `skip-worktree` bit or an unstaged `rm` hides a
    path this very commit still tracks, and the caller cannot tell — all it did
    was look on disk. Retiring merged tips takes away the sibling that used to
    cover this by accident, so HEAD's own tree has to answer for it: the ref is
    alive, and `dead` here is a commit-blocking error whose one-click fix
    deletes a perfectly good anchor."""
    _commit_on_branch(fake_git_repo, "feat/landed", {"tracked.py": "x\n"})
    _merge_branch(fake_git_repo, "feat/landed")
    (fake_git_repo / "tracked.py").unlink()

    assert classify_absent_paths(fake_git_repo, {"tracked.py"}) == {
        "tracked.py": ABSENT_ELSEWHERE,
    }


def test_head_answers_for_its_own_tree_with_no_other_branch_in_the_repo(
    fake_git_repo,
):
    """The same case with nothing else in the repo to cover it — one branch,
    the file tracked at HEAD and hidden by `skip-worktree`. Nothing but HEAD's
    own tree can say the anchor is alive here, so this is what fails if HEAD
    ever stops being asked."""
    (fake_git_repo / "hidden.py").write_text("x\n")
    subprocess.check_call(
        ["git", "-C", str(fake_git_repo), "add", "hidden.py"])
    _commit_all(fake_git_repo, "track a file")
    subprocess.check_call(
        ["git", "-C", str(fake_git_repo), "update-index",
         "--skip-worktree", "hidden.py"])
    (fake_git_repo / "hidden.py").unlink()

    assert classify_absent_paths(fake_git_repo, {"hidden.py", "gone.py"}) == {
        "hidden.py": ABSENT_ELSEWHERE,
        "gone.py": ABSENT_DEAD,
    }


def test_the_warning_does_not_claim_another_branch_when_head_is_the_carrier(
    fake_git_repo,
):
    """Claiming "it is present on another branch" in a repo whose only ref is
    the one checked out is the CAI-32 lie again, in the shape the HEAD carrier
    creates — the scan warning is what the pre-commit hook prints, so it is the
    wording most users see."""
    (fake_git_repo / "hidden.py").write_text("x\n")
    subprocess.check_call(
        ["git", "-C", str(fake_git_repo), "add", "hidden.py"])
    _commit_all(fake_git_repo, "track a file")
    subprocess.check_call(
        ["git", "-C", str(fake_git_repo), "update-index",
         "--skip-worktree", "hidden.py"])
    (fake_git_repo / "hidden.py").unlink()

    topics.bootstrap(fake_git_repo)
    graph = topics.load_graph(fake_git_repo)
    graph["topics"] = {"a": _topic_with_ref("hidden.py")}
    topics.save_graph(fake_git_repo, graph)

    result = topics.validate(fake_git_repo)

    assert result.ok
    assert any("tracked at HEAD but not checked out" in w
               for w in result.warnings), result.warnings
    assert not any("present on another branch" in w for w in result.warnings)


def test_a_spelling_git_refuses_stays_unprovable_with_every_branch_merged(
    fake_git_repo,
):
    """Only the sweep can tell a spelling `ls-tree` refuses from a path no tree
    carries — `cat-file` answers a flat `missing` for both — and the sweep only
    runs while there is a tree left to ask. Retiring merged tips can empty that
    set on a repo sitting on mainline, which is why HEAD is always in it:
    `apply._filter_dead_refs` classifies without a canonicality guard of its
    own, so an empty scan would hand it `dead` for `../outside.py`."""
    _commit_on_branch(fake_git_repo, "feat/landed", {"landed.py": "x\n"})
    _merge_branch(fake_git_repo, "feat/landed")
    (fake_git_repo / "landed.py").unlink()
    _commit_all(fake_git_repo, "delete the merged file")

    assert classify_absent_paths(
        fake_git_repo, {"../outside.py", "/etc/passwd", "landed.py"},
    ) == {
        "../outside.py": ABSENT_UNPROVABLE,
        "/etc/passwd": ABSENT_UNPROVABLE,
        "landed.py": ABSENT_DEAD,
    }


def test_merging_the_branch_that_carried_a_ref_re_answers_the_memo(
    fake_git_repo,
):
    """The verdict a merge invalidates must not be served from the tip memo.
    It is not, because merging moves HEAD and retires the tip from the scanned
    set — both halves of the key — so the second call is a different question,
    not a cache hit."""
    _commit_on_branch(fake_git_repo, "feat/landed", {"landed.py": "x\n"})
    assert classify_absent_paths(fake_git_repo, {"landed.py"}) == {
        "landed.py": ABSENT_ELSEWHERE,
    }

    _merge_branch(fake_git_repo, "feat/landed")
    (fake_git_repo / "landed.py").unlink()
    _commit_all(fake_git_repo, "delete the merged file")

    assert classify_absent_paths(fake_git_repo, {"landed.py"}) == {
        "landed.py": ABSENT_DEAD,
    }


def test_a_tip_git_cannot_read_is_not_mistaken_for_one_that_landed(
    fake_git_repo, branch_owned_ref,
):
    """Retiring merged tips must not retire unreadable ones with them. A ref
    pointing at a missing object is listed by neither `--merged` nor
    `--no-merged`, so asking git for the unmerged set directly would drop it
    silently — and a tip that is never scanned cannot report itself unreadable,
    which turns every unaccounted path into `dead`, the one verdict that
    deletes. Subtracting the merged set instead leaves it in the scan."""
    _break_a_tip(fake_git_repo)
    _commit_on_branch(fake_git_repo, "feat/landed", {"landed.py": "x\n"})
    _merge_branch(fake_git_repo, "feat/landed")

    verdicts = classify_absent_paths(
        fake_git_repo, {branch_owned_ref, "gone.py"})

    assert verdicts[branch_owned_ref] == ABSENT_ELSEWHERE
    assert verdicts["gone.py"] == ABSENT_UNPROVABLE


def test_a_merge_that_took_none_of_the_branch_s_files_still_ends_the_vouching(
    fake_git_repo,
):
    """The tradeoff this fix accepts: `-s ours` records the merge without
    taking the file, so the tip carries a path HEAD never had. `dead` is the
    intended reading — the branch landed, and this line of development
    deliberately does not carry the file, so waiting for a merge that already
    happened would leave the anchor stuck forever."""
    _commit_on_branch(fake_git_repo, "feat/ours", {"ours.py": "x\n"})
    _merge_branch(fake_git_repo, "feat/ours", strategy="ours")

    assert classify_absent_paths(fake_git_repo, {"ours.py"}) == {
        "ours.py": ABSENT_DEAD,
    }


def test_a_degraded_sweep_verdict_is_never_memoised(
    fake_git_repo, branch_owned_ref,
):
    """Only the batch answers each path independently of the others. The
    sweep's verdict for a spelling git refuses depends on how many paths shared
    the batch (`_MAX_PROBED_PATHS` turns the probe off above the cap), so
    caching it would serve a later, smaller ask a verdict nobody formed about
    that path — and `dead` is the deletable direction."""
    crowded = {"../outside.py", branch_owned_ref} | {
        f"gone{i}.py" for i in range(branch_refs._MAX_PROBED_PATHS + 1)
    }
    assert classify_absent_paths(fake_git_repo, crowded)["../outside.py"] \
        == ABSENT_DEAD

    assert classify_absent_paths(fake_git_repo, {"../outside.py"}) == {
        "../outside.py": ABSENT_UNPROVABLE,
    }


def test_an_unreadable_subtree_under_a_readable_tip_still_withholds_dead(
    fake_git_repo,
):
    """The batch reports `missing` with exit 0 and an empty stderr both when a
    tip does not carry the path and when an object *on the way to it* cannot be
    read — so a tip whose root tree resolves can still be lying. Reading only
    the tip's own tree missed this, and `dead` is the verdict that deletes: the
    strip button took the anchor off a graph whose file was merely unreachable.
    """
    _commit_on_branch(fake_git_repo, "feat/secret", {"secret/target.py": "x\n"})
    _delete_object(fake_git_repo, _rev_parse(fake_git_repo, "feat/secret:secret"))

    assert classify_absent_paths(fake_git_repo, {"secret/target.py"}) == {
        "secret/target.py": ABSENT_UNPROVABLE,
    }

    graph = _graph_with_refs("secret/target.py")
    issues = audit_graph(graph, repo_path=fake_git_repo)
    codes = {i.code for i in issues}
    assert UNPROVABLE_REF_CODE in codes
    assert "graph.dead_ref" not in codes
    assert compose_fix(graph, issues, codes_to_fix=AUTO_FIXABLE_CODES) == []


def test_an_unreadable_blob_does_not_turn_a_live_anchor_dead(fake_git_repo):
    """`cat-file` has to read the blob to answer, and says `missing` when it
    cannot; `ls-tree` never reads it and lists the path regardless. Corroborate
    only the trees and this half of the hole stays open — reporting an anchor
    that a branch genuinely carries as dead, and deletable."""
    _commit_on_branch(fake_git_repo, "feat/blob", {"live.py": "unique\n"})
    _delete_object(fake_git_repo, _rev_parse(fake_git_repo, "feat/blob:live.py"))

    assert classify_absent_paths(fake_git_repo, {"live.py"}) == {
        "live.py": ABSENT_UNPROVABLE,
    }


def test_a_non_canonical_ref_is_left_to_the_sweep_not_called_dead(
    fake_git_repo, branch_owned_ref,
):
    """`<tip>:/etc/passwd` and `<tip>:..` come back a flat `missing` — which
    reads as dead — where `ls-tree` refuses them and the sweep records
    `unprovable`. `audit_graph` rejects these spellings earlier, but
    `apply._filter_dead_refs` classifies without that guard."""
    for path in ("/etc/passwd", ".."):
        assert classify_absent_paths(fake_git_repo, {path}) == {
            path: ABSENT_UNPROVABLE,
        }, path

    mixed = classify_absent_paths(
        fake_git_repo, {"/etc/passwd", branch_owned_ref, "gone.py"})
    assert mixed["/etc/passwd"] == ABSENT_UNPROVABLE
    assert mixed[branch_owned_ref] == ABSENT_ELSEWHERE
    assert mixed["gone.py"] == ABSENT_DEAD


def test_a_ref_spelled_without_a_slash_does_not_start_matching_directories(
    fake_git_repo, monkeypatch,
):
    """The sweep's `ls-tree -r` only lists files, so a ref with no trailing `/`
    never matched a directory. Resolving `<tip>:<path>` answers differently
    unless the kind is checked — and CAI-36 is an optimisation, so the two
    strategies have to keep agreeing."""
    _commit_on_branch(fake_git_repo, "feat/pkg", {"pkg/mod.py": "x\n"})

    batched = classify_absent_paths(fake_git_repo, {"pkg", "pkg/"})

    branch_refs._TIP_SCAN_CACHE.clear()
    monkeypatch.setattr(branch_refs, "_batched_tip_lookup", lambda *a: None)
    swept = classify_absent_paths(fake_git_repo, {"pkg", "pkg/"})

    assert batched == swept
    assert batched["pkg/"] == ABSENT_ELSEWHERE
    assert batched["pkg"] == ABSENT_DEAD


def test_a_submodule_ref_reads_as_present_and_keeps_the_batch(
    fake_git_repo, monkeypatch,
):
    """git answers a gitlink `<oid> submodule` — two fields, no size. Treat
    that as unreadable and every graph carrying a submodule ref silently loses
    the batch and pays the per-tip sweep again."""
    # An oid that is NOT in this store: a gitlink to a commit git has is
    # reported as a plain `commit`, which the 3-field shape already covers, so
    # the `submodule` shape would go untested.
    gitlink = f"{'0' * 39}7"
    subprocess.check_call(
        ["git", "-C", str(fake_git_repo), "update-index", "--add",
         "--cacheinfo", f"160000,{gitlink},vendor"])
    subprocess.check_call(
        ["git", "-C", str(fake_git_repo), "commit", "-qm", "gitlink"])
    subprocess.check_call(
        ["git", "-C", str(fake_git_repo), "branch", "feat/gitlink"])
    subprocess.check_call(
        ["git", "-C", str(fake_git_repo), "reset", "-q", "--hard", "HEAD~1"])
    ls_tree = _count_calls(monkeypatch, "_ls_tree")

    verdicts = classify_absent_paths(fake_git_repo, {"vendor"})

    assert ls_tree == []
    assert verdicts["vendor"] == ABSENT_ELSEWHERE


@pytest.mark.parametrize("call", [
    lambda repo: branch_refs._memoise(repo, "h", ("a",), {"x.py": ABSENT_DEAD}),
    lambda repo: branch_refs._memoised(repo, "h", ("a",)),
    lambda repo: branch_refs._landed_tips(repo, "h", ("a",)),
])
def test_every_memo_access_takes_the_lock(fake_git_repo, call):
    """The dashboard answers `/diff` and `/audit` from Flask's thread pool, and
    the eviction is a check-then-act — two threads picking the same victim key
    raise `KeyError` out of an endpoint that has nothing to do with caching.

    Asserted by holding the lock rather than by racing: a hammer loop only
    fails when it happens to interleave, so it can report a missing lock as
    green. Blocking is deterministic.
    """
    finished = threading.Event()

    def run():
        call(fake_git_repo)
        finished.set()

    with branch_refs._TIP_SCAN_LOCK:
        worker = threading.Thread(target=run)
        worker.start()
        assert not finished.wait(0.25), "memo access did not take the lock"

    assert finished.wait(5)
    worker.join()


def test_concurrent_audits_never_raise_out_of_the_cache(
    fake_git_repo, branch_owned_ref,
):
    """The lock's payoff, exercised end to end: many threads auditing the same
    repo while eviction churns."""
    errors: list[BaseException] = []
    absent = {branch_owned_ref, "gone.py"}

    def hammer(worker):
        for i in range(30):
            try:
                classify_absent_paths(fake_git_repo, absent)
                branch_refs._memoise(
                    fake_git_repo, f"h{i % 8}", (str(worker),),
                    {"x.py": ABSENT_DEAD})
                branch_refs._memoised(fake_git_repo, f"h{i % 8}", ("a",))
            except BaseException as exc:  # noqa: BLE001 - none may escape
                errors.append(exc)

    threads = [threading.Thread(target=hammer, args=(n,)) for n in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert len(branch_refs._TIP_SCAN_CACHE) <= branch_refs._TIP_SCAN_CACHE_MAX


def test_the_display_bucket_is_not_the_bulk_fix_waiver_set():
    """Two policies, two sets: a code added to `UNDELETABLE_REF_CODES` because
    it needs a human decision must not silently leave the error tally."""
    from lib.topics.validation import (
        INFORMATIONAL_DISPLAY_CODES,
        UNDELETABLE_REF_CODES,
    )

    assert INFORMATIONAL_DISPLAY_CODES is not UNDELETABLE_REF_CODES
