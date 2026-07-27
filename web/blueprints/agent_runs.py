"""Launch and inspect regin-owned agent sessions (`lib/agent_sdk`).

Launching runs the agent **in this process**, deliberately. The registry that
holds a parked `AskUserQuestion` is process-local, so a runner started anywhere
else could not be answered from `/live` — the route resolving the question and
the loop holding it have to be the same process.

`require_editor` matches the bridge's session-scoped routes: starting an agent
with the user's own credentials and working tree outranks every editor-gated
mutation, so viewers get 403. A disabled tier is a clean structured refusal,
not a 404, mirroring `bridge-send`.
"""

from __future__ import annotations

from flask import Blueprint, request, jsonify

from lib.auth import require_editor
from lib.agent_sdk import store, supervisor
from lib.settings import settings

agent_runs_bp = Blueprint('agent_runs', __name__)

# A prompt is a message, not a payload — bound it so a stray body can't be
# shipped into an agent turn.
_PROMPT_MAX = 8000


@agent_runs_bp.route('/api/agent-runs', methods=['POST'])
@require_editor
def api_launch_agent_run():
    """Start a session and return its trace id.

    Returns as soon as the run is scheduled, so the caller can open `/live` and
    watch it — including answering the questions it asks.
    """
    if not settings.agent_sdk.enabled:
        return jsonify({"launched": False, "detail": "agent_sdk disabled"})
    payload = request.get_json(silent=True) or {}
    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return jsonify({"error": "prompt required"}), 400
    cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) else None
    try:
        trace_id = supervisor.launch(prompt[:_PROMPT_MAX], cwd=cwd)
    except supervisor.LaunchRefused as exc:
        return jsonify({"launched": False, "detail": str(exc)})
    except ImportError:
        return jsonify({
            "launched": False,
            "detail": 'claude-agent-sdk not installed (pip install -e ".[agent-sdk]")',
        })
    return jsonify({"launched": True, "trace_id": trace_id})


@agent_runs_bp.route('/api/agent-runs/<trace_id>/prompt', methods=['POST'])
@require_editor
def api_prompt_agent_run(trace_id):
    """Send a follow-up prompt to a run this process owns.

    This is what makes a launched run a session rather than a one-shot: the
    prompt is queued on the runner's input queue and picked up when the turn in
    flight ends. Refusals are structured (`queued: false` + a reason), never a
    500 — a session that already exited is an ordinary outcome for a phone that
    still has the card open.
    """
    payload = request.get_json(silent=True) or {}
    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return jsonify({"error": "prompt required"}), 400
    queued, detail = supervisor.send_prompt(trace_id, prompt[:_PROMPT_MAX])
    return jsonify({"queued": queued, "detail": detail})


@agent_runs_bp.route('/api/agent-runs/<trace_id>/interrupt', methods=['POST'])
@require_editor
def api_interrupt_agent_run(trace_id):
    """Cancel the turn in flight. The session stays open for the next prompt —
    stopping it is a separate, explicit act."""
    from lib import agent_sdk

    delivered, detail = agent_sdk.interrupt_run(trace_id)
    return jsonify({"delivered": delivered, "detail": detail})


@agent_runs_bp.route('/api/agent-runs/<trace_id>/stop', methods=['POST'])
@require_editor
def api_stop_agent_run(trace_id):
    """End the session: the runner finishes its current turn, emits
    `session.end` and disconnects. Asynchronous by design — the stored row
    advances to `exited` when teardown actually completes, not when it's asked
    for."""
    from lib import agent_sdk

    delivered, detail = agent_sdk.stop_run(trace_id)
    return jsonify({"delivered": delivered, "detail": detail})


@agent_runs_bp.route('/api/agent-runs/<trace_id>', methods=['GET'])
@require_editor
def api_get_agent_run(trace_id):
    """Run status, plus whether this process still holds a live channel.

    `owned` is the routing fact, not `status`: a runner killed with the server
    leaves a `running` row behind, so the row records intent while `owned`
    records reachability.
    """
    from lib import agent_sdk

    row = store.get_run(trace_id)
    if row is None:
        return jsonify({"error": "not found"}), 404
    return jsonify({**row, "owned": agent_sdk.is_sdk_owned(trace_id)})
