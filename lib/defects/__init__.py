"""Defect records and rounds-to-done (CAI-206).

A defect is something a feature got wrong, addressed by a **feature key** — the
tracker issue identifier, which also roots the branch name, so it is stable
from the first day of the spec and survives rebases, branch deletion and the
session boundary.

**Rounds-to-done** for a feature is `1 + every completion the tracker recorded
and later walked back` — one build, plus each time the work was called done and
reopened. It is read from the tracker alone (`lib.defects.tracker`), because
only a declared state transition witnesses a completion. `Defect.round` is a
different number entirely: fix attempts on one defect, never aggregated to a
feature.

An **escape** is a defect blamed on a feature that had already shipped, read
from `blamed_against_state` without comparing any two clocks
(`lib.defects.escapes`). It is reported beside rounds and never folded into it.

Writes go through `lib.defects.store`; nothing else should touch the table.
"""

from lib.defects.store import DefectError

__all__ = ["DefectError", "escapes", "rounds", "store", "tracker"]

from lib.defects import escapes, rounds, store, tracker  # noqa: E402  (after DefectError)
