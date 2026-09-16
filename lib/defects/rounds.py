"""Rounds-to-done, read from declared state transitions (CAI-210).

**rounds = 1 + every completion that was later walked back** — one build, plus
each time the work was called done and then reopened.

Every input here is a fact somebody recorded: a tracker state the work occupied
and the moment it left. Nothing is inferred from prose, no clock is consulted,
and no proximity is assumed. That matters because rounds-to-done is the metric
the whole feature pipeline is judged against (`docs/feature-pipeline.md`), and
a metric fed by inference measures the inference as much as the work.

A completion that was *left* is the signal, not the completion itself. This is
also why the current row cannot be used: reopening an issue clears its
`completedAt`, so a feature reopened and re-completed looks, in the snapshot,
exactly like one that was right the first time. Verified against a live issue.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

# Tracker state categories that mean "this was called done". `canceled` is
# deliberately absent: abandoning work is not completing it, so walking back
# out of it says nothing about rework.
COMPLETED_TYPES = frozenset({"completed"})


def _is_abandoned_completion(entry: Any) -> bool:
    """A completed state the work later moved *out of* — one round."""
    if not isinstance(entry, dict):
        return False
    state = entry.get("state") or {}
    if state.get("type") not in COMPLETED_TYPES:
        return False
    return bool(entry.get("endedAt"))


def from_state_history(history: Optional[Iterable[Any]]) -> int:
    """Rounds implied by an ordered state history.

    An absent or unreadable history is **1**, never 0: the work was still
    built once, and reporting 0 would quietly understate a feature whose
    history we simply could not read.
    """
    if not history:
        return 1
    return 1 + sum(1 for entry in history if _is_abandoned_completion(entry))


def from_task(task: Any) -> int:
    """Rounds for one native task row: `1 + completions_walked_back`.

    Takes the **row**, not an id, so it adds no query and this module keeps
    importing nothing project-local. The count is never stored, only derived,
    which is what stops the number and the column it comes from drifting.

    `1` here means *built once, never walked back* — and on a local provider
    it can never also mean "we could not look", because a local read cannot
    fail the way that fear describes.
    """
    return 1 + int(getattr(task, "completions_walked_back", 0) or 0)


def from_issue(issue: Optional[dict]) -> int:
    """Rounds for one tracker issue payload."""
    if not issue:
        return 1
    return from_state_history(issue.get("stateHistory"))
