"""Reviewable topic graph proposal generation.

This package deliberately writes proposal artifacts, not approved topics.
The provider (LangChain LLM or external agent) drafts the topic list
and wiki narrative; this package only orchestrates persistence and
review-state transitions.

Package layout — this module re-exports the public API so existing call
sites (`from lib.topics.proposals import X`) keep working unchanged:

  * _common.py        — small constants + dependency-free helpers
  * core_io.py        — create / load / save / list / delete + restore
                        and review-state transitions
  * topic_actions.py  — update / accept / replace / merge / ignore +
                        proposal → approved-graph converters
  * external_jobs.py  — background-thread proposal + regenerate runs
  * downgrade.py      — lift approved topic back into a proposal draft
  * feedback.py       — feedback thread + comment CRUD
"""

from __future__ import annotations

from ._common import (
    VALID_PROPOSAL_REVIEW_STATES,
    _find_proposed_topic,
    proposal_review_state,
)
from .apply_service import (
    apply_proposal_topic,
    diff_proposal_topic,
)
from .core_io import (
    backfill_disk_proposals_to_orm,
    create_proposal_run,
    delete_proposal_run,
    list_proposal_revisions,
    list_proposal_runs,
    load_proposal,
    load_proposal_revision,
    load_proposal_status,
    restore_proposal_to_revision,
    save_proposal,
    set_proposal_review_state,
    stop_proposal_run,
)
from .downgrade import (
    _restore_pruned_edges,
    downgrade_topic_to_proposal,
)
from .external_jobs import (
    regenerate_proposal_run,
    start_external_proposal_run,
    start_external_regenerate_run,
)
from .finish import finish_proposal_run
from .reap import reap_stranded_proposal_runs
from .feedback import (
    add_proposal_feedback_comment,
    create_proposal_feedback_thread,
    delete_proposal_feedback_comment,
    dismiss_content_drift_thread,
    list_proposal_feedback_threads,
    set_proposal_feedback_thread_resolution,
    update_proposal_feedback_comment,
)
from .topic_actions import (
    _approved_edges_from_proposal,
    _approved_refs_from_proposal,
    _approved_topic_from_proposal,
    accept_proposed_topic,
    ignore_proposed_topic,
    merge_proposed_topic,
    replace_approved_topic,
    update_proposed_topic,
)

__all__ = [
    # constants
    "VALID_PROPOSAL_REVIEW_STATES",
    # public API — load / save / list
    "create_proposal_run",
    "load_proposal",
    "load_proposal_revision",
    "load_proposal_status",
    "save_proposal",
    "set_proposal_review_state",
    "restore_proposal_to_revision",
    "list_proposal_revisions",
    "list_proposal_runs",
    "backfill_disk_proposals_to_orm",
    "delete_proposal_run",
    "stop_proposal_run",
    "proposal_review_state",
    # public API — diff/apply
    "diff_proposal_topic",
    "apply_proposal_topic",
    # public API — topic actions
    "update_proposed_topic",
    "accept_proposed_topic",
    "replace_approved_topic",
    "merge_proposed_topic",
    "ignore_proposed_topic",
    # public API — async jobs
    "start_external_proposal_run",
    "start_external_regenerate_run",
    "regenerate_proposal_run",
    "finish_proposal_run",
    "reap_stranded_proposal_runs",
    # public API — downgrade
    "downgrade_topic_to_proposal",
    # public API — feedback
    "list_proposal_feedback_threads",
    "create_proposal_feedback_thread",
    "add_proposal_feedback_comment",
    "update_proposal_feedback_comment",
    "delete_proposal_feedback_comment",
    "dismiss_content_drift_thread",
    "set_proposal_feedback_thread_resolution",
    # private helpers used by tests / blueprints / CLI directly
    "_approved_topic_from_proposal",
    "_approved_edges_from_proposal",
    "_approved_refs_from_proposal",
    "_find_proposed_topic",
    "_restore_pruned_edges",
]
