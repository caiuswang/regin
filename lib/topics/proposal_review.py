"""LLM-written review notes for proposal runs.

After a proposal run drafts (or regenerates) topics, an agentic reviewer
assesses the draft and writes a single ``review_note`` feedback thread carrying
a structured recommendation — ``REGENERATE`` / ``ACCEPT`` / ``DISMISS`` — plus
its reasons. The note rides the *existing* feedback-thread machinery, so it

* renders in the review sidebar like any human comment, and
* is carried into the next regenerate's drafting prompt by
  ``format_review_feedback_for_prompt`` (which only replays *open* threads).

Two ways the note is produced, mirroring the drafting agent's two ingest paths:

* **Async, notify-on-finish** (the automatic trigger). ``start_review_run``
  launches the reviewer *detached* — it never blocks the run-completion thread —
  and the reviewer reports back by running ``regin topics review-finish <id>``
  as its final step (``finish_review_note``). Because the finish command + the
  output path are baked *literally* into the prompt, a reviewer that gets stuck
  and is resumed by hand still has everything it needs to submit its verdict.
  This is the direct analogue of the drafting agent's ``proposal-finish``.
* **Synchronous, stdout-parsed** (``generate_review_note``). The manual, ungated
  entry (CLI ``review-note`` / web endpoint) where a human wants the note *now*
  and watches it happen — it blocks on the agent and parses the recommendation
  from stdout.

``maybe_generate_review_note`` is the gated trigger wired into the run-completion
paths: a no-op unless ``topic_evolution.auto_review_notes`` is set, and it now
*starts the async job* rather than blocking inline.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Optional

from lib.activity_log import get_activity_logger
from lib.settings import settings
from lib.topics import TopicGraphError, topic_dir

log = get_activity_logger("topics")

# Allowed recommendations, in the order we scan for them. ACCEPT is the
# neutral fallback when the reviewer doesn't emit a recognisable token.
RECOMMENDATIONS: tuple[str, ...] = ("REGENERATE", "DISMISS", "ACCEPT")
_DEFAULT_RECOMMENDATION = "ACCEPT"
_RECOMMENDATION_RE = re.compile(
    r"RECOMMENDATION\s*[:=]\s*(REGENERATE|ACCEPT|DISMISS)", re.IGNORECASE
)


def _open_feedback_lines(threads: list[dict[str, Any]]) -> str:
    """One bullet per still-open human thread, so the reviewer doesn't
    re-raise issues the user already flagged (and can note if a draft
    addressed them)."""
    lines: list[str] = []
    for thread in threads:
        if thread.get("resolution_state") != "open":
            continue
        comments = thread.get("comments") or []
        body = comments[0].get("body") if comments else thread.get("body")
        if body and str(body).strip():
            lines.append(f"- {str(body).strip()}")
    return "\n".join(lines)


def _topic_lines(proposal: dict[str, Any]) -> str:
    """One bullet per drafted topic (`- <id>: <intent>` + its ref paths) for the
    ``<draft_topics>`` block. Kept as a discrete builder so it can be passed as
    the ``topic_lines`` variable into the editable review skeleton."""
    lines: list[str] = []
    for topic in proposal.get("topics") or []:
        tid = topic.get("id", "?")
        refs = ", ".join(_format_ref(r) for r in topic.get("refs", []) if isinstance(r, dict))
        lines.append(f"- {tid}: {topic.get('intent', '')}\n  refs: {refs}")
    return "\n".join(lines)


def _format_ref(ref: dict[str, Any]) -> str:
    """`path`, annotated with `(tier=…)` for non-primary refs so the reviewer
    can judge whether each ref's tier matches how central the file actually is."""
    path = ref.get("path", "")
    tier = ref.get("tier")
    if tier and tier != "primary":
        return f"{path} (tier={tier})"
    return path


def _feedback_block(open_feedback: str) -> str:
    """The `<prior_open_feedback>` block, or "" when there is no open feedback."""
    if not open_feedback:
        return ""
    return f"<prior_open_feedback>\n{open_feedback}\n</prior_open_feedback>\n\n"


