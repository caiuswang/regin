"""Parsing of Claude Code's `<task-notification>` envelope.

Shared by the UserPromptSubmit hook that captures these live and by the CLI
backfill that re-parses already-stored envelopes, so a tag added here reaches
both instead of the two drifting apart.
"""

from __future__ import annotations

import re

_ENVELOPE_RE = re.compile(
    r'<task-notification>(.*?)</task-notification>', re.DOTALL)

# Single-line fields. `[^<]*` is deliberate: it cannot run past a malformed
# close tag into the rest of the envelope.
_FLAT_RE = {
    'task_id': re.compile(r'<task-id>([^<]*)</task-id>'),
    'tool_use_id': re.compile(r'<tool-use-id>([^<]*)</tool-use-id>'),
    'output_file': re.compile(r'<output-file>([^<]*)</output-file>'),
    'status': re.compile(r'<status>([^<]*)</status>'),
    'summary': re.compile(r'<summary>([^<]*)</summary>'),
}

# The agent's own report: multi-line markdown whose angle brackets arrive
# entity-escaped, so it needs a DOTALL body rather than the `[^<]*` above.
_RESULT_RE = re.compile(r'<result>(.*?)</result>', re.DOTALL)

# Every `<task-id>` in the envelope, not just the first. A session that ends
# with background shells still running is reported to the NEXT session as ONE
# envelope naming all of them at once.
_ALL_TASK_IDS_RE = re.compile(r'<task-id>([^<]*)</task-id>')

# The envelope documents this itself: ids beginning `__orphan_summary` are
# internal scan markers the harness emits alongside the real ids, never tasks.
_SCAN_MARKER_PREFIX = '__orphan_summary'

_USAGE_RE = re.compile(r'<usage>(.*?)</usage>', re.DOTALL)
_COUNTER_RE = {
    'subagent_tokens': re.compile(r'<subagent_tokens>(\d+)</subagent_tokens>'),
    'tool_uses': re.compile(r'<tool_uses>(\d+)</tool_uses>'),
    'duration_ms': re.compile(r'<duration_ms>(\d+)</duration_ms>'),
}

ENVELOPE_MARKER = '<task-notification>'


def parse_task_notification(text: str) -> dict | None:
    """Return the envelope's fields, or None when `text` isn't one.

    An envelope that carries no recognised tag yields `{}` — falsy, but
    distinguishable from "not a notification", which callers branch on.
    """
    if not text or ENVELOPE_MARKER not in text:
        return None
    body_match = _ENVELOPE_RE.search(text)
    if not body_match:
        return None
    body = body_match.group(1)
    fields: dict = {}
    for key, regex in _FLAT_RE.items():
        m = regex.search(body)
        if m:
            fields[key] = m.group(1).strip()
    task_ids = [t.strip() for t in _ALL_TASK_IDS_RE.findall(body)]
    real = [t for t in task_ids if t and not t.startswith(_SCAN_MARKER_PREFIX)]
    if real:
        fields['task_ids'] = real
    result = _RESULT_RE.search(body)
    if result:
        fields['result'] = result.group(1).strip()
    # Scoped to `<usage>` rather than the whole body: `<result>` precedes it and
    # a report that quotes these tags — a verifier reporting on this parser does
    # exactly that — would otherwise win the search and poison the counters.
    usage = _USAGE_RE.search(body)
    for key, regex in _COUNTER_RE.items():
        m = regex.search(usage.group(1)) if usage else None
        if m:
            fields[key] = int(m.group(1))
    return fields
