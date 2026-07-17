"""Characterization test: the editable ``topic-proposal-regenerate`` surface
renders byte-identical to a frozen reference of the prior-reference block.

``_reference_prior_reference`` is a **frozen copy** of what
``proposal_external._prior_reference_block`` produces. It calls the same
(unchanged, shared) helpers, so the only thing under test is the surface body +
the context wiring. If the two diverge, an edit dropped or mangled text — edit
the surface body and this reference together.

The block hands the agent an absolute-path *pointer* to the prior on-disk
``wiki.md`` and a wiki-stripped proposal JSON — no wiki narrative is embedded in
the prompt.
"""

from __future__ import annotations

import json

import lib.topics.proposal_external as pe
from lib.topics.wiki import wiki_read_pointer


def _reference_prior_reference(repo, out_dir, prior_draft):
    if not prior_draft:
        return ""
    feedback_reference = ""
    feedback_block = pe.format_review_feedback_for_prompt(prior_draft.get("feedback_threads") or [])
    if feedback_block:
        feedback_reference = f"""
{feedback_block}

"""
    scope_reference = pe._scoped_refresh_directive(prior_draft)
    return f"""

Prior draft reference:
{feedback_reference}{scope_reference}Use the previous proposal and wiki only as reference — to keep good coverage and to address any review feedback above — not as a baseline to diff against. Re-check every topic against the current repository.

Write each topic's wiki and the notes as a standalone description of the repository as it is NOW. Do NOT write changelog or diff prose comparing this revision to the previous one: avoid phrasing like "was removed", "is now", "no longer", "the old …", "previously", "changed from", or "renamed to". The reader has never seen the prior draft — describe the current structure and behavior directly, citing files that exist today.

A regenerate REVISES the page in place; it does not accrete. Keep each wiki's scope and length close to the prior draft — correct what the current code no longer matches and cut detail that has gone stale, rather than appending new file-by-file descriptions because some files changed. A drift note asking you to refresh a topic is a request to re-verify and tighten its existing narrative, not to grow it.

Previous proposal JSON (structural fields only — each topic's `wiki` body is omitted):
```json
{json.dumps(pe._prior_proposal_for_prompt(prior_draft.get('proposal')), indent=2, sort_keys=True)}
```

Previous wiki markdown (not embedded here — Read it yourself with your Read tool at this path): {wiki_read_pointer(repo, out_dir / 'wiki.md', absolute=True)}
"""


def _dirs(tmp_path):
    repo = tmp_path / "repo"
    out_dir = repo / ".regin" / "topics" / "proposals" / "p1"
    out_dir.mkdir(parents=True)
    return repo, out_dir


def test_fresh_run_is_empty(tmp_path):
    repo, out_dir = _dirs(tmp_path)
    assert pe._prior_reference_block(repo, out_dir, None) == ""
    assert pe._prior_reference_block(repo, out_dir, {}) == ""


def test_parity_prior_draft_no_feedback_no_scope(tmp_path):
    repo, out_dir = _dirs(tmp_path)
    (out_dir / "wiki.md").write_text("# Old wiki\ncontent")
    prior = {
        "proposal": {"version": 1, "topics": [{"id": "t1", "label": "T1"}]},
    }
    actual = pe._prior_reference_block(repo, out_dir, prior)
    assert actual == _reference_prior_reference(repo, out_dir, prior)
    assert "Prior draft reference:" in actual


def test_parity_prior_draft_with_feedback(tmp_path):
    repo, out_dir = _dirs(tmp_path)
    (out_dir / "wiki.md").write_text("# Old wiki\ncontent")
    prior = {
        "proposal": {"version": 1, "topics": [{"id": "t1", "label": "T1"}]},
        "feedback_threads": [
            {"topic_id": "t1", "anchor": "intent", "quoted_text": "vague",
             "comments": [{"author": "rev", "body": "sharpen this"}]},
        ],
    }
    assert pe._prior_reference_block(repo, out_dir, prior) == \
        _reference_prior_reference(repo, out_dir, prior)


def test_parity_scoped_regenerate_includes_directive(tmp_path):
    repo, out_dir = _dirs(tmp_path)
    (out_dir / "wiki.md").write_text("# Old wiki")
    prior = {
        "proposal": {"version": 1, "topics": [{"id": "t1", "label": "T1"}]},
        "scope_topic_ids": ["t1"],
        "scope_drifted_paths": {"t1": ["lib/t1.py"]},
    }
    actual = pe._prior_reference_block(repo, out_dir, prior)
    assert actual == _reference_prior_reference(repo, out_dir, prior)
    assert "Scoped refresh" in actual
    assert "`lib/t1.py`" in actual


# ── pointer form: no wiki content rides in the prompt ─────────


def test_wiki_content_not_embedded(tmp_path):
    repo, out_dir = _dirs(tmp_path)
    (out_dir / "wiki.md").write_text("the distinctive on-disk wiki body")
    prior = {
        "proposal": {"version": 1, "topics": [
            {"id": "t1", "label": "T1", "wiki": "the distinctive topic narrative"},
        ], "wiki": "combined draft narrative"},
    }
    actual = pe._prior_reference_block(repo, out_dir, prior)
    assert "distinctive" not in actual
    assert "combined draft narrative" not in actual
    assert ".regin/topics/proposals/p1/wiki.md" in actual


def test_no_prior_wiki_yields_placeholder(tmp_path):
    repo, out_dir = _dirs(tmp_path)
    prior = {"proposal": {"version": 1, "topics": [{"id": "t1", "label": "T1"}]}}
    actual = pe._prior_reference_block(repo, out_dir, prior)
    assert "(no wiki on file)" in actual


def test_prior_proposal_json_strips_wiki_fields():
    clean = pe._prior_proposal_for_prompt({
        "version": 1,
        "wiki": "run-level narrative",
        "notes": ["self note"],
        "topics": [
            {"id": "t1", "wiki": "t1 narrative", "refs": [{"path": "a.py"}]},
            "not-a-dict",
        ],
    })
    assert "wiki" not in clean
    assert "notes" not in clean
    assert clean["topics"][0] == {"id": "t1", "refs": [{"path": "a.py"}]}
    assert clean["topics"][1] == "not-a-dict"
