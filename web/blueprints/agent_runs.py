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
from lib.agent_sdk import client, store, supervisor
from lib.settings import settings

agent_runs_bp = Blueprint('agent_runs', __name__)

# A prompt is a message, not a payload — bound it so a stray body can't be
# shipped into an agent turn.
_PROMPT_MAX = 8000
# Identifiers, not prose: a model name or a session id arriving longer than this
# is a malformed request, and both are passed to a child process.
_ID_MAX = 200
_PATH_MAX = 2000
# regin's own name for a run it launched (`supervisor.launch_run`). The CLI has
# no session under that id, so resuming one could only fail at start.
_SYNTHETIC_PREFIX = 'sdk-'


class _BadRequest(ValueError):
    """A malformed launch payload — a 400, not a structured refusal."""


def _text(payload: dict, key: str, limit: int) -> str:
    value = payload.get(key)
    return value.strip()[:limit] if isinstance(value, str) else ""


def _launch_params(payload: dict) -> dict:
    """Validated per-run overrides for `supervisor.launch`.

    `permission_mode` is checked against the CLI's own vocabulary here rather
    than left for the SDK: an unknown mode reaching the launch would surface as
    a run that died on start, which reads to the operator like regin broke.
    """
    mode = _text(payload, "permission_mode", _ID_MAX)
    if mode and mode not in client.PERMISSION_MODES:
        raise _BadRequest(f"unknown permission_mode {mode!r}")
    resume = _text(payload, "resume", _ID_MAX)
    if resume.startswith(_SYNTHETIC_PREFIX):
        raise _BadRequest("a regin-launched run has no CLI session to resume")
    return {
        "cwd": _text(payload, "cwd", _PATH_MAX) or None,
        "model": _text(payload, "model", _ID_MAX),
        "permission_mode": mode,
        "one_shot": bool(payload.get("one_shot")),
        "resume": resume or None,
    }


@agent_runs_bp.route('/api/agent-runs/launch-options', methods=['GET'])
@require_editor
def api_launch_options():
    """What the `/live` launch sheet may offer.

    Served rather than hardcoded in the client: the working directories are the
    operator's registered repos and the modes are the CLI's contract, so a
    client guessing either would drift from the install it is driving.
    """
    cfg = settings.agent_sdk
    return jsonify({
        "enabled": bool(cfg.enabled),
        # `repo_paths` are `Path`s, which jsonify cannot serialize.
        "cwds": [str(path) for path in (settings.repo_paths or [])],
        "permission_modes": list(client.PERMISSION_MODES),
        "default_permission_mode": cfg.permission_mode or "default",
        "default_model": cfg.model or "",
        "gating_active": bool(cfg.gate_plan or cfg.gated_tools),
    })


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
    try:
        params = _launch_params(payload)
    except _BadRequest as exc:
        return jsonify({"error": str(exc)}), 400
    try:
        trace_id = supervisor.launch(prompt[:_PROMPT_MAX], **params)
    except supervisor.LaunchRefused as exc:
        return jsonify({"launched": False, "detail": str(exc)})
    except ImportError:
        return jsonify({
            "launched": False,
            "detail": 'claude-agent-sdk not installed (pip install -e ".[agent-sdk]")',
        })
    body = {"launched": True, "trace_id": trace_id}
    # A mode the operator picked can make gating inert for this run alone, which
    # the settings-keyed report at launch would not have caught.
    warning = settings.agent_sdk.shadowed_gating(params["permission_mode"])
    if warning:
        body["warning"] = warning
    return jsonify(body)


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
