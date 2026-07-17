"""Wiki-anchor grounding for content-drift materiality.

A digest row captured while the topic has a wiki stores the identifier
tokens that wiki cites and that ground to the ref file. Detection then
treats a hash-changed ref as material only when a cited anchor vanished;
rows with no anchor set (no wiki at capture / pre-anchor rows) keep the
legacy flag-on-hash behavior.
"""

from __future__ import annotations

from pathlib import Path

from lib.topics.content_drift import (
    _drift_note_body,
    detect_drifted_topics,
    emit_refresh_proposal,
)
from lib.topics.core import write_split_graph
from lib.topics.proposals import load_proposal
from lib.topics.ref_digest import capture_ref_digests, digests_for_topic
from lib.topics.snapshots import resolve_or_create_repo
from lib.topics.wiki import wiki_dir
from lib.topics.wiki_anchors import anchors_in_content, wiki_anchor_tokens


def _topic(refs: list[dict]) -> dict:
    return {
        "label": "T", "intent": "t", "status": "active", "aliases": [],
        "refs": refs, "edges": [], "commands": [],
        "include_globs": [], "exclude_globs": [],
    }


def _write_graph(repo: Path, topics: dict) -> None:
    write_split_graph(repo, {"version": 1, "repo": repo.name,
                            "updated_at": "2026-01-01T00:00:00Z",
                            "topics": topics})


def _register(repo: Path) -> int:
    return resolve_or_create_repo(str(repo)).id


def _write_wiki(repo: Path, topic_id: str, text: str) -> None:
    root = wiki_dir(repo)
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{topic_id}.md").write_text(text)


# ── extraction ────────────────────────────────────────────────


def test_wiki_anchor_tokens_extracts_code_span_identifiers():
    text = ("The `capture_ref_digests` helper reads `lib/topics/route.py:104`. "
            "Prose mentions of detect_drifted_topics are not claims.")
    tokens = wiki_anchor_tokens(text)
    assert "capture_ref_digests" in tokens
    assert {"topics", "route"} <= tokens          # pathy span → segments
    assert "lib" not in tokens                    # < 4 chars dropped
    assert "detect_drifted_topics" not in tokens  # prose, not a code span


def test_overlong_span_cannot_leak_prose_or_drop_later_anchors():
    """An over-long span must be consumed and discarded whole — with a naive
    length-capped regex its closing backtick re-pairs with the next span's
    opener, extracting inter-span prose and losing the real anchor."""
    text = "`" + "x" * 121 + "` then prose_word_here `real_anchor`"
    tokens = wiki_anchor_tokens(text)
    assert tokens == {"real_anchor"}


def test_newline_span_is_valid_and_cannot_shift_pairing():
    """CommonMark allows a newline inside a code span; it must parse as one
    span so its closing backtick cannot re-pair with the next span's opener
    and leak the prose between them."""
    text = "cites `wrapped\nspan_token` then prose_here `real_anchor`"
    tokens = wiki_anchor_tokens(text)
    assert {"wrapped", "span_token", "real_anchor"} <= tokens
    assert "prose_here" not in tokens


def test_fenced_code_blocks_are_not_citations():
    text = ("Intro cites `real_anchor`.\n"
            "```python\n"
            "fenced_example_symbol = 1  # `span_like` inside fence\n"
            "```\n"
            "Outro cites `other_anchor`.")
    tokens = wiki_anchor_tokens(text)
    assert {"real_anchor", "other_anchor"} <= tokens
    assert "fenced_example_symbol" not in tokens
    assert "span_like" not in tokens


def test_blank_line_bounds_stray_backtick_damage():
    text = "stray ` here\n\nnext paragraph cites `real_anchor`"
    tokens = wiki_anchor_tokens(text)
    assert "real_anchor" in tokens
    assert "paragraph" not in tokens


def test_tilde_fenced_blocks_are_not_citations():
    text = ("Cites `real_anchor`.\n"
            "~~~\n"
            "tilde_fenced_symbol = 1\n"
            "~~~\n"
            "Done.")
    tokens = wiki_anchor_tokens(text)
    assert "real_anchor" in tokens
    assert "tilde_fenced_symbol" not in tokens


def test_unterminated_fence_keeps_later_citations():
    """A truncated wiki whose last fence never closes must not swallow every
    citation after it — only the dangling marker line is dropped."""
    text = ("Intro cites `alpha_anchor`.\n"
            "```python\n"
            "some = code\n"
            "later prose cites `beta_anchor`.")
    tokens = wiki_anchor_tokens(text)
    assert {"alpha_anchor", "beta_anchor"} <= tokens


def test_fence_between_prose_keeps_paragraph_boundary():
    """Blanked fence lines must leave a paragraph break — rejoining the
    surrounding prose would let a stray backtick above the fence pair into
    the prose below it and swallow the real citation."""
    text = ("Intro stray ` tick\n"
            "```\n"
            "code = 1\n"
            "```\n"
            "then `real_anchor` here")
    tokens = wiki_anchor_tokens(text)
    assert "real_anchor" in tokens
    assert "tick" not in tokens and "then" not in tokens


def test_anchors_in_content_keeps_only_grounded_tokens():
    tokens = {"frobnicate_widget", "absent_symbol"}
    content = "def frobnicate_widget():\n    return 1\n"
    assert anchors_in_content(tokens, content) == ["frobnicate_widget"]


# ── capture ───────────────────────────────────────────────────


