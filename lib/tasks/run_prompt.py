"""What a task run is launched with: the prompt text assembled from the task,
its feature, spec, tracker and follow-ups, plus the launch kwargs and images.
"""

from __future__ import annotations

import shlex
import sys
from typing import Optional

from lib.agent_sdk import worktree
from lib.comments import store as comments
from lib.orm.models.comments import RESOLUTION_OPEN, SUBJECT_TASK
from lib.orm.models.tasks import TASK_KIND_SPEC
from lib.refs.grammar import RefError, format_label, parse_refs
from lib.settings import settings
from lib.tasks import images
from lib.tasks.integrations import registry


PROMPT_MAX = 8000


# A comment is already capped at `COMMENT_BODY_MAX`, but the number of threads
# is not, so an unresolved conversation could otherwise crowd out the task.
FOLLOWUP_MAX = 8000


REFERENCES_MAX = 20


_LAUNCH_DEFAULTS: dict = {"cwd": None, "model": "", "effort": "",
                          "permission_mode": "", "one_shot": False, "env": {}}


def _finish_command(run_id: int) -> str:
    """The exact shell command the agent runs to report its work finished.

    Built from the server's own interpreter and CLI path rather than a bare
    `regin`, so it works in a worktree, under any cwd, and whether or not the
    agent's PATH has regin on it — the same reason
    `topics.proposal_external._finish_command` builds its own.
    """
    cli = settings.project_root / "cli" / "regin.py"
    parts = [sys.executable, str(cli), "task", "finish", str(run_id)]
    return " ".join(shlex.quote(part) for part in parts)


def _cli_command(*parts: str) -> str:
    """A `regin <…>` command as the prompt must write it — the server's own
    interpreter and CLI path, for the same reason `_finish_command` does it:
    the agent's cwd is a worktree and its PATH may not carry regin at all."""
    cli = settings.project_root / "cli" / "regin.py"
    argv = [sys.executable, str(cli), *parts]
    return " ".join(shlex.quote(part) for part in argv)


def _open_fix_defects(task_id: int) -> list[int]:
    """Open defects that name this task as the work that closes them."""
    from lib.defects import store as defect_store  # noqa: PLC0415
    from lib.orm.models.defects import STATUS_OPEN  # noqa: PLC0415

    return sorted(row.id for row in defect_store.by_fix_item(task_id)
                  if row.status == STATUS_OPEN)


def _close_defect_command(defect_id: int) -> str:
    return (_cli_command("defects", "close", str(defect_id), "--check")
            + " '<the check that would have caught it>'")


def _defects_named(ids: list[int]) -> str:
    refs = ", ".join(f"#{i}" for i in ids)
    return f"defect {refs}" if len(ids) == 1 else f"defects {refs}"


def _defects_order(task_id: Optional[int]) -> str:
    """The close step a fix task's run must take before it finishes.

    Closing is the agent's act, not a side effect of the finish: `close`
    demands the check that would have caught the bug, which no status
    transition can supply. So the run is handed the exact command, and
    `finish_run` refuses a done report while the defect is still open.
    """
    from lib.prompts import render_surface  # noqa: PLC0415
    from lib.prompts.surfaces.tasks import (  # noqa: PLC0415
        FINISH_ORDER_DEFECTS_SURFACE_ID)

    ids = _open_fix_defects(task_id) if task_id is not None else []
    if not ids:
        return ""
    return "\n" + render_surface(FINISH_ORDER_DEFECTS_SURFACE_ID, {
        "defects": _defects_named(ids),
        "close_cmds": "\n".join(f"   {_close_defect_command(i)}"
                                for i in ids)})