def _drafted_ids_and_buckets(proposal: dict[str, Any]) -> tuple[set[str], set[str]]:
    """The drafted topics' ids and the set of buckets (`parent_id`s) they target."""
    drafted: set[str] = set()
    buckets: set[str] = set()
    for t in proposal.get("topics") or []:
        if not isinstance(t, dict):
            continue
        drafted.add(t.get("id"))
        if t.get("parent_id"):
            buckets.add(t.get("parent_id"))
    return drafted, buckets


def _is_sibling(tid: str, topic: Any, drafted: set[str], buckets: set[str]) -> bool:
    """A non-bucket approved topic that shares one of the draft's buckets and
    isn't one of the drafted topics itself."""
    return (
        isinstance(topic, dict)
        and topic.get("kind") != "bucket"
        and tid not in drafted
        and topic.get("parent_id") in buckets
    )


def _sibling_lines(proposal: dict[str, Any], graph: dict[str, Any]) -> str:
    """One bullet per approved topic sharing a bucket with a drafted topic
    (the drafted topics themselves excluded), each with a pointer to its wiki
    page so the reviewer can open it and judge whether the draft restates it."""
    drafted, buckets = _drafted_ids_and_buckets(proposal)
    if not buckets:
        return ""
    lines: list[str] = []
    for tid, topic in sorted((graph.get("topics") or {}).items()):
        if not _is_sibling(tid, topic, drafted, buckets):
            continue
        intent = (topic.get("intent") or topic.get("blurb") or "").strip()
        lines.append(f"- {tid}: {intent}\n  wiki: .regin/topics/wiki/{tid}.md")
    return "\n".join(lines)


def _sibling_block(sibling_lines: str) -> str:
    """The `<sibling_topics>` block, or "" when the draft has no same-bucket
    approved neighbours to check against."""
    if not sibling_lines:
        return ""
    return (
        "<sibling_topics>\n"
        "Approved topics already under the same bucket(s) as this draft. The "
        "draft must complement, not restate, these — open their wiki pages to "
        f"check for overlap.\n{sibling_lines}\n"
        "</sibling_topics>\n\n"
    )


def _safe_graph(repo: Path) -> dict[str, Any]:
    """The authoritative graph, or {} — same-bucket siblings are best-effort
    context, never a reason to fail the review."""
    from lib.topics.graph_io import load_authoritative_graph
    try:
        return load_authoritative_graph(repo)
    except Exception:  # noqa: BLE001 - siblings are optional context
        return {}


def _review_context(repo: Path, proposal_id: str,
                    proposal: dict[str, Any]) -> tuple[str, str]:
    """The two runtime blocks shared by the sync and async prompt builders:
    the open-human-feedback bullets and the same-bucket sibling block."""
    from lib.topics.proposals import list_proposal_feedback_threads

    open_feedback = _open_feedback_lines(
        list_proposal_feedback_threads(repo, proposal_id)
    )
    sibling_block = _sibling_block(_sibling_lines(proposal, _safe_graph(repo)))
    return open_feedback, sibling_block


def _build_prompt(proposal: dict[str, Any], open_feedback: str,
                  sibling_block: str = "") -> str:
    """Build the reviewer's task prompt.

    The body is the editable ``topic-proposal-review`` surface
    (``lib/prompts/surfaces/review.py``); this function only assembles the
    runtime context it interpolates. A broken user edit degrades to the built-in
    default inside ``render_surface`` — the prompt is never left unbuildable.

    The async submit instructions are **not** interpolated here: they are
    appended by ``start_review_run`` *outside* ``render_surface`` (see
    ``_finish_block``), because ``render_surface`` prefers a stored/edited
    prompt row over the registry default — so a placeholder in the body would
    silently vanish on any install whose review prompt was seeded or edited
    before this feature. Appending keeps the mechanical hand-off unconditional.
    """
    from lib.prompts import render_surface
    from lib.prompts.surfaces.review import SURFACE_ID

    context = {
        "topic_lines": _topic_lines(proposal),
        "sibling_block": sibling_block,
        "feedback_block": _feedback_block(open_feedback),
    }
    return render_surface(SURFACE_ID, context)


