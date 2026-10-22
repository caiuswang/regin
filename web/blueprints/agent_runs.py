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

from pathlib import Path

from flask import Blueprint, request, jsonify

from lib.auth import get_current_user, require_editor
from lib.agent_bridge import launch as terminal_launch
from lib.agent_sdk import client, resumable, store, supervisor, worktree
from lib.providers import registry as provider_registry
from lib.providers.base import provider_for_trace
from lib.settings import settings
from lib.tasks import skill_catalog, slots
from lib import workspaces

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
# D1 of #140: a launch that names no provider runs claude, whatever the
# install's active provider is.
_DEFAULT_PROVIDER = 'claude'
_PROVIDER_FIELDS = ("model", "permission_mode", "effort")


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
    child = _resumed_child(resume, row)
    if not child:
        raise _BadRequest("that run never reported a CLI session to resume")
    # A run's child can be reopened in a terminal pane, so a stopped run is no
    # proof its conversation is idle.
    _refuse_if_live_in_terminal(child)
    return child, trace_id


def _resumed_child(resume: str, row: dict) -> str | None:
    """Which of a run's conversations "resume this run" means.

    The id the operator arrived with, whenever that is itself one of the run's
    children — `find_run` resolved the row through the alias group, so any id
    that is not the run's own `sdk-…` is one. `agent_runs.cli_session_id` pins
    the FIRST child forever by design, so reading the column instead reopens
    the conversation they left behind and strands the one they were reading:
    it is neither the live child (no composer) nor resumable (its run is now
    live), which renders as a page with no controls at all.

    The run's own id names no conversation, so it takes the newest child.
    """
    if resume != row["trace_id"] and not resume.startswith(_SYNTHETIC_PREFIX):
        return resume
    return store.latest_cli_session(row["trace_id"])


def _launch_provider(payload: dict):
    """The provider this launch runs on.

    A resume runs on the CLI that recorded the session, never on the active
    provider: only that CLI can reopen it. Naming a different one is refused
    rather than overridden, so a body never quietly means something else.
    """
    named = _text(payload, "provider", _ID_MAX)
    if named and named not in provider_registry.list_provider_ids():
        raise _BadRequest(f"unknown provider {named!r}")
    resume = _text(payload, "resume", _ID_MAX)
    if not resume:
        return provider_registry.build_provider(named or _DEFAULT_PROVIDER)
    own = provider_for_trace(resume)
    if named and named != own.provider_id:
        raise _BadRequest(f"that session was recorded by {own.provider_id} — "
                          f"it resumes on {own.provider_id}, not {named}")
    return own


def _refuse_undeclared(payload: dict, provider) -> None:
    """Invariant 4: a field the provider does not declare is a 400 naming it,
    not a value dropped on the way to a CLI that has no such setting."""
    caps = provider.launch_capabilities()
    if not caps["launch_modes"]:
        raise _BadRequest(f"{provider.provider_id} has no launch channel")
    for field in _PROVIDER_FIELDS:
        if _text(payload, field, _ID_MAX) and field not in caps["fields"]:
            raise _BadRequest(
                f"{provider.provider_id} does not accept {field}")


def _channel_resume_target(resume: str) -> tuple[str | None, None]:
    """A channel session resumes under its own id — there is no `sdk-…` run
    to continue through — so the refusals are only the live ones."""
    if not resume:
        return None, None
    from lib import agent_sdk

    if agent_sdk.is_sdk_owned(resume):
        raise _BadRequest("that session is still live — stop it before "
                          "resuming")
    _refuse_if_live_in_terminal(resume)
    return resume, None


def _launch_params(payload: dict, provider=None) -> dict:
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
    channel = provider is not None and provider.channel_launcher() is not None
    target = _channel_resume_target if channel else _resume_target
    resume, trace_id = target(_text(payload, "resume", _ID_MAX))
    return {
        "cwd": _text(payload, "cwd", _PATH_MAX) or None,
        "model": _text(payload, "model", _ID_MAX),
        "effort": effort,
        "permission_mode": mode,
        "one_shot": bool(payload.get("one_shot")),
        "resume": resume,
        "trace_id": trace_id,
    }


def _worktree_spec(payload: dict) -> dict | None:
    """The requested worktree, or `None` for a run in the cwd as it stands.

    Keyed off the branch rather than a separate flag: a worktree with no branch
    named is not a request this route can serve, so the branch *is* the ask.
    """
    spec = payload.get("worktree")
    if not isinstance(spec, dict):
        return None
    raw = spec.get("branch")
    branch = raw.strip() if isinstance(raw, str) else ""
    if not branch:
        return None
    # Refused rather than clipped like the other identifiers here: this string
    # is not looked up, it is *created*, so a silently shortened one would put
    # the operator on a branch nobody named — and leave the one they asked for
    # nowhere. An over-long base is safe to clip by contrast: it resolves to no
    # ref and comes back as "unknown base ref".
    if len(branch) > _ID_MAX:
        raise _BadRequest(f"branch name is longer than {_ID_MAX} characters")
    return {"branch": branch, "base": _text(spec, "base", _ID_MAX)}


