"""Reserved span-id helpers for live "pending" placeholder spans.

A *pending* span marks an operation that is currently blocking on the user:
a prompt just submitted (its real `prompt-<uuid>` anchor isn't written until
the turn flushes), an `AskUserQuestion` / `ExitPlanMode` awaiting an answer,
or a permission request awaiting grant/deny. It is emitted immediately with a
deterministic span_id under a RESERVED PREFIX so it can never collide with a
real span. The store is append-only: ingest never deletes the placeholder;
`lib/trace/merge.py` drops it at read time once the resolved span is present
in the window — see `pending_id_for_resolved`.

The id is the handoff key:
- prompt   → `promptlive-<sha1(session + text-prefix)>`  (matched by text)
- tool     → `pending-<tool_use_id>`                     (matched by tool_use_id)
- permission → `permreq-<tool_use_id>`                   (matched by tool_use_id)
"""

from __future__ import annotations

import hashlib
from datetime import datetime

# The silence window (seconds) after which an unstopped agent / stuck pending
# span is considered dead. Shared by the roster stale gate
# (web/blueprints/trace/sessions.py) and the merge demotion (lib/trace/merge.py)
# so the two verdicts can never drift.
INACTIVE_THRESHOLD_SEC = 600

# The per-agent id, preferring the dedicated column and falling back to the
# JSON attribute (older rows never populated the column). Interpolated into the
# raw agent-scoped SQL in sessions.py / trace_service.queries so the fragment
# lives in exactly one place.
AGENT_ID_SQL = "COALESCE(agent_id, json_extract(attributes,'$.agent_id'))"


def parse_naive_ts(iso) -> datetime | None:
    """Normalize a span timestamp to naive local. Hook placeholders are naive
    server-local; transcript anchors + test fixtures are tz-aware (`...Z`) —
    comparisons must never mix awareness."""
    if not isinstance(iso, str) or not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace('Z', '+00:00'))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


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


# A subagent's launch-prompt span carries name=='prompt' but is agent-scoped:
# id `prompt-sa-<agent_id>`, attributes.agent_id set. The main-conversation
# prompt-anchor machinery (ceiling, stale-blocker cutoff, orphan grafting,
# turn-lookahead) must EXCLUDE these, or a subagent prompt fired mid-run acts
# as a main turn anchor — dropping the user's live placeholder and grafting
# main orphans under the subagent subtree.
SUBAGENT_PROMPT_PREFIX = 'prompt-sa-'


def is_agent_scoped_prompt(span: dict, attrs: dict | None = None) -> bool:
    """True for a subagent-scoped `prompt` span. Detected by attributes.agent_id
    (present on every emit, old and new rows) or the `prompt-sa-` id prefix.
    `attrs` covers callers that carry attributes separately from the span row."""
    for candidate in (attrs, span.get('attributes')):
        if isinstance(candidate, dict) and candidate.get('agent_id'):
            return True
    sid = span.get('span_id')
    return isinstance(sid, str) and sid.startswith(SUBAGENT_PROMPT_PREFIX)


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
    out = _prompt_supersede_ids(span, attrs)
    tu_id = (attrs or {}).get('tool_use_id')
    if isinstance(tu_id, str) and tu_id:
        out.append(tool_pending_id(tu_id))
        out.append(perm_pending_id(tu_id))
    return out


def _prompt_supersede_ids(span: dict, attrs: dict) -> list[str]:
    if span.get('name') != 'prompt' or is_agent_scoped_prompt(span, attrs):
        return []
    text = (attrs or {}).get('text')
    if not isinstance(text, str) or not text:
        return []
    return [prompt_placeholder_id(span.get('trace_id'), text)]
