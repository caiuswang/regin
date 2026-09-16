"""Escapes — defects blamed on a feature that had already shipped (CAI-233).

The reading is deliberately built so that **no two clocks are ever compared**.
The obvious design — subtract a defect's report moment from the feature's first
completion — has operands minted by two different machines, and a defect near
the ship boundary would fall on either side of it according to host drift. The
metric would be measuring NTP quality as much as rework.

So the question is answered where it is answerable without subtraction: at the
instant a human declares which feature is to blame, the tracker is asked what
state that feature occupies, and the raw answer is frozen onto the defect row.
Both operands are declared at one instant, by one act.

The distortion this admits, stated so nobody rediscovers it as a bug: a defect
found while a feature was still building but blamed after it shipped counts as
an escape. Read the name as **blamed on an already-shipped feature**, which is
what it says.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from lib.activity_log import get_activity_logger
from lib.tasks.integrations.base import STATE_COMPLETED, STATE_UNKNOWN

log = get_activity_logger("defects")


def is_escape(state: Optional[str]) -> bool:
    """Whether a blame snapshot says the feature had already shipped.

    NULL is False and so is `'unknown'` — but neither is *counted* as a
    non-escape; both are gaps, and `counts_for` keeps them separate.
    """
    return state == STATE_COMPLETED


def blame_state(feature_key: str) -> Optional[str]:
    """The tracker's raw `state_category` for a feature, or None.

    Exactly one fetch, and a refusal returns None rather than a plausible
    category: losing one field beats losing the defect report to a network
    blip, and a guessed category would silently decide an escape.
    """
    from lib.defects import tracker  # noqa: PLC0415  (circular at import time)
    from lib.tasks.integrations import IntegrationError  # noqa: PLC0415
    from lib.tasks import issue_cache  # noqa: PLC0415
    try:
        provider, ref = tracker.resolve(feature_key)
        issue = provider.fetch_issue(ref)
    except (tracker.TrackerUnavailable, IntegrationError) as exc:
        log.read("blame_state_unavailable", feature_key=feature_key,
                 reason=str(exc))
        return None
    issue_cache.remember(ref.provider, ref.key, issue)
    state = getattr(issue, "state_category", None)
    log.read("blame_state_read", feature_key=feature_key, state=state)
    return state


@dataclass(frozen=True)
class EscapeCounts:
    """Escapes for one feature, each gap counted rather than absorbed."""

    escapes: int = 0
    #: Blame was assigned with no tracker reachable — we never looked.
    state_unknown: int = 0
    #: We looked, and the tracker answered with a word regin does not carry.
    unknown_vocabulary: int = 0


def counts_for(feature_key: str) -> EscapeCounts:
    """Escapes and their two gaps, read from the local ledger alone."""
    from lib.defects import store  # noqa: PLC0415  (circular at import time)
    rows = store.for_feature(feature_key)
    return EscapeCounts(
        escapes=sum(1 for r in rows if is_escape(r.blamed_against_state)),
        state_unknown=sum(1 for r in rows if r.blamed_against_state is None),
        unknown_vocabulary=sum(1 for r in rows
                               if r.blamed_against_state == STATE_UNKNOWN),
    )