def _worktree_cwd(spec: dict | None, params: dict) -> dict | None:
    """Create the requested worktree and point the launch at it.

    Refused alongside a resume because `claude --resume` resolves the session
    id relative to the working directory: a continuation started in a tree the
    session has never run in finds nothing to continue, and would come up as a
    silent fresh session instead of an error.

    The anchor for an unset `cwd` is the install's own project root — the same
    directory the run itself would have inherited from the server process, so
    the worktree lands beside the repo the operator was implicitly launching in.
    """
    if spec is None:
        return None
    if params["resume"]:
        raise _BadRequest("a resumed session cannot move into a new worktree — "
                          "it resolves its id against the directory it ran in")
    anchor = params["cwd"] or str(settings.project_root)
    created = worktree.ensure_worktree(anchor, spec["branch"], spec["base"])
    params["cwd"] = created["path"]
    return created


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
    terminal_reason = terminal_launch.unavailable_reason(_caller_role())
    return jsonify({
        "enabled": bool(cfg.enabled),
        # `repo_paths` are `Path`s, which jsonify cannot serialize.
        "cwds": [str(path) for path in (settings.repo_paths or [])],
        "workspaces": workspaces.launch_choices(),
        "permission_modes": list(client.PERMISSION_MODES),
        "default_permission_mode": cfg.permission_mode or "default",
        "models": list(client.MODEL_CHOICES),
        "default_model": cfg.model or "",
        "efforts": list(client.EFFORT_LEVELS),
        "default_effort": cfg.effort or "",
        "gating_active": bool(cfg.gate_plan or cfg.gated_tools),
        "terminal_available": not terminal_reason,
        "terminal_detail": terminal_reason,
        "detached_detail": terminal_launch.detached_unavailable_reason(),
        "providers": provider_registry.launch_provider_rows(),
    })


@agent_runs_bp.route('/api/agent-runs/slots', methods=['GET'])
@require_editor
def api_run_slots():
    """`RunSlots`: who holds each `max_concurrent_runs` slot, so a refused
    Run can name what to close instead of only saying the pool is full."""
    return jsonify(slots.run_slots())


@agent_runs_bp.route('/api/agent-runs/worktree-options', methods=['GET'])
@require_editor
def api_worktree_options():
    """The branches and worktree root for one working directory.

    Keyed on `cwd` rather than served with the rest of the launch options
    because it is per-repo: the sheet's directory menu spans every registered
    repo, and each answers this differently. A directory that is not a git
    checkout is a 400 with the reason — the form disables its worktree section
    on it rather than offering branches it made up.
    """
    cwd = (request.args.get('cwd') or '').strip()[:_PATH_MAX]
    try:
        return jsonify(worktree.options(cwd or str(settings.project_root)))
    except worktree.WorktreeRefused as exc:
        return jsonify({"error": str(exc)}), 400


@agent_runs_bp.route('/api/agent-runs/skill-options', methods=['GET'])
@require_editor
def api_skill_options():
    """The skills a run in `cwd` can open with — per-repo, like the worktree
    options, because a skill one repo carries is a dead slash command in
    another, and per-provider, because each CLI reads its own dirs. An empty
    `cwd` is the server working directory and an empty `provider` is claude,
    as at launch."""
    cwd = (request.args.get('cwd') or '').strip()[:_PATH_MAX]
    cwd = cwd or str(settings.project_root)
    provider = (request.args.get('provider') or '').strip() or "claude"
    try:
        skills = skill_catalog.available_skills(cwd, provider=provider)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"cwd": cwd, "provider": provider, "skills": skills})


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


def _caller_role() -> str | None:
    user = get_current_user()
    return user.get("role") if user else None


def _refuse_sdk_only_fields(payload: dict) -> None:
    """An interactive TUI has no one-shot mode — refused rather than ignored,
    so a launch never quietly means less than the form asked for.

    The pane is claude's TUI, so a terminal launch on any other provider is
    refused too rather than opened as claude (D3 of #140)."""
    if payload.get("one_shot"):
        raise _BadRequest("one_shot is not supported for terminal sessions")
    provider = _launch_provider(payload)
    if provider.provider_id != _DEFAULT_PROVIDER:
        raise _BadRequest(f"terminal sessions launch {_DEFAULT_PROVIDER} "
                          f"only, not {provider.provider_id}")


