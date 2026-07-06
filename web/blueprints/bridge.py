"""Agent bridge — HTTP surface (human/system → live claude session).

Three routes for the inverse of the `send_to_user` inbox: push a steering
message into a running session's tmux pane (`POST`), and read the reachable
sessions / delivered messages back (`GET`). See `docs/agent-bridge-design.md`
(*Architecture*, *Security model*, *Slice 4*).

Auth is the `require_bridge_token` decorator ALONE: these endpoints join the
app's PUBLIC_API_ENDPOINTS allowlist (the JWT gate returns without auth for
them), so this bearer check — a constant-time compare against
`settings.agent_bridge.token`, a credential separate from the web-UI JWT — is
the sole guard. It fails closed: a disabled bridge 404s, and any missing /
mismatched / (crucially) unconfigured token 401s. Both GETs are guarded too;
the inbox exposes steering bodies.

One deliberate exception: `api_session_bridge_send` (the /live composer's
proxy) is NOT bridge-token-guarded and NOT in PUBLIC_API_ENDPOINTS — its
auth is the app-wide JWT gate plus `require_editor` (keystroke injection
into a live agent terminal outranks every editor-gated mutation, so viewer
JWTs are refused). It calls the same delivery layer in-process, so the
bridge bearer token never reaches the browser; that token remains the
credential for headless/external callers only.

The VIEW orchestrates record → deliver → mark; the store never calls delivery
(avoids the store→delivery import cycle).
"""

from __future__ import annotations

import hmac
import re
from functools import wraps

from flask import Blueprint, request, jsonify

from lib.auth import get_current_user, require_editor
from lib.settings import settings
from lib.agent_bridge import store, delivery, commands, ansi_html

bridge_bp = Blueprint('bridge', __name__)

# Sender is a short label (a phone name, a system id), never body-length
# free text — bound it hard so a rogue payload can't stuff the inbox row.
_SENDER_MAX = 200
_SENDER_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")


def _clip_sender(sender) -> str | None:
    """Strip control/newline bytes from `sender` and cap at 200 chars.

    None passes through as None (nullable column); anything else is coerced
    to str, cleaned, and clipped so an oversized/binary sender can't bloat or
    corrupt the rendered inbox row."""
    if sender is None:
        return None
    cleaned = _SENDER_CTRL_RE.sub("", str(sender))
    return cleaned[:_SENDER_MAX]


