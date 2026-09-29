"""Serve-time prompt expansions: slash-command expansion text read from a
session's transcript and attached to its prompt spans, behind a
fingerprinted per-trace cache.
"""

from __future__ import annotations

import os
import threading
import time
from collections import OrderedDict
from typing import NamedTuple, Optional

# Per-turn text cap for the expansion read. The expansion map comes from the
# transcript's isMeta entries and is not subject to this cap, so capping costs
# nothing here and keeps a serve-time read from materialising a 20 MB
# transcript's full response/thinking text.
_EXPANSION_TEXT_CAP = 64 * 1024


_EXPANSION_CACHE_MAX = 64


# Providers whose sessions pay for the serve-time expansion read. Claude is
# deliberately absent: the pass used to gate on a `settings.transcript_dir`
# that has never existed, so it has been inert for every Claude session for
# the whole life of the feature, and switching it on costs a cold 20 MB
# transcript parse (~1.1s, ~75x the whole projection) at the head of
# `/api/sessions/<id>/map`. Enabling it for Claude is a separate decision
# that owes its own performance work.
_EXPANSION_PROVIDER_IDS = frozenset({'kimi'})


# Re-read window for a transcript that is still being appended to. A live
# session's file changes between polls, so the fingerprint alone would
# re-parse on every request; only an expensive parse is worth throttling.
_EXPANSION_RESCAN_SECONDS = 30.0


_EXPANSION_RESCAN_MIN_COST = 0.02


class _ExpansionEntry(NamedTuple):
    path: Optional[str]
    fingerprint: Optional[tuple]
    parsed_at: float
    parse_seconds: float
    target_ids: frozenset
    expansions: dict


_expansion_cache: 'OrderedDict[str, _ExpansionEntry]' = OrderedDict()


_expansion_cache_lock = threading.Lock()


def _reset_prompt_expansion_cache() -> None:
    with _expansion_cache_lock:
        _expansion_cache.clear()


def _expansion_cache_get(trace_id: str) -> Optional[_ExpansionEntry]:
    with _expansion_cache_lock:
        entry = _expansion_cache.get(trace_id)
        if entry is not None:
            _expansion_cache.move_to_end(trace_id)
        return entry


def _expansion_cache_put(trace_id: str, entry: _ExpansionEntry) -> None:
    with _expansion_cache_lock:
        _expansion_cache[trace_id] = entry
        _expansion_cache.move_to_end(trace_id)
        while len(_expansion_cache) > _EXPANSION_CACHE_MAX:
            _expansion_cache.popitem(last=False)


def _transcript_fingerprint(path: str) -> Optional[tuple]:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_size, st.st_mtime_ns)


def _expansion_targets(spans: list[dict]) -> dict[str, dict]:
    """Prompt spans that could still gain an `expanded_text`."""
    out: dict[str, dict] = {}
    for span in spans:
        span_id = span.get('span_id')
        if span.get('name') != 'prompt' or not isinstance(span_id, str):
            continue
        if not span_id.startswith('prompt-'):
            continue
        attrs = span.get('attributes')
        if attrs is not None and not isinstance(attrs, dict):
            continue
        if isinstance(attrs, dict) and attrs.get('expanded_text'):
            continue
        out[span_id] = span
    return out


def _expansion_entry_is_usable(entry: _ExpansionEntry,
                               target_ids: frozenset, now: float) -> bool:
    """Whether a cached read still answers this call without re-parsing."""
    if not target_ids <= entry.target_ids:
        return False
    if entry.path is None:
        return now - entry.parsed_at < _EXPANSION_RESCAN_SECONDS
    if _transcript_fingerprint(entry.path) == entry.fingerprint:
        return True
    return (entry.parse_seconds >= _EXPANSION_RESCAN_MIN_COST
            and now - entry.parsed_at < _EXPANSION_RESCAN_SECONDS)


def _read_prompt_expansions(provider, path: Optional[str]) -> dict:
    if not path:
        return {}
    try:
        usage = provider.parse_transcript(path, max_text_bytes=_EXPANSION_TEXT_CAP)
        expansions = usage.prompt_expansions if usage else None
    except Exception:
        return {}
    return dict(expansions) if expansions else {}


