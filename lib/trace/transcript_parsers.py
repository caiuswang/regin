"""Stateless parsing helpers for Claude Code transcript entries.

Extracted from `lib.trace.transcript_usage` so the read_usage pipeline
can remain a thin orchestration layer over these primitives.
"""

from __future__ import annotations

import re
from datetime import datetime


def _delta_ms(start_iso: str | None, end_iso: str | None) -> int | None:
    """Return `end - start` in whole milliseconds, or None when either
    timestamp can't be parsed or the result is negative (which would
    mean the transcript writer wrote entries out of order — defensive
    skip rather than emit a misleading negative latency).
    """
    if not isinstance(start_iso, str) or not start_iso:
        return None
    if not isinstance(end_iso, str) or not end_iso:
        return None
    try:
        s = datetime.fromisoformat(start_iso.replace('Z', '+00:00'))
        e = datetime.fromisoformat(end_iso.replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None
    delta = e - s
    ms = int(delta.total_seconds() * 1000)
    if ms < 0:
        return None
    return ms


def _walk_to_assistant(
    start: str | None,
    entry_kind: dict[str, str],
    entry_parent: dict[str, str | None],
) -> str | None:
    """Walk a parentUuid chain back to the most recent `assistant`
    entry. Bounded depth so a malformed transcript can't loop.
    Returns the assistant uuid, or None if no `assistant` ancestor is
    reachable within the bound.
    """
    cursor = start
    for _ in range(32):
        if cursor is None:
            return None
        kind = entry_kind.get(cursor)
        if kind == 'assistant':
            return cursor
        cursor = entry_parent.get(cursor)
    return None


def _walk_to_prompt(
    start: str | None,
    entry_parent: dict[str, str | None],
    real_prompt_uuids: set[str],
) -> str | None:
    """Walk a parentUuid chain back to the nearest real-prompt ancestor.

    A "real prompt" is the user entry that opened a turn — classified by
    the caller and collected into `real_prompt_uuids` (typed prompts and
    queued commands; NOT tool_results, task-notifications, local-command
    echoes, image carriers, meta, or sidechain entries). Intermediate
    `assistant`, `system`, `attachment`, and tool_result `user` entries
    are passed straight through, so a multi-iteration turn (tool_result
    between assistant entries) still resolves to the prompt that began
    it. Returns the prompt uuid, or None if the chain reaches the root
    without crossing a real prompt (e.g. a workflow-resume turn whose
    only ancestor prompts are meta). Cycle-guarded via a visited set so
    a malformed transcript can't loop, with no fixed depth cap (a long
    agentic turn can chain through hundreds of entries).
    """
    cursor = start
    seen: set[str] = set()
    while cursor is not None and cursor not in seen:
        if cursor in real_prompt_uuids:
            return cursor
        seen.add(cursor)
        cursor = entry_parent.get(cursor)
    return None


def _extract_text_blocks(content: object) -> list[str]:
    """Pull text out of `type: text` content blocks ONLY.

    Earlier versions folded `type: thinking` blocks in here so the
    response span stayed populated for thinking-enabled models. That
    quietly mixed extended-thinking reasoning into what downstream
    consumers treated as "what the user saw." We now split them:
    text blocks land here, thinking blocks land in
    `_scan_thinking_blocks` and end up in a separate `thinking_text`
    field on the turn.
    """
    if not isinstance(content, list):
        return []
    out: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get('type') != 'text':
            continue
        t = block.get('text')
        if isinstance(t, str) and t:
            out.append(t)
    return out


def _scan_thinking_blocks(content: object) -> tuple[list[str], int, int]:
    """Return (text_parts, block_count, signature_byte_sum) for the
    thinking content blocks under `content`.

    Block count and signature bytes are surfaced separately because
    Anthropic frequently redacts the visible `thinking` text but keeps
    the encrypted `signature` — the bytes are the only signal that
    thinking happened on that turn.
    """
    if not isinstance(content, list):
        return [], 0, 0
    parts: list[str] = []
    count = 0
    sig_bytes = 0
    for block in content:
        if not isinstance(block, dict) or block.get('type') != 'thinking':
            continue
        count += 1
        t = block.get('thinking')
        if isinstance(t, str) and t:
            parts.append(t)
        sig = block.get('signature')
        if isinstance(sig, str):
            sig_bytes += len(sig)
    return parts, count, sig_bytes


def _to_snake(name: str) -> str:
    if not isinstance(name, str) or not name:
        return name
    s1 = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1_\2', name)
    s2 = re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', s1)
    return s2.replace('-', '_').lower()


def _normalize_dict_keys(d: object) -> dict:
    """Return a shallow dict with snake_case aliases for every key."""
    if not isinstance(d, dict):
        return {}
    out = dict(d)
    for key, value in list(d.items()):
        if isinstance(key, str):
            out.setdefault(_to_snake(key), value)
    return out


def _assistant_message(entry: dict) -> tuple[str | None, dict]:
    """Return (entry_type, message_dict) for assistant-like entries."""
    entry_n = _normalize_dict_keys(entry)
    etype = entry_n.get('type')
    role = entry_n.get('role')
    message = _normalize_dict_keys(entry_n.get('message'))

    if isinstance(etype, str):
        etype = etype.lower()
    if isinstance(role, str):
        role = role.lower()

    # Claude shape: {"type":"assistant","message":{...}}
    if etype == 'assistant':
        return 'assistant', message
    # Codex/other shapes: {"role":"assistant", ...}
    if role == 'assistant':
        return 'assistant', message or entry_n
    return None, {}


def _usage_tokens(usage: dict) -> tuple[int, int, int, int]:
    """Return (input, output, cache_read, cache_creation) from mixed schemas."""
    usage_n = _normalize_dict_keys(usage)
    input_tokens = int(
        usage_n.get('input_tokens')
        or usage_n.get('prompt_tokens')
        or 0
    )
    output_tokens = int(
        usage_n.get('output_tokens')
        or usage_n.get('completion_tokens')
        or 0
    )
    cache_read_tokens = int(
        usage_n.get('cache_read_input_tokens')
        or usage_n.get('cache_read_tokens')
        or 0
    )
    cache_creation_tokens = int(
        usage_n.get('cache_creation_input_tokens')
        or usage_n.get('cache_creation_tokens')
        or 0
    )
    return input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens


def _truncate_utf8(text: str, max_bytes: int) -> tuple[str, bool]:
    """Truncate `text` so its UTF-8 encoding is at most `max_bytes`.

    Cuts on a UTF-8 boundary (never mid-code-point). Returns
    `(possibly_truncated, was_truncated)`.
    """
    encoded = text.encode('utf-8')
    if len(encoded) <= max_bytes:
        return text, False
    # Decode best-effort then trim trailing replacement chars produced
    # by a mid-codepoint cut.
    head = encoded[:max_bytes].decode('utf-8', errors='ignore')
    return head + '\n\n…[truncated]', True
