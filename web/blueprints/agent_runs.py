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
from lib.agent_sdk import client, resumable, store, supervisor
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
# no session under that id, so it names an `agent_runs` row to resume *through*
# rather than a session id to hand the CLI.
_SYNTHETIC_PREFIX = 'sdk-'


class _BadRequest(ValueError):
    """A malformed launch payload — a 400, not a structured refusal."""


def _text(payload: dict, key: str, limit: int) -> str:
    value = payload.get(key)
    return value.strip()[:limit] if isinstance(value, str) else ""


def _refuse_if_live_in_terminal(session_id: str) -> None:
    """Refuse a session a terminal is still driving.

    The picker already hides these, so reaching this is a direct POST or a
    list that went stale between render and pick — the window is small but the
    outcome is not: two processes on one session id, and the live session's own
    composer redirected at the copy that just claimed its id.
    """
    from lib.agent_bridge import delivery

    if delivery.session_is_live(session_id):
        raise _BadRequest("that session is live in a terminal — exit it "
                          "before resuming")


def _resume_target(resume: str) -> tuple[str | None, str | None]:
    """`(cli session to resume, trace id to run it under)`.

    A stopped run regin launched is continued as *itself*: the CLI session
    behind it is reopened (`fork_session=False`, so the child keeps its id) and
    the run's own `sdk-…` id is reused, so one conversation stays one trace
    instead of splitting into a second session with no visible link to its
    first half. Either of the run's ids resolves it, because the id an operator
    arrives with is usually the child's — that is the one the session list and
    `/live` route on.

    An id regin has no record of is passed through untouched — that is a
    session the user drove in a terminal, whose trace id *is* the CLI's —
    unless that terminal is still driving it, which is refused for the same
    reason a live run is.
    """
    if not resume:
        return None, None
    from lib import agent_sdk

    row = store.find_run(resume)
    if row is None:
        if resume.startswith(_SYNTHETIC_PREFIX):
            raise _BadRequest(f"no run recorded for {resume}")
        _refuse_if_live_in_terminal(resume)
        return resume, None
    trace_id = row["trace_id"]
    if agent_sdk.is_starting(trace_id):
        raise _BadRequest("that run is starting — it cannot be stopped until "
                          "its session is up")
    if agent_sdk.is_sdk_owned(trace_id):
        raise _BadRequest("that run is still live — stop it before resuming")
    child = row.get("cli_session_id")
    if not child:
        raise _BadRequest("that run never reported a CLI session to resume")
    return child, trace_id


def _launch_params(payload: dict) -> dict:
    """Validated per-run overrides for `supervisor.launch`.

    `permission_mode` and `effort` are checked against the CLI's own vocabulary
    here rather than left for the SDK: an unknown value reaching the launch
    would surface as a run that died on start, which reads to the operator like
    regin broke. `model` is deliberately NOT checked — the sheet's menu is a
    convenience, not the set of models the install can serve.
    """
    mode = _text(payload, "permission_mode", _ID_MAX)
    if mode and mode not in client.PERMISSION_MODES:
        raise _BadRequest(f"unknown permission_mode {mode!r}")
    effort = _text(payload, "effort", _ID_MAX)
    if effort and effort not in client.EFFORT_LEVELS:
        raise _BadRequest(f"unknown effort {effort!r}")
    resume, trace_id = _resume_target(_text(payload, "resume", _ID_MAX))
    return {
        "cwd": _text(payload, "cwd", _PATH_MAX) or None,
        "model": _text(payload, "model", _ID_MAX),
        "effort": effort,
        "permission_mode": mode,
        "one_shot": bool(payload.get("one_shot")),
        "resume": resume,
        "trace_id": trace_id,
    }


