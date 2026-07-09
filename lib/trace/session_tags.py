"""Session tag model: the single source of truth for how sessions are grouped.

A session carries **many tags**. Two kinds coexist behind one flat surface:

- **Builtin category tags** (`user` / `topic-proposal` / `system`) are
  *intrinsic* — derived from `sessions.origin` at read time, never stored.
  This keeps them single-axis (origin only, never `agent_type`) so they can't
  drift, and needs no backfill. Each builtin owns a set of `origin` values;
  the sets partition the origin space, with `user` as the fallback.
- **Custom tags** are user-authored, stored in the `session_tags` join table
  (`source='manual'`), and are what "add a group" writes. Free-form M2M.

Everything downstream — the origin→builtin mapping, `_RUN_ORIGINS`, the
per-tag SQL filter, and slug validation — keys off `BUILTIN_TAGS` here, so
adding a builtin category is one entry and adding a custom group is one row.
"""

from __future__ import annotations

import re

# Ordered; `user` is the fallback and MUST stay last (its empty `origins`
# marks it as "everything not claimed above"). Adding a builtin category =
# one entry with the origin values it owns.
BUILTIN_TAGS: tuple[dict, ...] = (
    {
        "slug": "topic-proposal",
        "label": "Topic proposal",
        "origins": ("topic-proposal",),
    },
    {
        "slug": "system",
        "label": "System",
        "origins": ("workflow", "llm-stage"),
    },
    {
        "slug": "user",
        "label": "User",
        "origins": (),  # fallback: any origin not owned by a tag above
    },
)

_FALLBACK_TAG = "user"

# Slug charset for custom tags: lowercase alphanumerics plus dash, 1-40 chars.
# Keeps tags URL-safe (they ride `?tag=` and `DELETE .../tags/<tag>`) and
# collision-free with the builtin slugs.
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")

# Prompt auto-tagging: an external caller can prefix a prompt's FIRST line with
# `regin-tags: a, b` (singular `regin-tag:` and any case accepted) to have the
# session self-tag. Deliberately delicate — the marker is a self-contained line
# the agent ignores, tags are the same slugs as manual custom tags, and the
# stored rows carry source='auto' so they read as derived, not hand-assigned.
_TAG_MARKER_RE = re.compile(r"^\s*regin-tags?\s*:\s*(.*)$", re.IGNORECASE)

# Ceiling on tags parsed from one marker line, so a runaway line can't spray
# the tag facet with dozens of groups.
_MAX_PROMPT_TAGS = 8

AUTO_TAG_SOURCE = "auto"


def _first_nonblank_line(text: str) -> str | None:
    return next((ln for ln in text.splitlines() if ln.strip()), None)


def parse_prompt_tags(text: object) -> list[str]:
    """Custom tag slugs declared on a prompt's first line, or ``[]``.

    The first non-blank line must read ``regin-tags: a, b c``. The remainder
    is split on commas/whitespace; each token may carry a leading ``#``
    (hashtag style); tokens are normalized like manual tags via
    `normalize_custom_slug` (so builtin-category slugs and bad charsets are
    dropped), deduped in order, and capped at `_MAX_PROMPT_TAGS`.
    """
    if not isinstance(text, str):
        return []
    line = _first_nonblank_line(text)
    if line is None:
        return []
    m = _TAG_MARKER_RE.match(line)
    if not m:
        return []
    out: list[str] = []
    for raw in re.split(r"[,\s]+", m.group(1)):
        slug = normalize_custom_slug(raw.lstrip("#"))
        if slug and slug not in out:
            out.append(slug)
        if len(out) >= _MAX_PROMPT_TAGS:
            break
    return out


def strip_prompt_tag_marker(text: object) -> object:
    """`text` with a leading `regin-tags:` marker line removed.

    Lets the session title derive from the real first instruction rather than
    the metadata line — so an auto-tag marker never disturbs the agent's goal
    or the list title. A no-op (returns `text` unchanged) when the first
    non-blank line isn't a marker, so ordinary prompts are untouched.
    """
    if not isinstance(text, str):
        return text
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if not ln.strip():
            continue
        return "\n".join(lines[i + 1:]) if _TAG_MARKER_RE.match(ln) else text
    return text


def builtin_slugs() -> list[str]:
    return [t["slug"] for t in BUILTIN_TAGS]


def is_builtin(slug: str) -> bool:
    return any(t["slug"] == slug for t in BUILTIN_TAGS)


def origins_for_builtin(slug: str) -> tuple[str, ...] | None:
    """The origin values a builtin owns, or None for the fallback tag.

    None means "every origin not owned by another builtin" (the `user`
    fallback) — the caller expresses it as a NOT-IN over `claimed_origins`.
    """
    for t in BUILTIN_TAGS:
        if t["slug"] == slug:
            return t["origins"] or None
    return None


def claimed_origins() -> tuple[str, ...]:
    """Every origin explicitly owned by a non-fallback builtin."""
    return _claimed_origins()


def _claimed_origins() -> tuple[str, ...]:
    """Every origin explicitly owned by a non-fallback builtin."""
    return tuple(o for t in BUILTIN_TAGS for o in t["origins"])


def system_origins() -> tuple[str, ...]:
    """Origins the legacy `workflow=hide|only` toggle treats as runs.

    Kept as the `system` builtin's origin set so the old toggle and the new
    tag facet can't disagree about what a "run" is.
    """
    return origins_for_builtin("system") or ()


def builtin_tag_for_origin(origin: str | None) -> str:
    """The one builtin category slug a session's `origin` maps to."""
    for t in BUILTIN_TAGS:
        if origin in t["origins"]:
            return t["slug"]
    return _FALLBACK_TAG


def builtin_meta() -> list[dict]:
    """Builtin tag descriptors for the API (slug + label), display order."""
    return [{"slug": t["slug"], "label": t["label"]} for t in BUILTIN_TAGS]


def normalize_custom_slug(raw: object) -> str | None:
    """Validate/normalize a user-supplied tag slug, or None if unusable.

    Trims and lowercases, rejects the builtin slugs (reserved — those are
    intrinsic and can't be hand-assigned), and enforces the slug charset.
    """
    if not isinstance(raw, str):
        return None
    slug = raw.strip().lower()
    if not slug or is_builtin(slug) or not _SLUG_RE.match(slug):
        return None
    return slug