def _parse_recommendation(answer: str) -> str:
    """Pull the recommendation token from the reviewer's text. Prefer the
    explicit ``RECOMMENDATION:`` line; else first token seen; else the neutral
    default. Never raises — a malformed answer still yields a usable note."""
    match = _RECOMMENDATION_RE.search(answer or "")
    if match:
        return match.group(1).upper()
    upper = (answer or "").upper()
    for token in RECOMMENDATIONS:
        if token in upper:
            return token
    return _DEFAULT_RECOMMENDATION


def _write_review_note(
    repo_path: str | Path, proposal_id: str, recommendation: str, answer: str,
    *, agent_trace_id: str | None = None,
) -> dict[str, Any]:
    """Persist one ``review_note`` feedback thread (``created_by='agent'``).
    The single writer shared by the synchronous and notify-on-finish paths, so
    both produce an identically-shaped note the UI badge + carry-forward rail
    already understand."""
    from lib.topics.proposals import create_proposal_feedback_thread

    body = (
        f"**Automated review — recommendation: {recommendation}**\n\n"
        f"{str(answer).strip()}"
    )
    # Structured copy of the recommendation so the UI badge reads a field
    # instead of regexing the LLM's prose body.
    metadata: dict[str, Any] = {"recommendation": recommendation}
    if agent_trace_id:
        metadata["agent_trace_id"] = agent_trace_id
        metadata["agent_trace_url"] = f"/trace/sessions/{agent_trace_id}"
    thread = create_proposal_feedback_thread(
        repo_path, proposal_id,
        proposal_topic_id=None,
        kind="review_note",
        anchor_kind="general",
        body=body,
        created_by="agent",
        metadata=metadata,
    )
    log.write("proposal_review_note_created", proposal_id=proposal_id,
              recommendation=recommendation, repo_path=str(repo_path))
    return thread


def generate_review_note(
    repo_path: str | Path, proposal_id: str, *, llm: Any = None,
) -> Optional[dict[str, Any]]:
    """Synchronously generate one review note and persist it as a
    ``review_note`` feedback thread (``created_by='agent'``). Blocks on the
    reviewer and parses the recommendation from its stdout. Returns the thread
    dict, or ``None`` when the proposal has no drafted topics or no external
    agent is configured (``complete`` → ``None``). Manual/ungated: callers
    decide whether to gate on ``auto_review_notes``."""
    from lib.topics.proposals import load_proposal

    repo = Path(repo_path)
    proposal = load_proposal(repo, proposal_id)
    if not proposal.get("topics"):
        return None

    open_feedback, sibling_block = _review_context(repo, proposal_id, proposal)
    if llm is None:
        from lib.memory.adapters import resolve_proposal_reviewer
        llm = resolve_proposal_reviewer()
    answer = llm.complete(
        _build_prompt(proposal, open_feedback, sibling_block),
        max_tokens=1024, cwd=repo_path,
    )
    if not answer or not str(answer).strip():
        log.write("proposal_review_note_skipped", proposal_id=proposal_id,
                  reason="no_llm_output", repo_path=str(repo_path))
        return None

    return _write_review_note(
        repo, proposal_id, _parse_recommendation(str(answer)), str(answer),
    )


# ───────────────────── notify-on-finish (async) ─────────────────────
#
# The review agent's analogue of the drafting agent's `proposal-finish`: the
# reviewer writes its review to a deterministic output file and signals back by
# running the finish command. Both artifact paths are derived only from
# (repo, proposal_id) so a manually-resumed session — which never inherits the
# launcher's env — reconstructs them identically from the literal values baked
# into its prompt.

_REVIEW_OUTPUT_FILE = "review-output.md"
_REVIEW_STATUS_FILE = "review-status.json"


def _review_dir(repo: Path, proposal_id: str) -> Path:
    return topic_dir(repo) / "proposals" / proposal_id


def _review_output_path(repo: Path, proposal_id: str) -> Path:
    return _review_dir(repo, proposal_id) / _REVIEW_OUTPUT_FILE


def _review_status_path(repo: Path, proposal_id: str) -> Path:
    return _review_dir(repo, proposal_id) / _REVIEW_STATUS_FILE


