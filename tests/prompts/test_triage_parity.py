"""Characterization test: the editable drift-triage skeleton renders
byte-identical to a frozen reference of the evidence-pointer body.

``_reference_triage_prompt`` below is a **frozen copy** of the pointer-form
default body (single topic, ``VERDICT:`` answer format). It pins the template
at the ``render_surface`` level with a hand-built context — the runtime
context assembly (``agent_spawn._triage_prompt`` over
``drift_judge.evidence_context``) is covered by the topics suites. If the two
ever diverge, the body edit dropped or mangled text — edit the surface body
and this reference together (and register the superseded body's sha256 as a
retired default in ``lib/prompts/surfaces/triage.py``).
"""

from __future__ import annotations

from lib.prompts import render_surface
from lib.prompts.surfaces.triage import SURFACE_ID


def _reference_triage_prompt(topic_id: str, wiki_pointer: str,
                             changed_refs: str, repo_root: str) -> str:
    return (
        "A topic's ref files changed since its wiki was written. Decide "
        "whether the change is MATERIAL (the wiki narrative is now inaccurate "
        "or incomplete and should be re-drafted) or TRIVIAL (formatting, "
        "comments, renames, or edits that don't change what the wiki says).\n\n"
        "The evidence below is a set of pointers: the topic's current wiki "
        "path, and each changed ref with the baseline commit its digest was "
        "captured at, any wiki-cited identifiers that vanished from it, and a "
        "one-line change summary. All paths are relative to the repo root "
        f"{repo_root}. Pull the evidence yourself before judging:\n"
        "- Read the wiki file — it is the narrative you are judging.\n"
        f"- Run `git -C {repo_root} diff <baseline> -- <path>` for the real "
        f"old→new change, and `git -C {repo_root} log --oneline "
        "<baseline>..HEAD -- <path>` for the commits (and their intent) "
        "behind it.\n"
        "- Read/Glob/Grep anything else you need; do not rubber-stamp the "
        "summaries.\n\n"
        f"<topic_id>{topic_id}</topic_id>\n\n"
        f"<wiki>{wiki_pointer}</wiki>\n\n"
        f"<changed_refs>\n{changed_refs}\n</changed_refs>\n\n"
        "<task>\nAnswer with exactly one line:\n"
        "VERDICT: MATERIAL|TRIVIAL\n</task>"
    )


def _run(topic_id: str, wiki_pointer: str, changed_refs: str,
         repo_root: str) -> tuple[str, str]:
    expected = _reference_triage_prompt(topic_id, wiki_pointer,
                                        changed_refs, repo_root)
    actual = render_surface(SURFACE_ID, {
        "topic_id": topic_id, "wiki_pointer": wiki_pointer,
        "changed_refs": changed_refs, "repo_root": repo_root,
    })
    return expected, actual


def test_parity_no_paths_no_wiki():
    expected, actual = _run(
        "auth", "(no wiki on file)", "- (no changed paths recorded)",
        "/repos/auth",
    )
    assert actual == expected
    assert "(no wiki on file)" in actual
    assert "- (no changed paths recorded)" in actual


def test_parity_single_annotated_ref():
    expected, actual = _run(
        "trace-merge", ".regin/topics/wiki/trace-merge.md",
        "- lib/trace/merge.py — wiki cites `merge_spans`, no longer present"
        " — baseline abc123 — 1 file changed, 4 insertions(+)",
        "/repos/regin",
    )
    assert actual == expected
    assert "git -C /repos/regin diff" in actual


def test_parity_multiline_refs():
    refs = ("- lib/topics/graph_io.py — baseline abc123 — 2 files changed\n"
            "- db/schema.sql — no baseline recorded "
            "(read the file as it is now)")
    expected, actual = _run(
        "topic-graph", ".regin/topics/wiki/topic-graph.md", refs,
        "/repos/regin",
    )
    assert actual == expected
    assert "<changed_refs>" in actual