def _finish_block(run_id: int, linked: bool = False,
                  base: str = "", task_id: Optional[int] = None) -> str:
    """The `<finish>` instruction appended to a run's launch prompt.

    The command is written out literally, not left to an env var, so a session
    resumed later — with none of the launch's environment — can still report.

    A linked run is additionally told to update its issue *first*. The signal
    is what makes regin re-read the tracker, so an agent that reports before
    it records leaves regin holding the state from before its own work. An
    unlinked run is told nothing about a tracker: there is no issue to update,
    and the instruction would be noise it has to resolve.

    A native run is told to merge its slice back first instead. Its work only
    reaches the rest of the feature through that merge, so a run reporting
    finished on an unmerged branch releases its dependents onto a base that
    does not carry it. The base branch is named when the run was armed with
    one; without it the instruction still stands, unnamed.
    """
    from lib.prompts import render_surface  # noqa: PLC0415
    from lib.prompts.surfaces.tasks import (  # noqa: PLC0415
        FINISH_BASE_UNNAMED_SURFACE_ID, FINISH_ORDER_LINKED_SURFACE_ID,
        FINISH_ORDER_NATIVE_SURFACE_ID, FINISH_SURFACE_ID)

    if linked:
        order = render_surface(FINISH_ORDER_LINKED_SURFACE_ID, {})
    else:
        target = (f"`{base}`" if base
                  else render_surface(FINISH_BASE_UNNAMED_SURFACE_ID, {}))
        order = render_surface(FINISH_ORDER_NATIVE_SURFACE_ID,
                               {"target": target})
    order += _defects_order(task_id)
    return "\n\n" + render_surface(
        FINISH_SURFACE_ID,
        {"order": order, "finish_cmd": _finish_command(run_id)})


def _tracker_block(feature) -> str:
    """The `<tracker>` instruction for a run of a remotely-tracked feature.

    Identity, not content: the issue's description is deliberately not
    repeated, because the feature already carries it as its spec and a second
    copy is either duplication or a stale contradiction of one. The agent that
    needs the body reads it where it is current.

    It hangs off the FEATURE rather than off the task, which is what the
    redesign moved: a link used to live on a task, so which issue a run
    belonged to depended on which of a feature's tasks launched it.

    Empty — not an empty block — for a native feature, so its run's prompt is
    exactly what it was.
    """
    if feature is None or not feature.external_ref:
        return ""
    from lib.prompts import render_surface  # noqa: PLC0415
    from lib.prompts.surfaces.tasks import TRACKER_SURFACE_ID  # noqa: PLC0415

    return "\n\n" + render_surface(TRACKER_SURFACE_ID, {
        "tracker_name": registry.display_name(feature.origin),
        "external_ref": feature.external_ref,
        "external_url": feature.external_url or ""})


def _followup_rows(task_id: int) -> list[tuple[int, dict]]:
    """Every comment on this task's unresolved threads, oldest first.

    Only this task's own threads. A parent's comments are addressed to the
    feature, and a child's run is one concrete piece of work — threading the
    feature's whole discussion through every round is how a prompt rots.

    Chronological across threads and within them, because an appended
    instruction corrects the ones before it and no other order preserves that.
    """
    threads = [row for row
               in comments.thread_with_comments(SUBJECT_TASK, str(task_id))
               if row["resolution_state"] == RESOLUTION_OPEN]
    threads.sort(key=lambda row: (row["created_at"] or "", row["id"]))
    return [(thread["id"], comment)
            for thread in threads
            for comment in sorted(thread["comments"],
                                  key=lambda row: (row["created_at"] or "",
                                                   row["id"]))]


def _followup_line(thread_id: int, comment: dict, previous) -> str:
    """One comment, labelled by who wrote it, with a blank line where one
    conversation ends and the next begins.

    The thread id is on every line, not just the first of a run: it is the
    handle the reply command takes, and a run whose oldest comments were
    dropped to fit could otherwise be shown a continuation line with no id
    anywhere above it.
    """
    gap = "\n" if previous not in (None, thread_id) else ""
    return (f"{gap}  [thread {thread_id} · {comment['author_kind']}] "
            f"{comment['body'].strip()}\n")


