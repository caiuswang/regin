"""Declared event bus over the agent-message inbox.

Every *notifiable system event* — an agent blocked on a human, an external
LLM finishing a proposal, content drift detected, a session grade landing —
is declared once in `REGISTRY` and published through the single `emit`
function, which routes to `store.record_message` (the one writer). This
keeps producers uniform: no hand-rolled severity / key / deep-link / guard
per call site, and — the point of a *bus* — the full set of notifiable
events is enumerable. `regin events list` and `GET /api/events/kinds` read
the same registry, so "what can reach my inbox" is a single source of truth.

Enablement precedence for a kind: an explicit entry in
`settings.agent_messages.events` (`{kind: bool}`) wins; else the kind's
legacy boolean (the two interaction kinds honor `push_{permission,plan}_events`
so existing configs keep working); else the registry `default_enabled`.

Best-effort throughout: a notify must never break the producer that emitted
it, so `emit`/`resolve` swallow and log every failure.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from lib.activity_log import get_activity_logger
from lib.settings import settings

log = get_activity_logger("agent_messages")


@dataclass(frozen=True)
class EventKind:
    """One declared, notifiable event. `severity` is a value from
    `lib.orm.models.agent_messages.MESSAGE_TYPES` (drives inbox styling and
    the push severity gate); `summary` is the human blurb the catalog shows;
    `legacy_flag`, when set, is an `AgentMessagesConfig` boolean consulted as
    the enablement fallback for back-compat."""

    kind: str
    severity: str
    default_enabled: bool
    summary: str
    legacy_flag: Optional[str] = None


_KINDS: tuple[EventKind, ...] = (
    EventKind("permission.pending", "blocker", False,
              "Agent is blocked on a permission prompt or question.",
              legacy_flag="push_permission_events"),
    EventKind("plan.ready", "warning", False,
              "A plan is awaiting your approval (ExitPlanMode).",
              legacy_flag="push_plan_events"),
    EventKind("topic.suppress", "warning", True,
              "A routed topic crossed the fail-rate bar and is proposed for "
              "suppression."),
    EventKind("proposal.ready", "result", True,
              "An external agent finished drafting a topic proposal — review, "
              "apply, or regenerate it."),
    EventKind("content.drift", "warning", True,
              "A topic's wiki drifted from the code it documents; a refresh "
              "proposal is queued."),
    EventKind("grade.finished", "note", False,
              "A session grade finished."),
)

REGISTRY: dict[str, EventKind] = {k.kind: k for k in _KINDS}


def is_enabled(kind: str) -> bool:
    """Whether `kind` should currently notify (see the precedence in the
    module docstring). Unknown kinds are always disabled."""
    spec = REGISTRY.get(kind)
    if spec is None:
        return False
    overrides = getattr(settings.agent_messages, "events", None) or {}
    if kind in overrides:
        return bool(overrides[kind])
    if spec.legacy_flag:
        return bool(getattr(settings.agent_messages, spec.legacy_flag, False))
    return spec.default_enabled


def emit(kind: str, *, trace_id: Optional[str], body: str,
         title: Optional[str] = None, links=None, key: Optional[str] = None,
         severity: Optional[str] = None, span_id: Optional[str] = None,
         once: bool = False) -> Optional[dict]:
    """Publish one declared event to the inbox (and, past its severity gate,
    the push channels) through `store.record_message`. Returns the recorded
    row, or None when the kind is unknown, disabled, or `trace_id` is missing.

    `once=True` skips the write when a live (undismissed) keyed card already
    exists for this `(trace_id, key)` — for producers that must not
    re-surface a card the user hasn't acted on yet. `severity` overrides the
    kind's declared severity for this one emit.

    Best-effort: any failure is logged and swallowed so the producer's own
    path is never disturbed."""
    spec = REGISTRY.get(kind)
    if spec is None or not trace_id or not is_enabled(kind):
        return None
    try:
        from lib.agent_messages import store
        if once and key and store.live_keyed_message(trace_id, key) is not None:
            return None
        data = store.record_message(
            trace_id=trace_id, body=body, msg_type=severity or spec.severity,
            title=title, msg_key=key, links=links, span_id=span_id)
        log.write("event_emitted", kind=kind, trace_id=trace_id,
                  message_id=(data or {}).get("id"))
        return data
    except Exception:  # noqa: BLE001 — a notify must never break its producer
        log.error("event_emit_failed", kind=kind, exc_info=True)
        return None


def resolve(trace_id: Optional[str], key: str) -> None:
    """Dismiss the live keyed event card once its condition is handled
    (a prompt answered, a proposal applied). Best-effort — never raises."""
    if not trace_id or not key:
        return
    try:
        from lib.agent_messages import store
        store.dismiss_keyed(trace_id, key)
    except Exception:  # noqa: BLE001 — resolution is cosmetic
        log.error("event_resolve_failed", exc_info=True)


def catalog() -> list[dict]:
    """Every declared event kind with its current enablement — the data
    behind `regin events list` and `GET /api/events/kinds`."""
    return [{"kind": k.kind, "severity": k.severity,
             "default_enabled": k.default_enabled,
             "enabled": is_enabled(k.kind), "summary": k.summary}
            for k in _KINDS]


# ── Deep-link builders ───────────────────────────────────────
# The route paths mirror `frontend/src/router.js`; kept here so producers
# don't hard-code UI structure at every call site.

def topics_url(repo_path) -> str:
    """Deep-link to a repo's Topics / proposals view (`/repos/<name>/topics`),
    where a human applies or regenerates a proposal. `<name>` is the repo
    directory basename, matching the router's `:name` param."""
    return f"/repos/{os.path.basename(os.path.realpath(str(repo_path)))}/topics"


def session_url(trace_id: str) -> str:
    """Deep-link to a session's trace view (`/trace/sessions/<id>`)."""
    return f"/trace/sessions/{trace_id}"


__all__ = ["EventKind", "REGISTRY", "emit", "resolve", "is_enabled",
           "catalog", "topics_url", "session_url"]
