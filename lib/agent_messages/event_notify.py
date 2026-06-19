"""Push *interaction-required* events to the inbox + push channels.

Beyond agent-authored `send_to_user`, these are the moments the agent
halts for a human decision and you'd want to know out-of-band:

  * a pending **permission** prompt (Bash / file edit / WebFetch / …) or
    an **AskUserQuestion** — recorded as a `blocker`;
  * a **plan** ready for review on `ExitPlanMode` — recorded as a `warning`.

Each is opt-in (`settings.agent_messages.push_{permission,plan}_events`)
and routes through `store.record_message`, so it lands as an inbox card
*and* fans out through the configured push channels — exactly like
`send_to_user`, reusing one path. Both classes use a stable per-session
`msg_key`, so the inbox shows a single advancing "pending" card rather
than a stack, while each distinct prompt still pushes once.

Best-effort throughout: a notify failure must never break the hook that
called it — a permission prompt must still appear even if Telegram is down.
Hooks run as separate processes, so the only safe de-dup against a
double-firing event (PreToolUse + PermissionRequest for one prompt) is the
DB check in `_already_pushed`, not in-process state.
"""

from __future__ import annotations

from lib.activity_log import get_activity_logger
from lib.settings import settings

log = get_activity_logger("agent_messages")

_PERM_KEY = "permission-pending"
_PLAN_KEY = "plan-pending"
_PLAN_MAX = 1200


def _already_pushed(trace_id: str, key: str, body: str) -> bool:
    """True if the live keyed card already carries this exact body — so a
    double-firing event (or an unchanged re-prompt) doesn't push twice."""
    from lib.agent_messages import store
    live = store.live_keyed_message(trace_id, key)
    return live is not None and (live.get("body") or "") == body


def notify_permission_request(*, trace_id: str | None, attrs: dict) -> bool:
    """Surface a pending permission prompt. `attrs` is the dict built by
    `permission_events._build_perm_attrs` (tool_name, requested_permission,
    reason, option_count, questions, tool_use_id). Returns True if pushed.

    Whether the prompt actually awaits a human (vs. one the harness
    auto-resolves) is decided upstream by the provider before this is
    called — see `permission_events._maybe_notify_push`."""
    if not settings.agent_messages.push_permission_events or not trace_id:
        return False
    try:
        title, body = _format_permission(attrs)
        if _already_pushed(trace_id, _PERM_KEY, body):
            return False
        from lib.agent_messages import store
        store.record_message(
            trace_id=trace_id, body=body, msg_type="blocker", title=title,
            msg_key=_PERM_KEY, span_id=attrs.get("tool_use_id"))
        log.write("permission_event_pushed", trace_id=trace_id,
                  tool=attrs.get("tool_name"))
        return True
    except Exception:  # noqa: BLE001 — must never break the permission hook
        log.error("permission_event_push_failed", exc_info=True)
        return False


def resolve_permission(trace_id: str | None) -> None:
    """Dismiss the live permission card once the prompt is resolved
    (denied/answered), so a stale 'pending' card doesn't linger."""
    if not settings.agent_messages.push_permission_events or not trace_id:
        return
    try:
        from lib.agent_messages import store
        store.dismiss_keyed(trace_id, _PERM_KEY)
    except Exception:  # noqa: BLE001 — resolution is cosmetic
        log.error("permission_event_resolve_failed", exc_info=True)


def notify_plan_ready(*, trace_id: str | None, plan_text: str | None = None) -> bool:
    """Surface a plan ready for review (ExitPlanMode). Returns True if pushed."""
    if not settings.agent_messages.push_plan_events or not trace_id:
        return False
    try:
        body = _format_plan(plan_text)
        if _already_pushed(trace_id, _PLAN_KEY, body):
            return False
        from lib.agent_messages import store
        store.record_message(
            trace_id=trace_id, body=body, msg_type="warning",
            title="Plan ready for review", msg_key=_PLAN_KEY)
        log.write("plan_event_pushed", trace_id=trace_id)
        return True
    except Exception:  # noqa: BLE001 — must never break the plan hook
        log.error("plan_event_push_failed", exc_info=True)
        return False


# ── Body formatting ──────────────────────────────────────────

def _format_permission(attrs: dict) -> tuple[str, str]:
    """(title, body) for a pending permission / AskUserQuestion prompt."""
    questions = attrs.get("questions")
    if questions:
        return _format_question(questions[0])
    tool = attrs.get("tool_name") or "a tool"
    detail = attrs.get("requested_permission") or attrs.get("reason")
    lines = [detail] if detail else [f"The agent needs approval to run **{tool}**."]
    count = attrs.get("option_count")
    if count:
        lines.append(f"_{count} option(s) — approve or deny in your session._")
    return f"Permission needed: {tool}", "\n".join(lines)


def _format_question(q: dict) -> tuple[str, str]:
    """(title, body) for an AskUserQuestion permission prompt."""
    lines = [q.get("question") or "(question)"]
    for opt in (q.get("options") or []):
        label = opt.get("label") if isinstance(opt, dict) else str(opt)
        if label:
            lines.append(f"• {label}")
    return "The agent is asking you a question", "\n".join(lines)


def _format_plan(plan_text: str | None) -> str:
    if plan_text and plan_text.strip():
        text = plan_text.strip()
        if len(text) > _PLAN_MAX:
            text = text[:_PLAN_MAX] + "…"
        return text
    return ("The agent finished planning and is waiting for you to approve "
            "or reject the plan.")


__all__ = ["notify_permission_request", "resolve_permission", "notify_plan_ready"]
