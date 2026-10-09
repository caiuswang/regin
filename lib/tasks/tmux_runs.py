"""Task runs launched in a detached tmux session — `launch=tmux`.

An SDK run is a child of `regin serve` and dies with it. A tmux run's CLI
lives on regin's own tmux server instead, so it keeps going across a server
restart — including one it triggers itself — and settles the way every run
does, through the agent's own `regin task finish`.

There is no watcher. Liveness is read when the server starts and when a
control acts, never on an interval, and is only ever GONE on tmux's own word:
"no server running" or "can't find session", matched literally. Anything else —
a missing binary, a timeout — is UNKNOWN, and an UNKNOWN run is left alone,
because condemning a run on a proxy signal reaps one that is still working.
"""

from __future__ import annotations

import base64
import mimetypes
import os
import subprocess
from pathlib import Path
from typing import Optional

from sqlmodel import select

from lib.activity_log import get_activity_logger
from lib.agent_bridge import attach, delivery, launch
from lib.orm import SessionLocal
from lib.orm.models.tasks import AgentTaskRun, RUN_OPEN_STATUSES
from lib.providers.registry import build_provider
from lib.settings import settings
from lib.tasks import store
from lib.tasks.arm import LAUNCH_TMUX

log = get_activity_logger("tasks")

LIVE, GONE, UNKNOWN = "live", "gone", "unknown"

GONE_REASON = "tmux session ended without a report"
STOPPED_REASON = "stopped"

_GONE_MARKERS = ("no server running", "can't find session")


class TmuxRunRefused(Exception):
    """The attempt could not be started in tmux; the message says why."""


def is_tmux(params: Optional[dict]) -> bool:
    return (params or {}).get("launch") == LAUNCH_TMUX


def session_name(task_id: int, attempt: int) -> str:
    return f"task-{task_id}-a{attempt}"


def _image_paths(run_id: int, images: list[dict]) -> list[str]:
    """The attempt's images written to disk: a pane's argv carries text only,
    so the agent is pointed at files it can read instead."""
    if not images:
        return []
    folder = settings.data_dir / "task-run-images" / str(run_id)
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for idx, image in enumerate(images):
        ext = mimetypes.guess_extension(image.get("media_type") or "") or ".bin"
        path = folder / f"{idx}{ext}"
        path.write_bytes(base64.b64decode(image["data_b64"]))
        paths.append(str(path))
    return paths


def _with_images(prompt: str, paths: list[str]) -> str:
    if not paths:
        return prompt
    listed = "\n".join(f"- {p}" for p in paths)
    return f"{prompt}\n\nImages attached to this task (read them):\n{listed}"


def _anchor(provider, cwd: str | None, env: dict) -> str:
    """The session id the pane will run under, settled before it starts —
    after the provider has readied the directory for an unattended start.

    Resolved, because the pane's process sees its cwd resolved: kimi refuses
    to resume a session created under `/tmp/x` from `/private/tmp/x`."""
    workdir = os.path.realpath(cwd or settings.project_root)
    try:
        provider.prepare_workspace(Path(workdir))
        return provider.pane_session_id(workdir, env)
    except Exception as exc:  # noqa: BLE001 — any failure refuses the launch
        raise TmuxRunRefused(
            f"no {provider.provider_id} session to start the pane on: "
            f"{exc}") from exc


def _pane_provider(row: dict):
    """The attempt's provider, refused unless it can start here in tmux."""
    provider = build_provider((row["params"] or {}).get("provider")
                              or "claude")
    caps = provider.launch_capabilities()
    if LAUNCH_TMUX not in caps["launch_modes"]:
        raise TmuxRunRefused(f"{provider.provider_id} has no tmux launch")
    reason = launch.detached_unavailable_reason(caps["binary"])
    if reason:
        raise TmuxRunRefused(reason)
    return provider


def _argv(provider, trace_id: str, kwargs: dict, prompt: str) -> list[str]:
    return provider.pane_argv(
        trace_id, model=kwargs.get("model") or "",
        effort=kwargs.get("effort") or "",
        permission_mode=kwargs.get("permission_mode") or "", prompt=prompt)


def launch_attempt(row: dict, prompt: str, kwargs: dict,
                   images: list[dict]) -> str:
    """Start `row`'s attempt in its own tmux session. Returns the trace id.

    The provider fixes the session id before the pane starts, so the id the
    attempt records is the id the session's own hooks report.
    """
    provider = _pane_provider(row)
    env = kwargs.get("env") or {}
    trace_id = _anchor(provider, kwargs.get("cwd"), env)
    text = _with_images(prompt, _image_paths(row["id"], images))
    typing = provider.pane_input()
    name = session_name(row["task_id"], row["attempt"])
    try:
        launch.spawn_detached(
            name, trace_id, cwd=kwargs.get("cwd"), env=env,
            argv=_argv(provider, trace_id, kwargs, "" if typing else text))
    except launch.TerminalLaunchRefused as exc:
        raise TmuxRunRefused(str(exc)) from exc
    if typing:
        sent = delivery.deliver_first_turn(trace_id, text, typing)
        if not sent.delivered:
            attach.release_session(name, trace_id)
            raise TmuxRunRefused(f"first turn not delivered: {sent.detail}")
    log.write("task_tmux_run_launched", run_id=row["id"],
              task_id=row["task_id"], trace_id=trace_id, session=name)
    return trace_id


def liveness(view: dict) -> str:
    """LIVE, GONE or UNKNOWN for the attempt's tmux session."""
    name = session_name(view["task_id"], view["attempt"])
    try:
        r = attach._tmux("has-session", "-t", f"={name}")
    except (OSError, subprocess.SubprocessError):
        return UNKNOWN
    if r.returncode == 0:
        return LIVE
    stderr = r.stderr or ""
    if any(marker in stderr for marker in _GONE_MARKERS):
        return GONE
    return UNKNOWN


def stop(view: dict) -> bool:
    """Kill the attempt's tmux session. Returns whether tmux killed one."""
    name = session_name(view["task_id"], view["attempt"])
    try:
        return attach.release_session(name, view["trace_id"] or "")
    except (OSError, subprocess.SubprocessError):
        log.error("task_tmux_stop_failed", run_id=view["id"], exc_info=True)
        return False


def end_own(view: dict) -> bool:
    """`stop`, called from inside the attempt's own pane."""
    name = session_name(view["task_id"], view["attempt"])
    try:
        return attach.release_own_session(name, view["trace_id"] or "")
    except (OSError, subprocess.SubprocessError):
        log.error("task_tmux_end_failed", run_id=view["id"], exc_info=True)
        return False


def interrupt(view: dict) -> bool:
    return delivery.deliver_key(view["trace_id"], "Escape").delivered


def open_attempts() -> list[dict]:
    """Every unsettled tmux attempt that got as far as a launch."""
    with SessionLocal() as session:
        rows = session.exec(
            select(AgentTaskRun)
            .where(AgentTaskRun.status.in_(RUN_OPEN_STATUSES),
                   AgentTaskRun.ended_at.is_(None),
                   AgentTaskRun.trace_id.is_not(None))).all()
        views = [{"id": r.id, "task_id": r.task_id, "attempt": r.attempt,
                  "trace_id": r.trace_id,
                  "params": store._decode_params(r.params_json)}
                 for r in rows]
    return [v for v in views if is_tmux(v["params"])]