def _require_prompt_only_for_one_shot(prompt: str, params: dict) -> None:
    """The prompt is optional — starting the session is itself an act.

    That is what a terminal does: `claude` with no argument comes up and waits,
    and the run launched from `/live` lands on a card with a composer, so
    demanding a first turn here would make an operator invent one. `one_shot`
    is the exception: a run told to end with its first turn and given no turn
    to run would connect and immediately disconnect, which is never what the
    caller meant.
    """
    if not prompt.strip() and params["one_shot"]:
        raise _BadRequest("a one-shot run needs a prompt — it ends with its "
                          "first turn")


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
        "models": list(client.MODEL_CHOICES),
        "default_model": cfg.model or "",
        "efforts": list(client.EFFORT_LEVELS),
        "default_effort": cfg.effort or "",
        "gating_active": bool(cfg.gate_plan or cfg.gated_tools),
    })


@agent_runs_bp.route('/api/agent-runs/resumable', methods=['GET'])
@require_editor
def api_resumable_sessions():
    """Sessions the launch sheet may offer to continue, newest first.

    Served rather than derived from `/api/sessions`: a trace row exists for
    every session regin ever saw, and only some of those can still be handed to
    `--resume` (see `lib.agent_sdk.resumable`). A client filtering the general
    session list would offer ids that die on launch.

    `q` searches title, id and cwd; it runs in SQL, so it reaches past the page.
    """
    limit = 30
    try:
        limit = max(1, min(int(request.args.get('limit', limit)), 100))
    except (TypeError, ValueError):
        pass  # a malformed limit falls back to the default rather than 400ing
    query = (request.args.get('q') or '').strip()[:_ID_MAX]
    return jsonify({"sessions": resumable.list_resumable(query, limit)})


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
    prompt = prompt if isinstance(prompt, str) else ""
    try:
        params = _launch_params(payload)
        _require_prompt_only_for_one_shot(prompt, params)
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


@agent_runs_bp.route('/api/agent-runs/<trace_id>/queue/<prompt_id>',
                     methods=['PATCH'])
@require_editor
def api_edit_queued_prompt(trace_id, prompt_id):
    """Rewrite a prompt still waiting behind this run's current turn.

    Only this tier has such a route because only this tier has such a queue: a
    session regin merely traces queues inside Claude Code, whose queue regin
    reads back from the transcript and cannot write to. Offering the control
    there would be a button that silently changes nothing.

    Structured refusal, never a 500 — a prompt whose turn started between the
    poll that rendered it and this request is an ordinary outcome, not an error.
    """
    from lib import agent_sdk

    payload = request.get_json(silent=True) or {}
    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return jsonify({"error": "prompt required"}), 400
    updated, detail = agent_sdk.edit_queued(trace_id, prompt_id,
                                            prompt[:_PROMPT_MAX])
    return jsonify({"updated": updated, "detail": detail})


@agent_runs_bp.route('/api/agent-runs/<trace_id>/queue/<prompt_id>',
                     methods=['DELETE'])
@require_editor
def api_cancel_queued_prompt(trace_id, prompt_id):
    """Drop a prompt still waiting behind this run's current turn. The turn in
    flight keeps running — cancelling that is `interrupt`."""
    from lib import agent_sdk

    removed, detail = agent_sdk.cancel_queued(trace_id, prompt_id)
    return jsonify({"removed": removed, "detail": detail})


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

    `resumable` is served derived rather than left to the client to infer from
    `cli_session_id`: the two conditions that rule a continuation out — a child
    that never named itself, and a run still live under this process — are the
    launch route's, and a client re-deriving them would drift from it.

    Answers for the child's session id as well as the run's own, since that is
    the id the session list offers and `/live` navigates to; the `trace_id` in
    the body is then the run's, which is what the launch route resumes under.
    """
    from lib import agent_sdk

    row = store.find_run(trace_id)
    if row is None:
        return jsonify({"error": "not found"}), 404
    owned = agent_sdk.is_sdk_owned(row["trace_id"])
    return jsonify({**row, "owned": owned,
                    "resumable": bool(row.get("cli_session_id")) and not owned})
