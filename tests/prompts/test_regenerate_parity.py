"""Characterization test: the editable ``topic-proposal-regenerate`` surface
renders byte-identical to the pre-refactor hardcoded ``_prior_reference_block``
f-string.

``_reference_prior_reference`` is a **frozen copy** of what
``proposal_external._prior_reference_block`` produced before the block was moved
into prompt management. It calls the same (unchanged, shared) helpers, so the
only thing under test is the migrated surface body + the context wiring. If the
two diverge, the migration dropped or mangled text — edit the surface body and
this reference together.
"""

from __future__ import annotations

import json

import lib.topics.proposal_external as pe


def _reference_prior_reference(prior_draft):
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

Previous proposal JSON:
```json
{json.dumps(pe._prior_proposal_for_prompt(prior_draft.get('proposal')), indent=2, sort_keys=True)}
```

Previous wiki markdown:
```markdown
{str(prior_draft.get('wiki') or '')}
```
"""


def test_fresh_run_is_empty():
    assert pe._prior_reference_block(None) == ""
    assert pe._prior_reference_block({}) == ""


def test_parity_prior_draft_no_feedback_no_scope():
    prior = {
        "proposal": {"version": 1, "topics": [{"id": "t1", "label": "T1"}]},
        "wiki": "# Old wiki\ncontent",
    }
    actual = pe._prior_reference_block(prior)
    assert actual == _reference_prior_reference(prior)
    assert "Prior draft reference:" in actual


def test_parity_prior_draft_with_feedback():
    prior = {
        "proposal": {"version": 1, "topics": [{"id": "t1", "label": "T1"}]},
        "wiki": "# Old wiki\ncontent",
        "feedback_threads": [
            {"topic_id": "t1", "anchor": "intent", "quoted_text": "vague",
             "comments": [{"author": "rev", "body": "sharpen this"}]},
        ],
    }
    assert pe._prior_reference_block(prior) == _reference_prior_reference(prior)


def test_parity_scoped_regenerate_includes_directive():
    prior = {
        "proposal": {"version": 1, "topics": [{"id": "t1", "label": "T1"}]},
        "wiki": "# Old wiki",
        "scope_topic_ids": ["t1"],
        "scope_drifted_paths": {"t1": ["lib/t1.py"]},
    }
    actual = pe._prior_reference_block(prior)
    assert actual == _reference_prior_reference(prior)
    assert "Scoped refresh" in actual
    assert "`lib/t1.py`" in actual
