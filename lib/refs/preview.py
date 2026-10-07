"""`GET /api/refs/preview`: what one reference says, for the `@` menu's
preview pane — the live item plus the opening of its body.

The body is cut, never paged: the pane answers "is this the one I mean?",
and the item's own page is one click away. References in it read as their
labels and images as a placeholder, because the pane is no place to resolve
a second round of chips or fetch attachments.
"""

from __future__ import annotations

import json
import re
import subprocess
from typing import Optional

from sqlalchemy import func
from sqlmodel import select

from lib.activity_log import get_activity_logger
from lib.memory.engine import MemorySessionLocal
from lib.memory.models import Memory
from lib.orm import SessionLocal
from lib.orm.models import SessionSpan
from lib.orm.models.defects import Defect
from lib.orm.models.features import Feature
from lib.orm.models.tasks import AgentTask
from lib.refs import resolve
from lib.refs.grammar import RefError, decode_file_key, parse_ref_target, parse_refs

log = get_activity_logger("refs")

BODY_MAX = 4000
#: Read off `git show` before the cut; a body this size is never all shown.
FILE_BYTES_MAX = 64_000
_IMAGE = re.compile(r"!\[[^\]\n]*\]\(image:[0-9]+\)")


def _readable(body: Optional[str]) -> str:
    """References as their (still escaped) labels, images as a word."""
    text = body or ""
    parts, at = [], 0
    for ref in parse_refs(text):
        parts.append(text[at:ref.start])
        # The source label, escapes kept: markdown renders it as the text the
        # chip would have said.
        tail = len("](:)") + len(ref.kind) + len(ref.key)
        parts.append(text[ref.start + 1:ref.end - tail])
        at = ref.end
    parts.append(text[at:])
    return _IMAGE.sub("*(image)*", "".join(parts))


def _clip(body: str) -> tuple[str, bool]:
    if len(body) <= BODY_MAX:
        return body, False
    cut = body.rfind("\n", 0, BODY_MAX)
    return body[:cut if cut > BODY_MAX // 2 else BODY_MAX].rstrip(), True


def _feature(key, _project) -> tuple[str, str]:
    with SessionLocal() as session:
        row = session.get(Feature, int(key))
    return _readable(row.body if row else ""), "markdown"


def _task(key, _project) -> tuple[str, str]:
    with SessionLocal() as session:
        row = session.get(AgentTask, int(key))
    return _readable(row.body if row else ""), "markdown"


def _suspects(raw: Optional[str]) -> list[str]:
    try:
        paths = json.loads(raw or "[]")
    except ValueError:
        return []
    return [str(path) for path in paths if path] if isinstance(paths, list) \
        else []


def _defect(key, _project) -> tuple[str, str]:
    with SessionLocal() as session:
        row = session.get(Defect, int(key))
    if row is None:
        return "", "markdown"
    parts = [_readable(row.symptom)]
    paths = _suspects(row.suspect_paths_json)
    if paths:
        parts.append("**Suspect paths**\n\n"
                     + "\n".join(f"- `{path}`" for path in paths))
    if row.missing_check:
        parts.append(f"**Missing check**\n\n{row.missing_check}")
    return "\n\n".join(parts), "markdown"


def _session(key, _project) -> tuple[str, str]:
    with SessionLocal() as session:
        prompt = session.exec(
            select(func.json_extract(SessionSpan.attributes, "$.text"))
            .where(SessionSpan.trace_id == key, SessionSpan.name == "prompt")
            .order_by(SessionSpan.start_time, SessionSpan.id)
            .limit(1)).first()
    return str(prompt or ""), "text"


def _memory(key, _project) -> tuple[str, str]:
    with MemorySessionLocal() as session:
        row = session.get(Memory, key)
    return (row.body or "") if row else "", "text"


def _file(key, project) -> tuple[str, str]:
    """The file as committed at HEAD — what a build's worktree will hold."""
    path = decode_file_key(key)
    if project is None or not path:
        return "", "code"
    try:
        with subprocess.Popen(
                ["git", "-C", project.path, "show", f"HEAD:{path}"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as proc:
            data = proc.stdout.read(FILE_BYTES_MAX)
            proc.kill()
    except OSError:
        return "", "code"
    if b"\0" in data[:8000]:
        return "(binary file)", "text"
    return data.decode("utf-8", errors="replace"), "code"


_BODIES = {"feature": _feature, "task": _task, "defect": _defect,
           "session": _session, "file": _file, "memory": _memory}


def preview(ref: str, *, project_id=None, can_read_sessions: bool) -> dict:
    """`{ref, item, body, format, truncated}`. `item` is None when the
    reference names nothing, and a restricted session previews no body."""
    target = parse_ref_target(ref or "")
    if target is None:
        raise RefError(f"not a reference: {ref}", "ref")
    kind, key = target
    project = resolve.require_project(project_id)
    found = resolve.items_for(kind, [key], project=project,
                              can_read_sessions=can_read_sessions)
    item = found[0] if found else None
    body, fmt = ("", "text") if item is None or item.get("restricted") \
        else _BODIES[kind](key, project)
    body, truncated = _clip(body.strip())
    log.read("ref_previewed", kind=kind, found=item is not None)
    return {"ref": ref, "item": item, "body": body, "format": fmt,
            "truncated": truncated}