def require_bridge_token(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        cfg = settings.agent_bridge
        if not cfg.enabled:
            return jsonify({"error": "not found"}), 404
        configured = cfg.token or ""
        auth = request.headers.get("Authorization", "")
        presented = auth[7:] if auth.startswith("Bearer ") else ""
        # FAIL CLOSED: reject when no token is configured (an empty
        # compare_digest('', '') is True, so guard both sides explicitly).
        if (not configured or not presented
                or not hmac.compare_digest(presented, configured)):
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapper


def _resolve_trace_id(session_id: str) -> tuple[str, dict | None]:
    """Map the POST `session_id` to a delivery target.

    Returns (trace_id, refusal). For a concrete id the refusal is None.
    For the magic 'latest', resolve the most-recent reachable session; when
    none exists, return ("", {structured refusal}) so the caller records the
    attempt (trace_id="") and returns the structured no-delivery body — not a
    500 and not a blind deliver.
    """
    if session_id != "latest":
        return session_id, None
    latest = store.resolve_latest_trace_id()
    if latest is None:
        return "", {"delivered": False, "detail": "no reachable session"}
    return latest, None


@bridge_bp.route('/api/bridge/messages', methods=['POST'])
@require_bridge_token
def api_bridge_post_message():
    """Enqueue a steering message and attempt delivery; return the outcome."""
    payload = request.get_json(silent=True) or {}
    # Sanitize + cap the STORED body with the same rule delivery types with
    # (no ANSI/control bytes, flattened newlines, capped at max_text_len) so
    # the inbox row is bounded and safe to render — not just the typed copy.
    # Coerce non-string text to empty (a JSON number/list would raise inside
    # sanitize_text's re.sub → 500); it then fails the "text required" check.
    raw_text = payload.get("text")
    text = delivery.sanitize_text(raw_text if isinstance(raw_text, str) else "")
    session_id = payload.get("session_id")
    if not text:
        return jsonify({"error": "text required"}), 400
    if not isinstance(session_id, str) or not session_id:
        return jsonify({"error": "session_id required"}), 400
    sender = _clip_sender(payload.get("sender"))
    trace_id, refusal = _resolve_trace_id(session_id)
    row_id = store.record_bridge_message(trace_id, text, sender)
    if refusal is not None:
        store.mark_delivered(row_id, False, refusal["detail"])
        return jsonify({**refusal, "id": row_id})
    result = delivery.deliver(trace_id, text)
    store.mark_delivered(row_id, result.delivered, result.detail)
    return jsonify({"delivered": result.delivered,
                    "detail": result.detail, "id": row_id})


@bridge_bp.route('/api/sessions/<trace_id>/bridge-send', methods=['POST'])
@require_editor
def api_session_bridge_send(trace_id):
    """Web-JWT-authed proxy for the /live card's composer (editor+ only).

    Same record → deliver → mark orchestration as the bridge POST, called
    in-process (never an HTTP hop carrying the bridge token). Auth is the
    app-wide JWT gate plus `require_editor` — steering a live agent
    terminal outranks every editor-gated mutation, so viewers get 403.
    Deliberately absent from both PUBLIC_API_ENDPOINTS and the
    bridge-token decorator. A disabled bridge is a clean structured
    refusal, not a 404: the composer surfaces `detail` verbatim.
    """
    if not settings.agent_bridge.enabled:
        return jsonify({"delivered": False, "detail": "bridge disabled"})
    payload = request.get_json(silent=True) or {}
    raw_text = payload.get("text")
    text = delivery.sanitize_text(raw_text if isinstance(raw_text, str) else "")
    if not text:
        return jsonify({"error": "text required"}), 400
    user = get_current_user()
    sender = _clip_sender(f"web:{user['username']}" if user else "web")
    row_id = store.record_bridge_message(trace_id, text, sender)
    result = delivery.deliver(trace_id, text)
    store.mark_delivered(row_id, result.delivered, result.detail)
    return jsonify({"delivered": result.delivered,
                    "detail": result.detail, "id": row_id})


@bridge_bp.route('/api/sessions/<trace_id>/bridge-key', methods=['POST'])
@require_editor
def api_session_bridge_key(trace_id):
    """Inject an allowlisted control key (currently Escape) into the /live
    card's session — the composer's recovery affordance for a harness overlay
    that has swallowed typed input. Same JWT + `require_editor` gate as
    `bridge-send`; a keystroke into a live agent terminal outranks every
    editor-gated mutation, so viewers get 403. Not recorded in the steering
    inbox (a control keystroke is not a message); audited in the delivery log.
    A disabled bridge is a clean structured refusal, not a 404.
    """
    if not settings.agent_bridge.enabled:
        return jsonify({"delivered": False, "detail": "bridge disabled"})
    payload = request.get_json(silent=True) or {}
    key = payload.get("key")
    if not isinstance(key, str) or not key:
        return jsonify({"error": "key required"}), 400
    result = delivery.deliver_key(trace_id, key)
    return jsonify({"delivered": result.delivered, "detail": result.detail})


def _parse_answer(payload: dict):
    """(option_index, text, body) from an answer payload, or (None, ...) when
    `option_index` is missing/invalid. `text` is the sanitized free-form answer
    (None when absent); `body` is the human-readable line recorded in the inbox
    (the label, else the text, else the option ordinal)."""
    option_index = payload.get("option_index")
    if not isinstance(option_index, int) or option_index < 0:
        return None, None, None
    raw_text = payload.get("text")
    text = (delivery.sanitize_text(raw_text)
            if isinstance(raw_text, str) and raw_text.strip() else None)
    raw_label = payload.get("label")
    label = delivery.sanitize_text(raw_label) if isinstance(raw_label, str) else ""
    return option_index, text, (text or label or f"option {option_index + 1}")


@bridge_bp.route('/api/sessions/<trace_id>/bridge-answer', methods=['POST'])
@require_editor
def api_session_bridge_answer(trace_id):
    """Answer a pending AskUserQuestion in the /live card's session by driving
    its select TUI (editor+ only). `option_index` (0-based) picks a listed
    option; an optional `text` selects the auto-appended "Type something."
    entry at that index and types a free-form answer. Same JWT + `require_editor`
    gate as `bridge-send` — driving a live agent terminal outranks every
    editor-gated mutation, so viewers get 403. The human-readable answer
    (`label`, else the free text) is recorded in the steering inbox for audit,
    mirroring `bridge-send`. A disabled bridge is a clean structured refusal.
    """
    if not settings.agent_bridge.enabled:
        return jsonify({"delivered": False, "detail": "bridge disabled"})
    option_index, text, body = _parse_answer(request.get_json(silent=True) or {})
    if option_index is None:
        return jsonify({"error": "option_index required"}), 400
    user = get_current_user()
    sender = _clip_sender(f"web:{user['username']}" if user else "web")
    row_id = store.record_bridge_message(trace_id, body, sender)
    result = delivery.deliver_answer(trace_id, option_index, text)
    store.mark_delivered(row_id, result.delivered, result.detail)
    return jsonify({"delivered": result.delivered,
                    "detail": result.detail, "id": row_id})


@bridge_bp.route('/api/sessions/<trace_id>/bridge-commands', methods=['GET'])
@require_editor
def api_session_bridge_commands(trace_id):
    """The /live composer's `/`-autocomplete accept list (editor+ only).

    The slash commands + skills the target session would accept, enumerated
    from its own project `.claude/` (resolved via the pane registry's cwd)
    plus `~/.claude/`. Same JWT + `require_editor` gate as `bridge-send`;
    read-only and fail-closed — any error collapses to `{"commands": []}` so
    the composer just shows no menu, never an error. A disabled bridge is the
    same clean structured refusal the sibling routes return.
    """
    if not settings.agent_bridge.enabled:
        return jsonify({"commands": [], "detail": "bridge disabled"})
    try:
        rows = commands.list_session_commands(trace_id)
    except Exception:  # noqa: BLE001 — read-only convenience list, never 500
        rows = []
    return jsonify({"commands": rows})


@bridge_bp.route('/api/sessions/<trace_id>/bridge-screen', methods=['GET'])
@require_editor
def api_session_bridge_screen(trace_id):
    """One-shot raw terminal snapshot for the /live card's terminal-peek
    panel (editor+ only). Read-only: `capture_screen()` reuses the same
    reachability/identity guards as `deliver()` but never types or sends
    Enter. Gated editor+ like every other bridge-* route, not just viewer,
    because a raw screen can show things the parsed trace already redacts
    or never surfaced. Default is a recent PAGE of scrollback (200 lines,
    same idiom as `_await_pane_text`'s capture window) — not just the
    pane's bare current screen, which turned out to cut off too much
    useful recent history. The panel opens scrolled to the BOTTOM of
    whatever comes back (`LiveTerminalSheet.vue`), so the live status line
    is still what you see first; scrolling up reveals the rest. `?lines=N`
    overrides the depth (capped at 2000); a bad/missing value falls back
    to the 200-line default. `html` is `text` run through
    `ansi_html.convert()` so the panel can render it directly with no
    client-side escape parsing.
    """
    try:
        lines = max(1, min(int(request.args.get("lines", 200)), 2000))
    except (TypeError, ValueError):
        lines = 200
    result = delivery.capture_screen(trace_id, lines=lines)
    return jsonify({"ok": result.ok,
                    "html": ansi_html.convert(result.text) if result.ok else "",
                    "detail": result.detail})


@bridge_bp.route('/api/bridge/sessions', methods=['GET'])
@require_bridge_token
def api_bridge_list_sessions():
    """Live bridge-reachable sessions from the registry."""
    return jsonify(store.list_reachable_sessions())


@bridge_bp.route('/api/bridge/messages', methods=['GET'])
@require_bridge_token
def api_bridge_list_messages():
    """Inbox rows with delivery status; optional ?session_id / ?limit."""
    session_id = request.args.get("session_id")
    try:
        # Floor AND cap: a negative limit is an unlimited SQLite LIMIT (full
        # inbox dump), so clamp into [1, 200]; non-int falls back to 50.
        limit = max(1, min(int(request.args.get("limit", 50)), 200))
    except (TypeError, ValueError):
        limit = 50
    return jsonify(store.list_bridge_messages(session_id, limit))