def _session_agent_type(trace_id: str, conn) -> Optional[str]:
    """`sessions.agent_type` for one trace, on the caller's connection.

    Serve-time readers already hold an open sqlite handle, so this stays on
    it rather than going through `lib.providers.base.session_agent_type`,
    which spins up the SQLModel engine (~11 ms the first time in a process)
    for a single indexed row on a path whose whole budget is ~10 ms.
    """
    if conn is None:
        from lib.orm.engine import get_connection
        conn = get_connection()
        try:
            return _session_agent_type(trace_id, conn)
        finally:
            conn.close()
    row = conn.execute(
        "SELECT agent_type FROM sessions WHERE trace_id = ?", (trace_id,)
    ).fetchone()
    return row['agent_type'] if row else None


def _expansion_provider(trace_id: str, conn):
    """The session's provider when it serves prompt expansions, else None.

    Resolved from `sessions.agent_type` — the provider that WROTE this
    session — not from `settings.active_provider`, which is a global that
    says nothing about an already-recorded trace and would flip every
    session's answer the moment the user switched CLIs.
    """
    from lib.providers.registry import build_provider, canonical_agent_kind

    kind = canonical_agent_kind(_session_agent_type(trace_id, conn))
    return build_provider(kind) if kind in _EXPANSION_PROVIDER_IDS else None


def _load_prompt_expansions(provider, trace_id: str, target_ids: frozenset,
                            now: float) -> dict:
    path = provider.find_session_transcript(trace_id)
    # Fingerprint before the read: a file that grows mid-parse must look
    # stale afterwards, never current-with-partial-content.
    fingerprint = _transcript_fingerprint(path) if path else None
    started = time.monotonic()
    expansions = _read_prompt_expansions(provider, path)
    _expansion_cache_put(trace_id, _ExpansionEntry(
        path=path, fingerprint=fingerprint, parsed_at=now,
        parse_seconds=time.monotonic() - started,
        target_ids=target_ids, expansions=expansions,
    ))
    return expansions


def _prompt_expansions_for_trace(provider, trace_id: str,
                                 target_ids: frozenset) -> dict:
    """uuid → expanded prompt text for one session, memoized per process.

    This runs on the live polling path (`/map` and `/spans/<id>/children`
    re-derive it every few seconds per open session) where a full transcript
    parse costs ~0.1s on a 20 MB file. A cached read is reused until the
    transcript changes *and* either a prompt span the last read did not cover
    shows up or the re-scan window elapses.
    """
    now = time.monotonic()
    entry = _expansion_cache_get(trace_id)
    if entry is not None and _expansion_entry_is_usable(entry, target_ids, now):
        return entry.expansions
    return _load_prompt_expansions(provider, trace_id, target_ids, now)


def _attach_prompt_expansions(trace_id: str, spans: list[dict], conn=None) -> None:
    """Attach expanded_text to prompt spans from the transcript scan.

    Slash-command prompts (e.g. /review) have a bare command in the text
    attribute but carry a full expansion in the isMeta child entry of the
    transcript. This function reads the transcript, extracts the expansions,
    and attaches them as `expanded_text` attributes on the matching prompts
    so the frontend can show both the concise label and the full expansion.

    Only sessions written by a provider in `_EXPANSION_PROVIDER_IDS` are
    scanned; every other session is left exactly as the merge produced it,
    at no transcript-read cost.

    Pure serve-time derivation; mutates the shared `attributes` dicts in
    place. No DB schema change needed."""
    targets = _expansion_targets(spans)
    if not targets:
        return
    provider = _expansion_provider(trace_id, conn)
    if provider is None:
        return
    expansions = _prompt_expansions_for_trace(
        provider, trace_id, frozenset(targets))
    for prompt_uuid, expansion_text in expansions.items():
        # The span_id format for transcript-derived prompts is `prompt-<uuid[:13]>`
        span = targets.get(f'prompt-{prompt_uuid[:13]}')
        if span is not None:
            span.setdefault('attributes', {})['expanded_text'] = expansion_text