def _launch_terminal(payload: dict, prompt: str):
    """The `kind: "terminal"` arm: an interactive claude on a regin-owned pane.

    Gated by the bridge and terminal switches plus the admin role, not by
    `agent_sdk.enabled` — the two kinds share a form, not a capability. The
    availability refusal comes before any field is looked at, so a disabled
    install creates no worktree either.
    """
    reason = terminal_launch.unavailable_reason(_caller_role())
    if reason:
        return jsonify({"launched": False, "detail": reason})
    try:
        _refuse_sdk_only_fields(payload)
        params = _launch_params(payload)
        created_worktree = _worktree_cwd(_worktree_spec(payload), params)
    except (_BadRequest, worktree.WorktreeRefused) as exc:
        return jsonify({"error": str(exc)}), 400
    made = {"worktree": created_worktree} if created_worktree else {}
    try:
        launched = terminal_launch.launch_terminal(
            role=_caller_role(), cwd=params["cwd"], model=params["model"],
            effort=params["effort"], permission_mode=params["permission_mode"],
            prompt=prompt.strip()[:_PROMPT_MAX], resume=params["resume"] or "")
    except terminal_launch.TerminalLaunchRefused as exc:
        return jsonify({"launched": False, "detail": str(exc), **made})
    return jsonify({"launched": True, "trace_id": launched.trace_id,
                    "kind": "terminal", **made})


@agent_runs_bp.route('/api/agent-runs', methods=['POST'])
@require_editor
def api_launch_agent_run():
    """Start a session and return its trace id.

    Returns as soon as the run is scheduled, so the caller can open `/live` and
    watch it — including answering the questions it asks.
    """
    payload = request.get_json(silent=True)
    payload = payload if isinstance(payload, dict) else {}
    kind = payload.get("kind") or "sdk"
    if kind not in ("sdk", "terminal"):
        return jsonify({"error": f"unknown kind {kind!r}"}), 400
    prompt = payload.get("prompt")
    prompt = prompt if isinstance(prompt, str) else ""
    if kind == "terminal":
        return _launch_terminal(payload, prompt)
    return _launch_sdk(payload, prompt)


def _launch_sdk(payload: dict, prompt: str):
    if not settings.agent_sdk.enabled:
        return jsonify({"launched": False, "detail": "agent_sdk disabled"})
    try:
        provider = _launch_provider(payload)
        _refuse_undeclared(payload, provider)
        params = _launch_params(payload, provider)
        _require_prompt_only_for_one_shot(prompt, params)
        created_worktree = _worktree_cwd(_worktree_spec(payload), params)
    except (_BadRequest, worktree.WorktreeRefused) as exc:
        return jsonify({"error": str(exc)}), 400
    # Reported on the refusal paths as well as the success one: the tree is
    # already on disk by now, and an operator told only "refused" would go
    # looking for a directory nothing mentioned. A retry reuses it.
    made = {"worktree": created_worktree} if created_worktree else {}
    runner = provider.channel_launcher()
    if runner is not None:
        return _launch_on_channel(provider, runner, prompt, params, made)
    try:
        trace_id = supervisor.launch(prompt[:_PROMPT_MAX], **params)
    except supervisor.LaunchRefused as exc:
        return jsonify({"launched": False, "detail": str(exc), **made})
    except ImportError:
        return jsonify({
            "launched": False,
            "detail": 'claude-agent-sdk not installed (pip install -e ".[agent-sdk]")',
            **made,
        })
    body = {"launched": True, "trace_id": trace_id, **made}
    # A mode the operator picked can make gating inert for this run alone, which
    # the settings-keyed report at launch would not have caught.
    warning = settings.agent_sdk.shadowed_gating(params["permission_mode"])
    if warning:
        body["warning"] = warning
    return jsonify(body)


def _launch_on_channel(provider, runner, prompt: str, params: dict,
                       made: dict):
    """The structured-channel arm (kimi over ACP). The workspace is readied
    first (D8): kimi would otherwise stop a fresh directory on its Trust
    prompt with nobody at the terminal to answer it."""
    try:
        provider.prepare_workspace(
            Path(params["cwd"] or settings.project_root).resolve())
        trace_id = supervisor.launch_channel(
            runner, prompt[:_PROMPT_MAX], cwd=params["cwd"],
            model=params["model"], one_shot=params["one_shot"],
            permission_mode=params["permission_mode"],
            resume=params["resume"])
    except Exception as exc:  # noqa: BLE001 — each channel raises its own refusal
        return jsonify({"launched": False, "detail": str(exc), **made})
    return jsonify({"launched": True, "trace_id": trace_id,
                    "provider": provider.provider_id, **made})


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
    flight keeps running — cancelling that is `interrupt`.

    A `b<row>` id names a bridge-tier steer chip instead (a delivered tmux
    steer the transcript never settled). Dismissing one only retires the chip
    — the keystrokes were already typed into the pane and cannot be recalled,
    which is why this arm offers no matching edit."""
    from lib import agent_sdk

    if prompt_id.startswith('b') and prompt_id[1:].isdigit():
        from lib.agent_bridge import store as bridge_store
        removed = bridge_store.dismiss_steer(trace_id, int(prompt_id[1:]))
        detail = ("chip dismissed" if removed
                  else "that steer is no longer pending")
        return jsonify({"removed": removed, "detail": detail})
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
