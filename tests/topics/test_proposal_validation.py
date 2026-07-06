"""Single-layer validation of proposal payloads (`validate_proposal`).

Covers the fold of the agent-output contract into one place: the
non-empty-wiki requirement (opt-in via `require_wiki`), type checks for
the prompt-advertised optional fields (`parent_id`, `blurb`, ref entries),
actionable per-field messages, and the structured
`ProposalValidationError` the ingest paths persist.
"""

from __future__ import annotations

import pytest

from lib.topics.proposal_drafting import (
    ProposalValidationError,
    validate_proposal,
)


def _topic(**overrides):
    topic = {
        "id": "service", "label": "Service", "aliases": [],
        "intent": "Curated context for Service.", "status": "active",
        "refs": [], "edges": [], "commands": [],
        "include_globs": [], "exclude_globs": [], "evidence_paths": [],
        "wiki": "# Service\n\nA page.\n",
    }
    topic.update(overrides)
    return topic


def _proposal(**overrides):
    proposal = {"version": 1, "topics": [_topic()]}
    proposal.update(overrides)
    return proposal


def test_valid_proposal_passes_with_wiki_required():
    assert validate_proposal(_proposal(), require_wiki=True) == []


def test_missing_wiki_caught_only_when_required():
    proposal = _proposal(topics=[_topic(wiki="   ")])

    errors = validate_proposal(proposal, require_wiki=True)

    assert len(errors) == 1
    assert "empty wiki" in errors[0]
    # Review-time callers (accept/merge/update) validate structure only.
    assert validate_proposal(proposal) == []


def test_legacy_top_level_wiki_satisfies_requirement():
    proposal = _proposal(topics=[_topic(wiki="")], wiki="# Legacy combined doc\n")

    assert validate_proposal(proposal, require_wiki=True) == []


def test_bad_parent_id_and_blurb_types_have_actionable_messages():
    proposal = _proposal(topics=[_topic(parent_id=123, blurb=["not", "a", "str"])])

    errors = validate_proposal(proposal)

    assert "topics[0].parent_id must be a string when present" in errors
    assert "topics[0].blurb must be a string when present" in errors


def test_null_parent_id_and_blurb_are_allowed():
    proposal = _proposal(topics=[_topic(parent_id=None, blurb=None)])

    assert validate_proposal(proposal) == []


def test_ref_entry_type_errors_are_per_entry_and_specific():
    proposal = _proposal(topics=[_topic(refs=[
        "lib/settings.py",                       # not an object
        {"path": 5},                             # path wrong type
        {"path": "lib/ok.py", "role": 3, "tier": []},
    ])])

    errors = validate_proposal(proposal)

    assert "topics[0].refs[0] must be an object with a string `path`" in errors
    assert "topics[0].refs[1].path must be a string" in errors
    assert "topics[0].refs[2].role must be a string when present" in errors
    assert "topics[0].refs[2].tier must be a string when present" in errors


def test_valid_ref_role_and_tier_pass():
    proposal = _proposal(topics=[_topic(refs=[
        {"path": "lib/settings.py", "role": "implementation", "tier": "reference"},
        {"path": "lib/other.py"},
    ])])

    assert validate_proposal(proposal) == []


def test_unknown_extra_fields_are_not_rejected():
    proposal = _proposal(
        topics=[_topic(confidence=0.9, source_notes=["exploration"])],
        overview="An intro.", extra_top_level={"x": 1},
    )

    assert validate_proposal(proposal, require_wiki=True) == []


def test_non_object_topic_entry_is_reported_not_crashed():
    errors = validate_proposal({"version": 1, "topics": ["just-a-string"]})

    assert "topics[0] must be an object" in errors


def test_normalise_agent_payload_empty_wiki_raises_structured_error(fake_git_repo):
    """The wiki requirement now comes from validate_proposal but keeps the
    same error surface: a ValueError (subclass) mentioning the empty wiki,
    from the shared normalise step both ingest paths call."""
    from lib.topics.proposal_external import _normalise_agent_payload

    payload = {"topics": [_topic(wiki="")], "wiki": "   "}

    with pytest.raises(ProposalValidationError, match="empty wiki") as excinfo:
        _normalise_agent_payload(fake_git_repo, payload)

    assert any("empty wiki" in error for error in excinfo.value.errors)


def test_normalise_agent_payload_wrapper_branch_keeps_legacy_wiki(fake_git_repo):
    """A legacy payload (no version key, top-level wiki only) must still
    ingest: the wrap may not drop the top-level wiki the combined-wiki
    fallback reads."""
    from lib.topics.proposal_external import _normalise_agent_payload

    payload = {"topics": [_topic(wiki="")], "wiki": "# Legacy\n\nDoc.\n"}

    proposal, wiki = _normalise_agent_payload(fake_git_repo, payload)

    assert proposal["version"] == 1
    assert wiki.strip() == "# Legacy\n\nDoc."


def test_normalise_agent_payload_error_carries_all_field_errors(fake_git_repo):
    from lib.topics.proposal_external import _normalise_agent_payload

    payload = {"topics": [{"id": "svc", "refs": [{"path": 7}]}], "wiki": "# W\n"}

    with pytest.raises(ProposalValidationError) as excinfo:
        _normalise_agent_payload(fake_git_repo, payload)

    errors = excinfo.value.errors
    assert "topics[0].label is required" in errors
    assert "topics[0].refs[0].path must be a string" in errors
