"""The regenerate prompt renders each feedback thread's structured metadata
instead of the machine-authored prose it was extracted from.

The split is by author and by what regin already persisted — never by length.
These pin the three branches that decide it, because a regression here is
silent: the prompt still builds, it just carries 12k of a reviewer's working
notes again, or quietly loses a comment.
"""

from __future__ import annotations

from lib.topics.proposal_drafting import format_review_feedback_for_prompt
from lib.topics.proposal_review import _all_issues_attributed, _issue_reason


class _Issue:
    def __init__(self, code, message, topic_ids=()):
        self.code = code
        self.message = message
        self.topic_ids = topic_ids


def _review_thread(comments, **metadata):
    return {"kind": "review_note", "anchor_kind": "general",
            "metadata": metadata, "comments": comments}


def test_reviewer_prose_is_replaced_by_its_own_verdicts():
    text = format_review_feedback_for_prompt([_review_thread(
        [{"author_kind": "agent", "body": "## Ref paths\n" + "x" * 12000}],
        topic_verdicts={"session-trace-design": {
            "verdict": "FAIL", "reason": "repair.py misdescribed"}},
        recommendation="regenerate",
    )])
    assert "## Ref paths" not in text
    assert "`session-trace-design` = FAIL — repair.py misdescribed" in text
    assert "recommendation: REGENERATE" in text


def test_human_comments_are_never_summarised():
    body = ("Do not enumerate all files and designs; only keep key information "
            "the future agent may need.\nSecond line survives too.")
    text = format_review_feedback_for_prompt([_review_thread(
        [{"author_kind": "user", "body": body}],
        topic_verdicts={"t1": {"verdict": "FAIL", "reason": "r"}},
    )])
    assert body in text


def test_agent_reply_after_the_write_up_stays_verbatim():
    """Only the comment the metadata was extracted from is replaced; a later
    reply has no structured twin of its own, so dropping it would be silent
    loss."""
    text = format_review_feedback_for_prompt([_review_thread(
        [{"author_kind": "agent", "body": "THE WRITE UP " + "z" * 5000},
         {"author_kind": "agent", "body": "a follow-up instruction"}],
        topic_verdicts={"t1": {"verdict": "FAIL", "reason": "r"}},
    )])
    assert "THE WRITE UP" not in text
    assert "a follow-up instruction" in text


def test_agent_prose_without_a_structured_twin_stays_verbatim():
    text = format_review_feedback_for_prompt([_review_thread(
        [{"author_kind": "agent", "body": "free-form note, nothing extracted"}],
    )])
    assert "free-form note, nothing extracted" in text


def test_verdicts_that_do_not_cover_the_body_keep_the_prose():
    text = format_review_feedback_for_prompt([_review_thread(
        [{"author_kind": "agent", "body": "- **orphan_code**: names no topic"}],
        topic_verdicts={"t1": {"verdict": "FAIL", "reason": "r"}},
        verdicts_cover_body=False,
    )])
    assert "orphan_code" in text
    assert "`t1` = FAIL" not in text


def test_drift_note_renders_changed_files_but_not_vanished_anchors():
    text = format_review_feedback_for_prompt([{
        "kind": "content_drift", "proposal_topic_id": "session-trace-design",
        "anchor_kind": "wiki_range",
        "metadata": {"drifted_paths": ["lib/trace/span_gates.py"],
                     "missing_anchors": {"lib/trace/span_gates.py": ["queries"]}},
        "comments": [{"author_kind": "agent", "body": "The code under…" + "y" * 1500}],
    }])
    assert "`lib/trace/span_gates.py`" in text
    assert "queries" not in text
    assert "yyy" not in text


def test_all_issues_attributed_requires_this_issue_to_be_the_stored_one():
    """`_precheck_verdicts` stores the FIRST issue per topic (`setdefault`), so
    topic presence alone would mark a second issue's message as carried when it
    was dropped."""
    first = _Issue("dup_ref", "a.py claimed twice", ("t1",))
    second = _Issue("bad_role", "role is not in the allowed set", ("t1",))
    verdicts = {"t1": {"verdict": "FAIL", "reason": _issue_reason(first)}}

    assert _all_issues_attributed((first,), verdicts) is True
    assert _all_issues_attributed((first, second), verdicts) is False


def test_all_issues_attributed_rejects_unresolvable_topics():
    verdicts = {"t1": {"verdict": "FAIL", "reason": "dup_ref: x"}}
    assert _all_issues_attributed((_Issue("c", "m"),), verdicts) is False
    assert _all_issues_attributed((_Issue("c", "m", ("gone",)),), verdicts) is False
