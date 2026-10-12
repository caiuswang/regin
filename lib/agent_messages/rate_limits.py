"""Rate-limit alerts: one warning per window and threshold, across sessions.

Fed by `regin usage measure`, which the regin-meter mod runs with Claude
Code's `session.measure` input. A window is named by `(kind, resetsAt)`; the
same window read by two sessions is the same window, so the claim on a
threshold is a row in `rate_limit_alerts` and its unique constraint decides
who alerts. A window without `resetsAt` cannot be told apart from the next one
and never alerts.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from lib.activity_log import get_activity_logger
from lib.agent_messages.store import record_message
from lib.orm import SessionLocal
from lib.orm.models.agent_messages import RateLimitAlert
from lib.settings import settings

log = get_activity_logger("agent_messages")


class RateLimitWindow(BaseModel):
    kind: str
    percentUsed: float
    resetsAt: Optional[str] = None


class Measure(BaseModel):
    rateLimits: list[RateLimitWindow] = Field(default_factory=list)
    changed: list[str] = Field(default_factory=list)


def _claim(kind: str, resets_at: str, threshold: int, trace_id: str) -> bool:
    """Insert the alert row; False when any session already holds it."""
    with SessionLocal() as session:
        session.add(RateLimitAlert(kind=kind, resets_at=resets_at,
                                   threshold=threshold, trace_id=trace_id))
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            return False
    return True


def _title(window: RateLimitWindow) -> str:
    return (f"{window.kind} at {window.percentUsed:g}% · "
            f"resets {window.resetsAt}")


def _alert_window(trace_id: str, window: RateLimitWindow) -> Optional[dict]:
    crossed = sorted(t for t in settings.agent_messages.rate_limit_alert_at
                     if window.percentUsed >= t)
    if not window.resetsAt or not crossed:
        return None
    # Every crossed threshold is claimed, so a later reading between them
    # stays quiet; only a new claim on the highest one speaks.
    claimed = [t for t in crossed
               if _claim(window.kind, window.resetsAt, t, trace_id)]
    if crossed[-1] not in claimed:
        return None
    log.write("rate_limit_alerted", trace_id=trace_id, kind=window.kind,
              threshold=crossed[-1], resets_at=window.resetsAt)
    return record_message(
        trace_id=trace_id, msg_type="warning", title=_title(window),
        body=(f"The {window.kind} rate-limit window is {window.percentUsed:g}% "
              f"used (alert threshold {crossed[-1]}%). It resets at "
              f"{window.resetsAt}."))


def alert_crossings(trace_id: str, measure: Measure) -> list[dict]:
    """The warnings this measurement raised — at most one per window."""
    raised = (_alert_window(trace_id, w) for w in measure.rateLimits)
    return [m for m in raised if m]
