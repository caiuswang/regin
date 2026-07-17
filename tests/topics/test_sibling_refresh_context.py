"""Sibling-aware content-drift refresh: each refresh agent run is given the
OTHER pending content-drift siblings' current on-disk wiki excerpt +
drifted_paths so cross-references stay consistent across the batch.

Covers the helper `_sibling_refresh_context` and its injection into the
drafting prompt via `_instructions` (0 siblings, N≥1 siblings, sibling with
no wiki file).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lib.topics import topic_dir
from lib.topics.agent_spawn import _sibling_refresh_context
from lib.topics.content_drift import emit_refresh_proposal
from lib.topics.core import write_split_graph
from lib.topics.proposal_external import _instructions
from lib.topics.snapshots import resolve_or_create_repo


def _topic(refs: list[dict]) -> dict:
    return {
        "label": "T", "intent": "t", "status": "active", "aliases": [],
        "refs": refs, "edges": [], "commands": [],
        "include_globs": [], "exclude_globs": [],
    }


def _seed(repo: Path, topics: dict) -> None:
    write_split_graph(repo, {"version": 1, "repo": repo.name,
                            "updated_at": "2026-01-01T00:00:00Z", "topics": topics})
    resolve_or_create_repo(str(repo))


def _write_wiki(repo: Path, topic_id: str, body: str) -> None:
    from lib.topics.wiki import wiki_dir
    wd = wiki_dir(repo)
    wd.mkdir(parents=True, exist_ok=True)
    (wd / f"{topic_id}.md").write_text(body)


def _proposal_id(topic_id: str) -> str:
    return f"content-drift-{topic_id}"


# ── 0 siblings ────────────────────────────────────────────────


def test_zero_siblings_returns_empty(fake_git_repo):
    repo = fake_git_repo
    _seed(repo, {"t1": _topic([{"path": "a.py"}])})
    emit_refresh_proposal(repo, "t1", ["a.py"])

    # The lone proposal has no OTHER siblings → empty.
    assert _sibling_refresh_context(repo, _proposal_id("t1")) == ""


def test_zero_siblings_no_prompt_section(fake_git_repo):
    repo = fake_git_repo
    _seed(repo, {"t1": _topic([{"path": "a.py"}])})
    emit_refresh_proposal(repo, "t1", ["a.py"])

    out_dir = topic_dir(repo) / "proposals" / _proposal_id("t1")
    instructions = _instructions(repo, "req", out_dir, out_dir / "out.json")
    assert "Sibling topics being refreshed" not in instructions


def test_no_content_drift_proposals_returns_empty(fake_git_repo):
    # A user/external proposal id isn't in the content-drift set → empty.
    repo = fake_git_repo
    _seed(repo, {"t1": _topic([{"path": "a.py"}])})
    assert _sibling_refresh_context(repo, "some-user-proposal") == ""


# ── N≥1 siblings ──────────────────────────────────────────────


def test_siblings_block_lists_others_and_excludes_self(fake_git_repo):
    repo = fake_git_repo
    _seed(repo, {
        "t1": _topic([{"path": "a.py"}]),
        "t2": _topic([{"path": "b.py"}]),
        "t3": _topic([{"path": "c.py"}]),
    })
    emit_refresh_proposal(repo, "t1", ["a.py"])
    emit_refresh_proposal(repo, "t2", ["b.py"])
    emit_refresh_proposal(repo, "t3", ["c.py"])
    _write_wiki(repo, "t2", "# T2\n\nt2 narrative\n")
    _write_wiki(repo, "t3", "# T3\n\nt3 narrative\n")

    block = _sibling_refresh_context(repo, _proposal_id("t1"))

    # both siblings present with their drifted paths
    assert "t2" in block and "b.py" in block
    assert "t3" in block and "c.py" in block
    # self excluded — no t1 / a.py
    assert "`t1`" not in block
    assert "a.py" not in block


def test_siblings_block_includes_wiki_excerpt(fake_git_repo):
    repo = fake_git_repo
    _seed(repo, {
        "t1": _topic([{"path": "a.py"}]),
        "t2": _topic([{"path": "b.py"}]),
    })
    emit_refresh_proposal(repo, "t1", ["a.py"])
    emit_refresh_proposal(repo, "t2", ["b.py"])
    _write_wiki(repo, "t2", "# T2\n\nthe distinctive t2 narrative\n")

    block = _sibling_refresh_context(repo, _proposal_id("t1"))
    assert "the distinctive t2 narrative" in block


def test_siblings_excerpt_truncated(fake_git_repo):
    repo = fake_git_repo
    _seed(repo, {
        "t1": _topic([{"path": "a.py"}]),
        "t2": _topic([{"path": "b.py"}]),
    })
    emit_refresh_proposal(repo, "t1", ["a.py"])
    emit_refresh_proposal(repo, "t2", ["b.py"])
    _write_wiki(repo, "t2", "x" * 5000)

    block = _sibling_refresh_context(repo, _proposal_id("t1"))
    assert "…(truncated)" in block
    # excerpt bounded well under the raw 5000 chars
    assert block.count("x") <= 900


def test_prompt_section_emitted_with_siblings(fake_git_repo):
    repo = fake_git_repo
    _seed(repo, {
        "t1": _topic([{"path": "a.py"}]),
        "t2": _topic([{"path": "b.py"}]),
    })
    emit_refresh_proposal(repo, "t1", ["a.py"])
    emit_refresh_proposal(repo, "t2", ["b.py"])
    _write_wiki(repo, "t2", "# T2\n\nt2 narrative\n")

    out_dir = topic_dir(repo) / "proposals" / _proposal_id("t1")
    instructions = _instructions(repo, "req", out_dir, out_dir / "out.json")
    assert "Sibling topics being refreshed" in instructions
    assert "t2" in instructions
    # comes after the existing-approved-topics block
    assert (instructions.index("Existing approved topics")
            < instructions.index("Sibling topics being refreshed"))


# ── terminal-status siblings are not "in this batch" ──────────


def test_terminal_sibling_excluded(fake_git_repo):
    # A content-drift proposal that has left pending_review (here: ignored, the
    # same terminal a TRIVIAL dismissal or a human "ignore" reaches) is NOT a
    # live sibling — it must not be injected as "being rewritten alongside you".
    from lib.topics.proposals import ignore_proposed_topic

    repo = fake_git_repo
    _seed(repo, {
        "t1": _topic([{"path": "a.py"}]),
        "t2": _topic([{"path": "b.py"}]),
    })
    emit_refresh_proposal(repo, "t1", ["a.py"])
    emit_refresh_proposal(repo, "t2", ["b.py"])
    _write_wiki(repo, "t2", "# T2\n\nt2 narrative\n")

    # t2 reaches a terminal state → it should drop out of the sibling set.
    ignore_proposed_topic(repo, _proposal_id("t2"), "t2")

    block = _sibling_refresh_context(repo, _proposal_id("t1"))
    assert "`t2`" not in block
    assert block == ""


# ── sibling with no wiki file ─────────────────────────────────


def test_sibling_without_wiki_included_no_raise(fake_git_repo):
    repo = fake_git_repo
    _seed(repo, {
        "t1": _topic([{"path": "a.py"}]),
        "t2": _topic([{"path": "b.py"}]),
    })
    emit_refresh_proposal(repo, "t1", ["a.py"])
    emit_refresh_proposal(repo, "t2", ["b.py"])
    # deliberately write NO wiki file for t2

    block = _sibling_refresh_context(repo, _proposal_id("t1"))
    assert "t2" in block
    assert "b.py" in block
    assert "(no wiki on file yet)" in block
