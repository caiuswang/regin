"""The one figure this pipeline is judged on: how often a person had to type.

Invariant 7 states the whole rule — **a session counts as human exactly when
no `agent_task_runs` row produced it.** The population is decided by identity,
never by reading prose for intent and never by comparing clocks: a slow agent
and a thinking person look identical in a duration, and a chatty run would
otherwise inflate the count it is supposed to measure.

Like cost, it is an **upper bound**: attribution binds a whole session, and a
console session that specced two features carries one prompt count. Splitting
it would invent a ratio the data does not hold.

The reading lives here rather than on the ledger because the baseline it is
compared against (feature 73's) is a CLI question asked of one feature id, and
routing it through the full ledger build would price a session list and a
coverage sweep to answer it.
"""

from __future__ import annotations

from lib.features import attribution, cost

__all__ = ["HumanPrompts", "human_prompts"]

HumanPrompts = cost.HumanPrompts


def human_prompts(feature_id: int) -> HumanPrompts:
    """The human-prompt reading for one feature."""
    attributed = attribution.sessions_for_feature(feature_id)
    facts = cost.facts_for(row.trace_id for row in attributed)
    return cost.human_prompts(attributed, facts)
