"""ORM-backed CRUD for `ProposalRun` and `ProposalTopic` rows.

The Phase E2 source-of-truth flip for proposals: `load_proposal`,
`save_proposal`, and `load_proposal_status` (in `lib/topics/proposals.py`)
now delegate here. The disk files (`topics.json`, `status.json`) under
`.regin/topics/proposals/<id>/` are dual-written for now — Phase E3 (or
a later cleanup) drops the disk writes once enough tooling treats the
ORM as authoritative.

Disk artefacts that remain unconditionally on disk (per the plan):

  - `evidence.json` — evidence pack (read-only after creation)
  - `wiki.md` — generated narrative
  - `instructions.md` — agent's launch instructions
  - `agent-output.json` — raw agent stdout
  - `stdout.log` / `stderr.log` — process logs

These are runtime artefacts; the proposal STATE (topics list, review
status, scope, provider) is the part that moves to ORM.

Package layout — this module re-exports the public `orm_*` symbols so
existing call sites (`from lib.topics.proposal_orm import orm_X`) keep
working unchanged:

  * _common.py     — Repo lookup + timestamp + activity log helpers
  * serializers.py — ORM row → dict converters + reverse kwargs builder
  * revisions.py   — revision chain + restore/downgrade revision appends
  * runs.py        — ProposalRun CRUD + orm_save_proposal write path
  * feedback.py    — feedback thread/comment CRUD + addressed sweep
"""

from __future__ import annotations

from .feedback import (
    MANUAL_RESOLUTION_STATES,
    orm_add_feedback_comment,
    orm_create_feedback_thread,
    orm_delete_feedback_comment,
    orm_list_feedback_threads,
    orm_mark_feedback_threads_addressed,
    orm_set_feedback_thread_resolution,
    orm_update_feedback_comment,
)
from .revisions import (
    orm_append_downgrade_revision,
    orm_list_proposal_revisions,
    orm_load_proposal_revision,
    orm_restore_proposal_to_revision,
)
from .runs import (
    orm_create_proposal_run,
    orm_delete_proposal_run,
    orm_find_origin_proposal_run_for_topic,
    orm_list_proposal_runs,
    orm_load_proposal,
    orm_load_proposal_status,
    orm_save_proposal,
    orm_unaccept_topic_across_proposals,
    orm_update_proposal_status,
)

__all__ = [
    "orm_create_proposal_run",
    "orm_load_proposal",
    "orm_load_proposal_revision",
    "orm_load_proposal_status",
    "orm_list_proposal_runs",
    "orm_list_proposal_revisions",
    "orm_list_feedback_threads",
    "orm_create_feedback_thread",
    "orm_add_feedback_comment",
    "orm_update_feedback_comment",
    "orm_delete_feedback_comment",
    "orm_set_feedback_thread_resolution",
    "orm_mark_feedback_threads_addressed",
    "MANUAL_RESOLUTION_STATES",
    "orm_append_downgrade_revision",
    "orm_find_origin_proposal_run_for_topic",
    "orm_restore_proposal_to_revision",
    "orm_save_proposal",
    "orm_update_proposal_status",
    "orm_unaccept_topic_across_proposals",
    "orm_delete_proposal_run",
]
