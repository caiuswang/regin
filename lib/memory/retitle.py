"""Upgrade auto-derived lesson titles into distilled one-line rules.

A lesson captured via ``send_to_user(type=lesson)`` without an explicit
title gets a placeholder: :func:`lib.memory.store.title_from_body` — the
body's first non-empty line clipped to 80 chars + ``…``. That fragment is a
poor headline: it reads as a mid-sentence stub and (measured across the live
store) recalls markedly worse than a crafted title, yet it rides every
title-showing surface (recall results, the ``/memory`` list, the topic tree).

This module re-derives a proper imperative one-line rule from each such
lesson's body via the distiller LLM, in batches, and updates the rows in
place. The FTS index refreshes immediately inside ``store.update``; the dense
embedding self-heals on the next recall (its ``content_hash`` keys off
``title\\nbody``, so a title change marks it stale and the store's lazy
backfill re-embeds it — exactly how a body edit is handled).

Best-effort by contract: with no external agent configured the LLM
``complete`` returns None and the batch is skipped (nothing is clobbered), so
a caller can run this opportunistically. A crafted title is never touched —
:func:`needs_retitle` only fires on the ``auto-title`` capture tag or a title
that is verifiably a truncated slice of its own body.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field as dc_field

from lib.activity_log import get_activity_logger

log = get_activity_logger("memory")

# Tag stamped on a lesson whose title was auto-derived at capture time
# (`_remember_lesson`), so this pass can find it without re-guessing.
AUTO_TITLE_TAG = "auto-title"

_BODY_CLIP = 1200


@dataclass
class RetitleResult:
    scanned: int = 0        # active lessons examined
    candidates: int = 0     # lessons whose title needs upgrading
    retitled: int = 0       # titles actually changed (0 in dry-run)
    batches: int = 0        # LLM batches that returned a completion
    unparsed: int = 0       # completions whose JSON couldn't be read
    changes: list[dict] = dc_field(default_factory=list)  # {id, old, new}


def _looks_auto_titled(title: "str | None", body: "str | None") -> bool:
    """True for a title minted by ``title_from_body``: the body's first line
    clipped to 80 chars + ``…``. Require BOTH the trailing ellipsis AND that
    the stem is a literal slice of the body, so an authored 80-char headline
    that merely happens to end in ``…`` is left alone."""
    title = (title or "").strip()
    body = (body or "").strip()
    if not title or not body or not title.endswith("…"):
        return False
    stem = title[:-1].strip()
    return len(stem) >= 8 and stem in body


def needs_retitle(mem: dict) -> bool:
    """A `lesson` whose title is an auto-derived placeholder, not a rule."""
    if mem.get("kind") != "lesson":
        return False
    if AUTO_TITLE_TAG in (mem.get("tags") or []):
        return True
    return _looks_auto_titled(mem.get("title"), mem.get("body"))


def _entries_block(batch: list[dict]) -> str:
    """The batch rendered for the prompt: index + clipped body (no title —
    the placeholder is exactly what we don't want the model to anchor on)."""
    parts = []
    for i, m in enumerate(batch):
        body = " ".join((m.get("body") or "").split())[:_BODY_CLIP]
        parts.append(f'<lesson i="{i}">\n{body}\n</lesson>')
    return "\n".join(parts)


def _compose_prompt(batch: list[dict]) -> str:
    """Assemble the retitle prompt via the editable `memory-retitle` surface;
    a broken user edit degrades to the built-in default in `render_surface`."""
    from lib.prompts import render_surface
    from lib.prompts.surfaces.memory import RETITLE_SURFACE_ID
    return render_surface(RETITLE_SURFACE_ID, {"entries": _entries_block(batch)})


def _clean_title(raw) -> str:
    """One collapsed line, no trailing ellipsis, capped at 80 chars on a word
    boundary (never a mid-word cut when the model overshoots the limit)."""
    if not isinstance(raw, str):
        return ""
    t = " ".join(raw.split()).strip().rstrip("…").strip()
    if len(t) <= 80:
        return t
    return t[:80].rsplit(" ", 1)[0] or t[:80]


def _extract_array(answer: str) -> list:
    """The JSON array from a model answer, tolerating markdown fences and
    surrounding prose. Empty list when none can be parsed."""
    text = re.sub(r"```(?:json)?", "", answer or "")
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        return []
    try:
        items = json.loads(text[start:end + 1])
    except (json.JSONDecodeError, ValueError):
        return []
    return items if isinstance(items, list) else []


def _parse_titles(answer: str, n: int) -> "dict[int, str]":
    """Map ``{i: title}`` from one batch's answer. Indices outside ``[0, n)``
    and empty titles drop."""
    out: dict[int, str] = {}
    for item in _extract_array(answer):
        if not isinstance(item, dict):
            continue
        idx, title = item.get("i"), _clean_title(item.get("title"))
        if isinstance(idx, int) and 0 <= idx < n and title:
            out[idx] = title
    return out


def _active_lessons(store, scope, include_tests) -> list[dict]:
    return store.list_memories(kind="lesson", status="active", scope=scope,
                               include_tests=include_tests, limit=100_000)


def _candidates(store, *, scope: "str | None", limit: "int | None",
                include_tests: bool) -> list[dict]:
    """Active lessons whose title needs upgrading, oldest first (a stable
    order so repeated runs are deterministic), capped at `limit`."""
    picked = [m for m in _active_lessons(store, scope, include_tests)
              if needs_retitle(m)]
    picked.sort(key=lambda m: m.get("created_at") or "")
    return picked if limit is None else picked[:limit]


def _apply(store, mem: dict, new_title: str) -> None:
    """Write the new title and strip the `auto-title` tag (its job is done)."""
    tags = [t for t in (mem.get("tags") or []) if t != AUTO_TITLE_TAG]
    store.update(mem["id"], title=new_title, tags=tags)


def retitle_memories(store, llm, *, scope: "str | None" = None,
                     limit: "int | None" = None, batch_size: int = 10,
                     dry_run: bool = False,
                     include_tests: bool = False) -> RetitleResult:
    """Re-derive one-line rule titles for auto-titled lessons, in place.

    Returns a :class:`RetitleResult` with per-row ``changes`` (id, old, new)
    for both dry and live runs. A batch the LLM can't complete or whose JSON
    can't be parsed is skipped, so partial progress still lands.
    """
    result = RetitleResult()
    cands = _candidates(store, scope=scope, limit=limit,
                        include_tests=include_tests)
    result.scanned = len(_active_lessons(store, scope, include_tests))
    result.candidates = len(cands)
    for start in range(0, len(cands), batch_size):
        batch = cands[start:start + batch_size]
        answer = llm.complete(_compose_prompt(batch), max_tokens=2048)
        if not answer:
            log.error("retitle_no_completion", batch_start=start)
            continue
        result.batches += 1
        titles = _parse_titles(answer, len(batch))
        if not titles:
            result.unparsed += 1
            log.error("retitle_unparsed", batch_start=start)
            continue
        for idx, new_title in titles.items():
            mem = batch[idx]
            old = mem.get("title") or ""
            if new_title == old.rstrip("…").strip():
                continue
            result.changes.append({"id": mem["id"], "old": old, "new": new_title})
            if not dry_run:
                _apply(store, mem, new_title)
                result.retitled += 1
    log.write("memory_retitled", candidates=result.candidates,
              retitled=result.retitled, dry_run=dry_run)
    return result


__all__ = ["retitle_memories", "needs_retitle", "RetitleResult",
           "AUTO_TITLE_TAG"]
