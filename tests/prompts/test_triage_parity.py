"""Characterization test: the editable drift-triage skeleton renders
byte-identical to the pre-refactor hardcoded f-string.

``_reference_triage_prompt`` below is a **frozen copy** of what
``agent_spawn._triage_prompt`` produced before the dynamic-prompt-template
refactor. It is fully self-contained (the triage builder had no shared helpers),
so the only thing under test is the migrated template body + the context wiring
in the new ``_triage_prompt``. If the two ever diverge, the migration dropped or
mangled text — edit the surface body and this reference together.
"""

from __future__ import annotations

import lib.topics.agent_spawn as asp


def _reference_triage_prompt(topic_id: str, wiki_md: str,
                             drifted_paths: list[str]) -> str:
    paths = "\n".join(f"- {p}" for p in drifted_paths) or "- (this topic's refs)"
    wiki_block = wiki_md.strip() or "(no wiki on file)"
    return (
        "A topic's ref files changed since its wiki was written. Decide whether "
        "the change is MATERIAL (the wiki narrative below is now inaccurate or "
        "incomplete and should be re-drafted) or TRIVIAL (formatting, comments, "
        "renames, or edits that don't change what the wiki says).\n\n"
        "Use your Read/Glob/Grep tools to read the changed files as they exist "
        "NOW, then compare against the wiki.\n\n"
        f"<topic_id>{topic_id}</topic_id>\n\n"
        f"<changed_refs>\n{paths}\n</changed_refs>\n\n"
        f"<current_wiki>\n{wiki_block}\n</current_wiki>\n\n"
        "<task>\nRead the changed refs, then answer with exactly one line:\n"
        "VERDICT: MATERIAL|TRIVIAL\n</task>"
    )


def _run(topic_id: str, wiki_md: str, drifted_paths: list[str]) -> tuple[str, str]:
    expected = _reference_triage_prompt(topic_id, wiki_md, drifted_paths)
    actual = asp._triage_prompt(topic_id, wiki_md, drifted_paths)
    return expected, actual


def test_parity_empty_paths_empty_wiki():
    # Edge case: 0 drifted paths and empty wiki — both fall back to placeholders.
    expected, actual = _run("auth", "", [])
    assert actual == expected
    assert "- (this topic's refs)" in actual
    assert "(no wiki on file)" in actual


def test_parity_single_path_with_wiki():
    expected, actual = _run(
        "trace-merge", "  # Trace merge\nMerges spans at read time.  ",
        ["lib/trace/merge.py"],
    )
    assert actual == expected


def test_parity_many_paths_with_wiki():
    expected, actual = _run(
        "topic-graph",
        "# Topic graph\nThe append-only store plus serve-time merge.",
        ["lib/topics/graph_io.py", "lib/topics/core.py", "db/schema.sql"],
    )
    assert actual == expected
    assert "<changed_refs>" in actual