def _followup_lines(rows: list[tuple[int, dict]]) -> tuple[list[str], int]:
    """The rendered comments that fit in `FOLLOWUP_MAX`, and how many were
    dropped to make them fit.

    The oldest go first — a later comment corrects an earlier one, so losing
    the correction is the one loss that changes the instruction. The newest is
    kept unconditionally even when it alone overruns: a bound that can deliver
    nothing is worse than one that overshoots by one comment.
    """
    lines: list[str] = []
    previous = None
    for thread_id, comment in rows:
        lines.append(_followup_line(thread_id, comment, previous))
        previous = thread_id
    dropped = 0
    while len(lines) > 1 and sum(len(line) for line in lines) > FOLLOWUP_MAX:
        lines.pop(0)
        dropped += 1
    return lines, dropped


def _followup_block(task_id: int) -> str:
    """The `<followup>` instruction: what was said on the task after it ran.

    This is the only thing `resolution_state` has ever done. A thread stays
    here on every run until somebody resolves it — delivery does not consume
    it, because an instruction the agent has not acted on yet is not an
    instruction it has dealt with, and regin cannot tell the two apart.

    Empty — not an empty block — when nothing is open, so a run of a task
    nobody commented on carries the prompt it always did.
    """
    lines, dropped = _followup_lines(_followup_rows(task_id))
    if not lines:
        return ""
    from lib.prompts import render_surface  # noqa: PLC0415
    from lib.prompts.surfaces.tasks import (  # noqa: PLC0415
        FOLLOWUP_OMITTED_SURFACE_ID, FOLLOWUP_SURFACE_ID)

    omitted = "\n" + render_surface(
        FOLLOWUP_OMITTED_SURFACE_ID,
        {"dropped": dropped, "plural": "" if dropped == 1 else "s"}
    ) + "\n" if dropped else ""
    return "\n\n" + render_surface(FOLLOWUP_SURFACE_ID, {
        "comments": "".join(lines).lstrip("\n"),
        "omitted": omitted,
        "comment_cmd": _cli_command("task", "comment", "<thread id>",
                                    "--body", "<what you did about it>",
                                    "--resolve")})


def _feature_block(task: dict) -> str:
    """The `<feature>` instruction: which feature this run's session counts
    under.

    The attribution itself needs no act from the agent — the lane reads it off
    the run row through the task. What only the agent can do is blame a defect,
    so the block is mostly the command for that, with the id filled in.
    """
    feature_id = task.get("feature_id")
    if feature_id is None:
        return ""
    from lib.features import store as feature_store  # noqa: PLC0415
    from lib.prompts import render_surface  # noqa: PLC0415
    from lib.prompts.surfaces.tasks import FEATURE_SURFACE_ID  # noqa: PLC0415

    feature = feature_store.get(feature_id)
    named = f"#{feature_id}" + (f" ({feature.title})" if feature else "")
    return "\n\n" + render_surface(FEATURE_SURFACE_ID, {
        "feature_named": named, "feature_id": feature_id,
        "spec_cmd": _cli_command("feature", "spec", str(feature_id),
                                 "--file", "-")})


def _spec_block(task: dict) -> str:
    """The `<spec>` instruction, for a feature's spec step only.

    Keyed on the declared `kind`, never on the title: "write the spec and do
    not build" used to be typed into titles by hand.
    """
    feature_id = task.get("feature_id")
    if task.get("kind") != TASK_KIND_SPEC or feature_id is None:
        return ""
    from lib.features import store as feature_store  # noqa: PLC0415
    from lib.prompts import render_surface  # noqa: PLC0415
    from lib.prompts.surfaces.tasks import SPEC_SURFACE_ID  # noqa: PLC0415

    feature = feature_store.get(feature_id)
    named = f"#{feature_id}" + (f" ({feature.title})" if feature else "")
    return "\n\n" + render_surface(SPEC_SURFACE_ID, {
        "feature_named": named,
        "spec_cmd": _cli_command("feature", "spec", str(feature_id),
                                 "--file", "-")})


def _cited_refs(task: dict, feature) -> list[str]:
    """Every reference the task body cites, then each one its feature's spec
    adds, once, in order of first appearance.

    The whole body is read, not its `PROMPT_MAX` cut: a reference past the cut
    is the one whose text the run never sees, so its line is the only trace of
    it the run gets.
    """
    spec = feature.body if feature is not None else None
    cited = (ref.ref for text in (task.get("body"), spec)
             for ref in parse_refs(text))
    return list(dict.fromkeys(cited))


