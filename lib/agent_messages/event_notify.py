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

log = get_activity_logger("agent_messages")

PERM_KEY = "permission-pending"
PLAN_KEY = "plan-pending"

# The `msg_key`s that mark a card as a decision the agent is *parked* on
# rather than a report. Mirrored by `DECISION_KEYS` in
# frontend/src/constants/inboxTypes.js and pinned by
# `test_decision_keys_match_the_client`.
DECISION_KEYS = (PERM_KEY, PLAN_KEY)

_PLAN_MAX = 1200

# `resolve_answered` matches a span-less card back to its tool by this title,
# so the emit- and resolve-side strings must be the same constant.
_ASK_TITLE = "The agent is asking you a question"


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
    from lib.agent_messages import events
    if not trace_id or not events.is_enabled("permission.pending"):
        return False
    try:
        title, body = _format_permission(attrs)
        if _already_pushed(trace_id, PERM_KEY, body):
            return False
        data = events.emit(
            "permission.pending", trace_id=trace_id, body=body, title=title,
            key=PERM_KEY, span_id=attrs.get("tool_use_id"))
        return data is not None
    except Exception:  # noqa: BLE001 — must never break the permission hook
        log.error("permission_event_push_failed", exc_info=True)
        return False


def resolve_permission(trace_id: str | None) -> None:
    """Dismiss the live permission card once the prompt is resolved
    (denied/answered), so a stale 'pending' card doesn't linger."""
    from lib.agent_messages import events
    if not trace_id or not events.is_enabled("permission.pending"):
        return
    events.resolve(trace_id, PERM_KEY)


def resolve_plan(trace_id: str | None) -> None:
    """Dismiss the live plan card once the plan was decided.

    Not gated on `is_enabled`: a live card is its own evidence the event was
    enabled when it fired, and disabling the kind must not strand the card."""
    from lib.agent_messages import events
    if not trace_id:
        return
    events.resolve(trace_id, PLAN_KEY)


def resolve_answered(*, trace_id: str | None, tool_name: str | None,
                     tool_use_id: str | None = None) -> None:
    """Retire decision cards this completed tool call has just answered.

    The granting path: a denial fires PermissionDenied (handled elsewhere),
    so PostToolUse for the gated tool means the human approved it — in their
    own terminal as often as from the web. Matching is deliberately narrow:

      * a card that names its call retires only on that exact `tool_use_id`;
      * a span-less card (the PermissionRequest payload usually carries no
        `tool_use_id`) retires on the completing tool's name, via the title
        `_format_permission` stamped at emit time — the only durable record
        of which tool the card was about;
      * the plan card retires on any completion EXCEPT `ExitPlanMode`, which
        is the very event that raises it.

    A same-named tool completing in a parallel subagent while the prompt is
    still parked can false-retire the span-less case; that window is narrow,
    and the alternative — cards that outlive their answer by days — drowns
    the real blockers.
    """
    if not trace_id or not tool_name:
        return
    try:
        from lib.agent_messages import events, store
        live = store.live_decision_messages(trace_id)
        perm = live.get(PERM_KEY)
        if perm is not None and _card_matches_call(perm, tool_name, tool_use_id):
            events.resolve(trace_id, PERM_KEY)
        if PLAN_KEY in live and tool_name != "ExitPlanMode":
            events.resolve(trace_id, PLAN_KEY)
    except Exception:  # noqa: BLE001 — must never break the PostToolUse hook
        log.error("decision_resolve_failed", exc_info=True)


def resolve_on_prompt(trace_id: str | None) -> None:
    """Retire every decision card when the user submits a new prompt.

    A prompt cannot be typed while a permission menu or plan approval holds
    the terminal, so its submission proves every earlier prompt was decided.
    """
    if not trace_id:
        return
    try:
        from lib.agent_messages import events, store
        for key in store.live_decision_messages(trace_id):
            events.resolve(trace_id, key)
    except Exception:  # noqa: BLE001 — must never break the prompt hook
        log.error("decision_resolve_failed", exc_info=True)


def _card_matches_call(card: dict, tool_name: str,
                       tool_use_id: str | None) -> bool:
    if card.get("span_id"):
        return bool(tool_use_id) and card["span_id"] == tool_use_id
    title = card.get("title") or ""
    if tool_name == "AskUserQuestion":
        return title == _ASK_TITLE
    return title == f"Permission needed: {tool_name}"


def notify_plan_ready(*, trace_id: str | None, plan_text: str | None = None) -> bool:
    """Surface a plan ready for review (ExitPlanMode). Returns True if pushed."""
    from lib.agent_messages import events
    if not trace_id or not events.is_enabled("plan.ready"):
        return False
    try:
        body = _format_plan(plan_text)
        if _already_pushed(trace_id, PLAN_KEY, body):
            return False
        data = events.emit(
            "plan.ready", trace_id=trace_id, body=body,
            title="Plan ready for review", key=PLAN_KEY)
        return data is not None
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
        # Plain text, not `_italics_`: this body is rendered verbatim by the
        # blocker banner as well as by the markdown push channels, and the
        # underscores were reaching the operator as literal characters.
        lines.append(f"{count} option(s) — approve or deny in your session.")
    return f"Permission needed: {tool}", "\n".join(lines)


def _format_question(q: dict) -> tuple[str, str]:
    """(title, body) for an AskUserQuestion permission prompt."""
    lines = [q.get("question") or "(question)"]
    for opt in (q.get("options") or []):
        label = opt.get("label") if isinstance(opt, dict) else str(opt)
        if label:
            lines.append(f"• {label}")
    return _ASK_TITLE, "\n".join(lines)


def _format_plan(plan_text: str | None) -> str:
    if plan_text and plan_text.strip():
        text = plan_text.strip()
        if len(text) > _PLAN_MAX:
            text = text[:_PLAN_MAX] + "…"
        return text
    return ("The agent finished planning and is waiting for you to approve "
            "or reject the plan.")


__all__ = ["DECISION_KEYS", "PERM_KEY", "PLAN_KEY", "notify_permission_request",
           "resolve_permission", "resolve_plan", "resolve_answered",
           "resolve_on_prompt", "notify_plan_ready"]
