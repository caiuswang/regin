"""Rule-health classification used by the /trace/triggers tab.

A rule is one of:
- `dead`   — fires == 0 AND checks >= dead_min_checks.  It's been
             exercised enough times to draw a conclusion, and never
             matched. CTA: open the rule editor, relax or remove.
- `noisy`  — trigger rate >= noisy_min_rate_pct AND fires >=
             noisy_min_fires. The agent keeps tripping over it.
             CTA: tune the rule or train the agent.
- `active` — everything else, including the "unproven" zero-fires
             case where the rule hasn't been checked enough times
             yet to be called dead.

The compound `noisy_min_fires` gate exists because pure-percentage
classification breaks at low N (1/2 = 50% would otherwise flag a
brand-new rule as noisy on its second check).

Defined once here so the list endpoint, KPI aggregation, and any
future filter use the same logic — and the frontend never
re-implements it.
"""

from __future__ import annotations

from typing import Literal

from lib.settings import RuleTriggerThresholds

RuleStatus = Literal["active", "noisy", "dead"]


def classify_status(
    *,
    fires: int,
    checks: int,
    thresholds: RuleTriggerThresholds,
) -> RuleStatus:
    """Classify one rule based on its window-bounded fires and checks."""
    if checks >= thresholds.dead_min_checks and fires == 0:
        return "dead"
    rate_pct = (fires * 100 // checks) if checks > 0 else 0
    if (
        rate_pct >= thresholds.noisy_min_rate_pct
        and fires >= thresholds.noisy_min_fires
    ):
        return "noisy"
    return "active"


def trigger_rate_pct(fires: int, checks: int) -> int:
    """Integer percent (0–100). Zero-checks → 0. Used for sorting and display."""
    if checks <= 0:
        return 0
    return min(100, fires * 100 // checks)


__all__ = ["RuleStatus", "classify_status", "trigger_rate_pct"]