def _review_finish_command(repo: Path, proposal_id: str) -> str:
    """The exact shell command the review agent runs as its final step to
    submit its verdict. Built with the server's interpreter + regin CLI path
    and an explicit ``--repo`` so it works regardless of the target repo or
    whether ``regin`` is on the agent's PATH (mirror of ``_finish_command``)."""
    cli = settings.project_root / "cli" / "regin.py"
    parts = [
        sys.executable, str(cli), "topics", "review-finish",
        proposal_id, "--repo", str(repo),
    ]
    return " ".join(shlex.quote(part) for part in parts)


def _load_review_status(repo: Path, proposal_id: str) -> dict[str, Any]:
    path = _review_status_path(repo, proposal_id)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def _write_review_status(repo: Path, proposal_id: str, status: dict[str, Any]) -> None:
    path = _review_status_path(repo, proposal_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(status))


def _resolve_review_trace_id(source: str) -> str | None:
    """The review agent's own Claude Code session id, from the finish command's
    environment — but only on the ``agent`` path, where this runs in that
    agent's process. None otherwise (a server/test caller's session isn't the
    review session). Mirror of ``finish._resolve_agent_trace_id``."""
    if source != "agent":
        return None
    from lib.session_probe import resolve
    return resolve()


def _finish_block(repo: Path, proposal_id: str) -> str:
    """The ``<submit>`` block appended to the async reviewer's prompt: the
    literal output path + finish command, so even a resumed session (no
    inherited env) can complete the hand-back."""
    output_path = _review_output_path(repo, proposal_id)
    finish_cmd = _review_finish_command(repo, proposal_id)
    return (
        "\n\n<submit>\n"
        "Record your verdict — this is the ONLY way it reaches the server. As "
        "your final two steps:\n"
        "1. Write your full review (its last line the `RECOMMENDATION:` line "
        f"above) to this exact file:\n   {output_path}\n"
        "2. Then run this command to submit it:\n"
        f"   {finish_cmd}\n"
        "If your session is interrupted and later resumed, just complete these "
        "two steps — the path and command above still apply.\n"
        "</submit>"
    )


def finish_review_note(
    repo_path: str | Path, proposal_id: str, *, source: str = "agent",
) -> Optional[dict[str, Any]]:
    """Ingest the review agent's output file into a ``review_note`` thread on
    the agent's explicit signal (the notify-on-finish analogue of
    ``finish_proposal_run``).

    Reads the review the agent wrote to ``_review_output_path`` and persists it
    as one ``review_note`` thread. Idempotent: a second call after a successful
    ingest is a no-op (returns ``None``), so a resumed session re-running the
    command can't double-post. Raises ``TopicGraphError`` when signalled with no
    usable output, so the failure is visible and the agent can retry.
    """
    from lib.topics.proposals import load_proposal

    repo = Path(repo_path)
    status = _load_review_status(repo, proposal_id)
    if status.get("signaled"):
        log.read("review_finish_noop", proposal_id=proposal_id)
        return None

    proposal = load_proposal(repo, proposal_id)
    if not proposal.get("topics"):
        return None

    output_path = _review_output_path(repo, proposal_id)
    answer = output_path.read_text() if output_path.exists() else ""
    if not str(answer).strip():
        raise TopicGraphError(
            "review-finish: no review output found. Write your review to "
            f"{output_path} first, then re-run this same command."
        )

    recommendation = _parse_recommendation(str(answer))
    thread = _write_review_note(
        repo, proposal_id, recommendation, str(answer),
        agent_trace_id=_resolve_review_trace_id(source),
    )
    _write_review_status(repo, proposal_id, {
        **status, "state": "completed", "signaled": True, "signaled_by": source,
    })
    log.write("review_finish_ingested", proposal_id=proposal_id,
              source=source, recommendation=recommendation)
    return thread