def _project_of(task: dict, feature) -> Optional[int]:
    if task.get("project_id") is not None:
        return task["project_id"]
    return feature.project_id if feature is not None else None


def _resolved(refs: list[str], project_id: Optional[int]) -> dict:
    """`resolve_refs` for the run's own agent, which reads sessions as the
    operator does."""
    from lib.refs.resolve import resolve_refs  # noqa: PLC0415

    try:
        return resolve_refs(refs, project_id=project_id,
                            can_read_sessions=True)
    except RefError:
        # A project id carries no foreign key. A repo dropped since the task
        # was filed only unscopes its file lines; it must not stop the launch.
        return resolve_refs(refs, can_read_sessions=True)


def _read_command(kind: str, key: str) -> str:
    argv = {"feature": ("feature", "spec", key),
            "task": ("task", "show", key),
            "session": ("trace", "dump", key, "--index"),
            "memory": ("memory", "read", key)}.get(kind)
    return _cli_command(*argv) if argv else ""


def _reference_line(ref: str, item: Optional[dict]) -> str:
    """One reference as the run reads it: what it is now, and how to read the
    rest. A defect has no read command, so its line carries the symptom whole.
    """
    if item is None:
        return f"- {ref} — not found"
    kind = item["kind"]
    if kind == "file":
        where = f" in project {item['project']}" if item["project"] else ""
        return " ".join(f"- file {item['title']}{where}".splitlines())
    title = " ".join(item["title"].split())
    head = format_label(kind, item["key"]) + (f" 「{title}」" if title else "")
    facts = [kind]
    if item["status"]:
        facts.append(("stage " if kind == "feature" else "status ")
                     + item["status"])
    if kind == "defect" and item["feature_id"] is not None:
        facts.append(f"feature #{item['feature_id']}")
    read = _read_command(kind, item["key"])
    return f"- {head} — {', '.join(facts)}" + (f" — read: {read}" if read
                                               else "")


def _references_block(task: dict, feature) -> str:
    """The `<references>` instruction: what the task and its feature's spec
    point at, resolved at launch rather than copied, so it cannot drift from
    what it names.

    Empty — not an empty block — when nothing is cited, so such a run carries
    the prompt it always did.
    """
    refs = _cited_refs(task, feature)
    if not refs:
        return ""
    from lib.prompts import render_surface  # noqa: PLC0415
    from lib.prompts.surfaces.tasks import (  # noqa: PLC0415
        REFERENCES_SURFACE_ID)

    shown = refs[:REFERENCES_MAX]
    items = _resolved(shown, _project_of(task, feature))
    lines = [_reference_line(ref, items.get(ref)) for ref in shown]
    more = len(refs) - len(shown)
    if more:
        lines.append(f"… and {more} more reference{'' if more == 1 else 's'}")
    return "\n\n" + render_surface(REFERENCES_SURFACE_ID,
                                   {"lines": "\n".join(lines)})