def test_capture_stores_wiki_anchors(fake_git_repo):
    repo = fake_git_repo
    (repo / "a.py").write_text("def frobnicate_widget():\n    return 1\n")
    _write_graph(repo, {"t1": _topic([{"path": "a.py"}])})
    repo_id = _register(repo)
    _write_wiki(repo, "t1",
                "Grounds on `frobnicate_widget`; also cites `absent_symbol`.")

    capture_ref_digests(repo, "t1")

    (digest,) = digests_for_topic(repo_id, "t1")
    assert digest["anchors"] == ["frobnicate_widget"]


def test_capture_without_wiki_stores_null_anchors(fake_git_repo):
    repo = fake_git_repo
    (repo / "a.py").write_text("x\n")
    _write_graph(repo, {"t1": _topic([{"path": "a.py"}])})
    repo_id = _register(repo)

    capture_ref_digests(repo, "t1")

    (digest,) = digests_for_topic(repo_id, "t1")
    assert digest["anchors"] is None


# ── detection ─────────────────────────────────────────────────


def _seed_anchored(repo: Path, wiki_text: str) -> None:
    (repo / "a.py").write_text("def frobnicate_widget():\n    return 1\n")
    _write_graph(repo, {"t1": _topic([{"path": "a.py"}])})
    _register(repo)
    _write_wiki(repo, "t1", wiki_text)
    capture_ref_digests(repo, "t1")


def test_anchor_preserving_change_is_spared(fake_git_repo):
    repo = fake_git_repo
    _seed_anchored(repo, "Grounds on `frobnicate_widget`.")
    (repo / "a.py").write_text(
        "# reshuffled and grown, claim intact\n"
        "def frobnicate_widget():\n    return 2\n")

    assert detect_drifted_topics(repo) == []


def test_vanished_anchor_flags_with_missing_anchors(fake_git_repo):
    repo = fake_git_repo
    _seed_anchored(repo, "Grounds on `frobnicate_widget`.")
    (repo / "a.py").write_text("def renamed_widget():\n    return 1\n")

    assert detect_drifted_topics(repo) == [{
        "topic_id": "t1", "drifted_paths": ["a.py"],
        "missing_anchors": {"a.py": ["frobnicate_widget"]},
    }]


def test_empty_anchor_set_falls_back_to_hash_flag(fake_git_repo):
    """A wiki that cites nothing grounding to this ref makes no checkable
    claim about it — the anchor tier can't judge, so the ref falls back to
    the hash tier rather than being permanently exempt from drift."""
    repo = fake_git_repo
    _seed_anchored(repo, "Only cites `symbol_living_elsewhere`.")
    (repo / "a.py").write_text("entirely rewritten\n")

    assert detect_drifted_topics(repo) == [
        {"topic_id": "t1", "drifted_paths": ["a.py"]}]


def test_superset_rename_reads_as_vanished(fake_git_repo):
    """Grounding is token-exact: `frobnicate_widget` surviving only inside
    `frobnicate_widget_v2` is a vanished claim, not a match."""
    repo = fake_git_repo
    _seed_anchored(repo, "Grounds on `frobnicate_widget`.")
    (repo / "a.py").write_text("def frobnicate_widget_v2():\n    return 1\n")

    assert detect_drifted_topics(repo) == [{
        "topic_id": "t1", "drifted_paths": ["a.py"],
        "missing_anchors": {"a.py": ["frobnicate_widget"]},
    }]


def test_wiki_read_error_preserves_stored_anchors(fake_git_repo):
    """A transiently unreadable wiki must not null a good anchor set — that
    would silently revert the topic to noisy hash-only judging."""
    import os

    repo = fake_git_repo
    _seed_anchored(repo, "Grounds on `frobnicate_widget`.")
    page = wiki_dir(repo) / "t1.md"
    os.chmod(page, 0o000)
    try:
        capture_ref_digests(repo, "t1")
    finally:
        os.chmod(page, 0o644)

    from lib.topics.ref_digest import repo_id_for_path
    (digest,) = digests_for_topic(repo_id_for_path(repo), "t1")
    assert digest["anchors"] == ["frobnicate_widget"]


def test_wiki_added_after_capture_keeps_hash_behavior(fake_git_repo):
    """Rows captured before the topic had a wiki carry NULL anchors and must
    keep the legacy flag-on-hash behavior even once a wiki appears."""
    repo = fake_git_repo
    (repo / "a.py").write_text("def frobnicate_widget():\n    return 1\n")
    _write_graph(repo, {"t1": _topic([{"path": "a.py"}])})
    _register(repo)
    capture_ref_digests(repo, "t1")                 # no wiki yet → NULL
    _write_wiki(repo, "t1", "Grounds on `frobnicate_widget`.")
    (repo / "a.py").write_text("def frobnicate_widget():\n    return 2\n")

    assert detect_drifted_topics(repo) == [
        {"topic_id": "t1", "drifted_paths": ["a.py"]}]


# ── note body / proposal metadata ─────────────────────────────


def test_drift_note_body_names_missing_anchors():
    body = _drift_note_body("t1", ["a.py", "b.py"],
                            {"a.py": ["frobnicate_widget"]})
    assert "- `a.py` — the wiki cites `frobnicate_widget`, no longer present" in body
    assert "- `b.py`" in body


def test_standalone_refresh_metadata_carries_missing_anchors(fake_git_repo):
    repo = fake_git_repo
    (repo / "a.py").write_text("x\n")
    _write_graph(repo, {"t1": _topic([{"path": "a.py"}])})
    _register(repo)

    pid = emit_refresh_proposal(
        repo, "t1", ["a.py"],
        missing_anchors={"a.py": ["frobnicate_widget"]})

    proposal = load_proposal(repo, pid)
    assert proposal["metadata"]["missing_anchors"] == {
        "a.py": ["frobnicate_widget"]}