def _review_agent_worker(repo: Path, proposal_id: str, spec: Any,
                         prompt: str) -> None:
    """Popen the reviewer, pipe the prompt to stdin, wait only as a timeout
    backstop. The verdict comes back out-of-band via ``review-finish`` — this
    worker never parses stdout, so a stuck-then-resumed reviewer still lands its
    note. Runs on a daemon thread; swallows its own errors (a review must never
    take down the run that spawned it)."""
    out_dir = _review_dir(repo, proposal_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "REGIN_TOPIC_REVIEW_ID": proposal_id,
        "REGIN_TOPIC_REVIEW_OUTPUT": str(_review_output_path(repo, proposal_id)),
        "REGIN_TOPIC_REVIEW_FINISH_CMD": _review_finish_command(repo, proposal_id),
    }
    if spec.surface_id:
        env["REGIN_LLM_SURFACE"] = spec.surface_id
    try:
        with open(out_dir / "review-stdout.log", "wb") as out, \
                open(out_dir / "review-stderr.log", "wb") as err:
            proc = subprocess.Popen(
                spec.argv, stdin=subprocess.PIPE, stdout=out, stderr=err,
                env=env, cwd=spec.cwd or str(repo),
            )
            try:
                proc.communicate(prompt.encode("utf-8"), timeout=spec.timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
    except Exception:  # noqa: BLE001 - a review spawn must never break the run
        log.error("review_agent_spawn_failed", proposal_id=proposal_id, exc_info=True)
        # Surface the failure in the status file (e.g. a misconfigured agent
        # command) rather than leaving the run to look merely un-reviewed.
        # Merge, not overwrite: if the agent already submitted out-of-band
        # (signaled=True) before a late communicate() error, keep that marker so
        # a hand-resume can't double-post.
        _write_review_status(
            repo, proposal_id,
            {**_load_review_status(repo, proposal_id), "state": "failed"},
        )
        return
    if not _load_review_status(repo, proposal_id).get("signaled"):
        # The agent exited without calling review-finish. Not an error: a
        # resumed session can still submit via the literal command in its prompt.
        log.write("review_agent_exited_unsignaled", proposal_id=proposal_id)


def _spawn_review_agent(repo: Path, proposal_id: str, spec: Any,
                        prompt: str) -> None:
    """Launch the reviewer on a detached daemon thread and return at once — the
    seam tests monkeypatch to simulate the agent without a real subprocess."""
    threading.Thread(
        target=_review_agent_worker,
        args=(repo, proposal_id, spec, prompt),
        daemon=True,
    ).start()


def start_review_run(
    repo_path: str | Path, proposal_id: str, *, spawn: Any = None,
) -> bool:
    """Launch the review agent *detached* (non-blocking) and return immediately.
    The reviewer submits its verdict later via ``review-finish``. Returns
    ``True`` iff a job was started; ``False`` when the proposal has no draft or
    no external agent is configured (nothing to launch)."""
    from lib.memory.adapters import resolve_proposal_reviewer
    from lib.topics.proposals import load_proposal

    repo = Path(repo_path)
    proposal = load_proposal(repo, proposal_id)
    if not proposal.get("topics"):
        return False
    spec = resolve_proposal_reviewer().spawn_spec()
    if spec is None:
        return False

    open_feedback, sibling_block = _review_context(repo, proposal_id, proposal)
    # Append the submit block *outside* render_surface so it is present even
    # when the review prompt has a stored/edited row (which render_surface
    # prefers over the registry default). See `_build_prompt`.
    prompt = (_build_prompt(proposal, open_feedback, sibling_block)
              + _finish_block(repo, proposal_id))
    # Reset per-launch state: drop a stale verdict file and clear the signaled
    # marker so this run's finish can land (a prior run may have signaled).
    _review_output_path(repo, proposal_id).unlink(missing_ok=True)
    _write_review_status(repo, proposal_id, {"state": "running", "signaled": False})
    (spawn or _spawn_review_agent)(repo, proposal_id, spec, prompt)
    log.write("review_run_started", proposal_id=proposal_id, repo_path=str(repo))
    return True


def maybe_generate_review_note(repo_path: str | Path, proposal_id: str) -> bool:
    """Gated, best-effort trigger for the run-completion paths. Starts the
    detached review job (returns ``True`` iff it started); a no-op (``False``)
    unless ``auto_review_notes`` is set. Never raises into the run thread — a
    review-launch failure must not fail the proposal run."""
    if not settings.topic_evolution.auto_review_notes:
        return False
    try:
        return start_review_run(repo_path, proposal_id)
    except Exception:  # noqa: BLE001 - a review note must never break the run
        log.error("proposal_review_note_failed", proposal_id=proposal_id,
                  exc_info=True)
        return False


__all__ = ["generate_review_note", "maybe_generate_review_note",
           "finish_review_note", "start_review_run", "RECOMMENDATIONS"]