def _prompt(task: dict, run_id: int, params: Optional[dict] = None) -> str:
    """The sentence a run is launched with, plus how to report it finished.

    A task's images are NOT named here. They travel beside this text as content
    blocks (`supervisor.launch_run(images=…)`), so the prompt stays the idea as
    it was captured — and an attempt re-reads whatever the task carries *now*,
    which is what makes editing an image after the first failed run mean
    something.

    The title is a shelf label written for the board, not an instruction, so it
    is left out whenever a body exists; only a bodyless task falls back to it
    for want of anything else to say.

    An armed skill goes in front as a slash-command line (`_invocation`), so
    the run opens by following that procedure with the idea as its subject.

    Every block after the body is appended *after* the `PROMPT_MAX` cut, never
    inside it: truncation is a property of the captured idea, and a body long
    enough to reach the limit would otherwise silently eat the instructions —
    including the one that lets the task ever complete.

    `<followup>` sits after `<tracker>` so a linked task reads the external
    issue as the source of truth first, and the local unresolved comments as
    what was additionally said here.

    None of the wording is here: every block is a prompt surface under the
    `task-run` area (`lib/prompts/surfaces/tasks.py`), so an operator changes
    what a run is told without changing this file. The order is the envelope's
    to decide, which is why even the assembly renders through a template.
    """
    body = (task.get("body") or "").strip()
    text = body or task["title"]
    from lib.features import store as feature_store  # noqa: PLC0415
    feature = (feature_store.get(task["feature_id"])
               if task.get("feature_id") is not None else None)
    remote = bool(feature is not None and feature.external_ref)
    from lib.prompts import render_surface  # noqa: PLC0415
    from lib.prompts.surfaces.tasks import LAUNCH_SURFACE_ID  # noqa: PLC0415

    finish = _finish_block(run_id, linked=remote, base=_armed_base(params),
                           task_id=task["id"])
    prompt = render_surface(LAUNCH_SURFACE_ID, {
        "invocation": _invocation(params or {}),
        "body": text[:PROMPT_MAX],
        "tracker": _tracker_block(feature),
        "followup": _followup_block(task["id"]),
        "feature": _feature_block(task),
        "spec": _spec_block(task),
        "references": _references_block(task, feature),
        "finish": finish})
    # An envelope edited to drop `{{finish}}` renders fine — a *missing*
    # placeholder is not a render error, so `render_surface` never falls back —
    # and produces a task that can never complete. Every other block is the
    # operator's to remove; this one is the control instruction regin needs
    # back, so it is re-appended rather than silently lost.
    return prompt if finish in prompt else prompt + finish


def _armed_base(params: Optional[dict]) -> str:
    """The branch this run's worktree was cut from, as the prompt should name.

    Read only when a branch was armed too: `_launch_kwargs` builds a worktree
    only for an armed branch, so a lone `base` belongs to no tree this run is
    in — naming it would tell the agent to merge into a branch it was never
    based on.
    """
    spec = (params or {}).get("worktree")
    if not isinstance(spec, dict) or not spec.get("branch"):
        return ""
    base = spec.get("base")
    return base if isinstance(base, str) else ""


def _invocation(params: dict) -> str:
    """The `/<skill> <args>` line an armed skill puts in front of the idea.

    A slash command is what the CLI resolves from the *start* of a prompt, so
    the invocation is the first line and the body follows as the rest of the
    skill's arguments — a skill whose procedure takes the task as its subject
    reads it from there, and one with its own arguments gets them on the line.
    """
    skill = (params or {}).get("skill") or {}
    name = skill.get("name")
    if not name:
        return ""
    line = f"/{name}"
    if skill.get("args"):
        line += f" {skill['args']}"
    return line + "\n\n"


def _launch_kwargs(params: dict) -> dict:
    """The armed parameters, in `supervisor.launch_run`'s own vocabulary.

    A worktree is resolved here rather than at arm time because the tree has to
    exist for *this* attempt: the branch a task was armed with may have been
    removed between arming and running, and `ensure_worktree` is the launch
    route's own answer to that.
    """
    kwargs = {name: params.get(name) or default
              for name, default in _LAUNCH_DEFAULTS.items()}
    kwargs["one_shot"] = bool(kwargs["one_shot"])
    kwargs["env"] = dict(kwargs["env"])
    spec = params.get("worktree") or {}
    if spec.get("branch"):
        anchor = kwargs["cwd"] or str(settings.project_root)
        created = worktree.ensure_worktree(anchor, spec["branch"],
                                           spec.get("base") or "")
        kwargs["cwd"] = created["path"]
    return kwargs


def _launch_images(task: dict) -> list[dict]:
    """The feature's images, then the task's own.

    Resolved here rather than copied onto the slice when the spec was
    transcribed: the mockup is the part of a spec most likely to be replaced
    after the slices exist, and reading it through `feature_id` means
    correcting it corrects the next attempt of every slice already cut. The
    feature's go first — they are the shared backdrop the slice-specific shot
    is read against.
    """
    own = images.payload_for(task["id"])
    feature_id = task.get("feature_id")
    if feature_id is None:
        return own
    from lib.features import images as feature_images  # noqa: PLC0415

    return feature_images.payload_for(feature_id) + own
