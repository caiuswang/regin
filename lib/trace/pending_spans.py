"""Reserved span-id helpers for live "pending" placeholder spans.

A *pending* span marks an operation that is currently blocking on the user:
a prompt just submitted (its real `prompt-<uuid>` anchor isn't written until
the turn flushes), an `AskUserQuestion` / `ExitPlanMode` awaiting an answer,
or a permission request awaiting grant/deny. It is emitted immediately with a
deterministic span_id under a RESERVED PREFIX so it can never collide with a
real span, and `ingest_session_spans` deletes it the moment the resolved span
lands — see `pending_id_for_resolved`.

The id is the handoff key:
- prompt   → `promptlive-<sha1(session + text-prefix)>`  (matched by text)
- tool     → `pending-<tool_use_id>`                     (matched by tool_use_id)
- permission → `permreq-<tool_use_id>`                   (matched by tool_use_id)
"""

from __future__ import annotations

import hashlib

PROMPT_PLACEHOLDER_PREFIX = 'promptlive-'
TOOL_PENDING_PREFIX = 'pending-'
PERM_PENDING_PREFIX = 'permreq-'

_RESERVED_PREFIXES = (
    PROMPT_PLACEHOLDER_PREFIX,
    TOOL_PENDING_PREFIX,
    PERM_PENDING_PREFIX,
)

# Hash a fixed-length prefix of the prompt text, not the whole thing: the
# live placeholder hashes `payload.prompt` while ingest hashes the anchor's
# `attributes.text`, which turn_trace caps at 8 KB. A 512-char prefix is well
# under that cap, so both sides hash the identical bytes.
_PROMPT_HASH_CHARS = 512


def is_pending_span_id(span_id) -> bool:
    """True if `span_id` is a reserved pending-placeholder id."""
    return isinstance(span_id, str) and span_id.startswith(_RESERVED_PREFIXES)


def prompt_placeholder_id(session_id, text: str) -> str:
    """Stable id for a prompt placeholder. Uses hashlib (not Python's salted
    `hash()`) so the hook process and the server compute the same value."""
    key = f'{session_id or ""}\x00{(text or "").strip()[:_PROMPT_HASH_CHARS]}'
    digest = hashlib.sha1(key.encode('utf-8')).hexdigest()[:13]
    return f'{PROMPT_PLACEHOLDER_PREFIX}{digest}'


def tool_pending_id(tool_use_id: str) -> str:
    """Pending id for a blocking tool call, keyed on its `tool_use_id`
    (same [:13] truncation the synthetic tool spans use)."""
    return f'{TOOL_PENDING_PREFIX}{(tool_use_id or "")[:13]}'


def perm_pending_id(tool_use_id: str) -> str:
    """Pending id for a permission request, keyed on the gated call's
    `tool_use_id`."""
    return f'{PERM_PENDING_PREFIX}{(tool_use_id or "")[:13]}'


def pending_id_for_resolved(span: dict, attrs: dict) -> list[str]:
    """Pending span_ids a freshly-ingested *resolved* span supersedes.

    - a real prompt anchor (`name == 'prompt'`, non-reserved id) supersedes
      the `promptlive-<hash(text)>` placeholder for its own text.
    - any span carrying a `tool_use_id` supersedes that call's `pending-<tu>`
      and `permreq-<tu>` placeholders (answered / denied / errored / granted).

    A reserved-prefix span supersedes nothing (a pending span never retires
    another) — that's also the self-delete guard for the ingest handoff.
    """
    span_id = span.get('span_id')
    if is_pending_span_id(span_id):
        return []
    out: list[str] = []
    if span.get('name') == 'prompt':
        text = (attrs or {}).get('text')
        if isinstance(text, str) and text:
            out.append(prompt_placeholder_id(span.get('trace_id'), text))
    tu_id = (attrs or {}).get('tool_use_id')
    if isinstance(tu_id, str) and tu_id:
        out.append(tool_pending_id(tu_id))
        out.append(perm_pending_id(tu_id))
    return out
