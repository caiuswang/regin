"""Handler: warn when a Markdown edit introduces rot-prone content.

Fires on PostToolUse for Write/Edit/MultiEdit on `*.md`. Scans the
new content for two classes of doc-rot and surfaces hits via
`additional_context` so the agent can fix them in-flight. Never blocks
— style warnings only.

Companion to the `doc-hygiene` skill, which documents the same rules in
prose for the agent to read up front.
"""

from __future__ import annotations

import re

from ..core import HookPayload, HookResponse

# R1: counts of moving structure (files, tests, etc.). Hand-tuned to avoid
# matching numbers tied to a stable contract (e.g. "26 hook events" is fine —
# it's anchored to an upstream spec). The list below is structural-only.
_ROT_PRONE_COUNTS = re.compile(
    r'\b(\d+)\s+'
    r'(tests?|specs?\s+files?|spec\s+files?|views?|blueprints?|components?|'
    r'composables?|modules?|tables?|endpoints?|guides?|patterns?|procedures?|'
    r'repos?|sibling\s+repos?|lines?\s+of\s+\w+|files?\s+in\s+\w+|skills?\s+'
    r'deployed)\b',
    re.IGNORECASE,
)

# R4: aspirational / dated phrases that rot.
_STALE_PHRASES = [
    re.compile(r'\bLast updated:?\s*[0-9]{4}-[0-9]{2}-[0-9]{2}\b', re.IGNORECASE),
    re.compile(r'\bUpdated\s+[0-9]{4}-[0-9]{2}-[0-9]{2}\b', re.IGNORECASE),
    re.compile(r'\bin this milestone\b', re.IGNORECASE),
    re.compile(r'\bcurrently in progress\b', re.IGNORECASE),
    re.compile(r'\bcurrently broken\b', re.IGNORECASE),
    re.compile(r'\bwe plan to\b', re.IGNORECASE),
    re.compile(r'\bcoming soon\b', re.IGNORECASE),
]

_MAX_HITS_REPORTED = 5


def handle(payload: HookPayload) -> HookResponse | None:
    if payload.tool_name not in ('Write', 'Edit', 'MultiEdit'):
        return None

    file_path = (payload.tool_input or {}).get('file_path', '') or ''
    if not file_path.lower().endswith('.md'):
        return None

    # Ignore generated / vendored / scratch areas.
    skip_markers = ('node_modules/', '.venv/', 'dist/', '.regin/topics/proposals/')
    if any(marker in file_path for marker in skip_markers):
        return None

    new_text = _extract_new_text(payload)
    if not new_text:
        return None

    findings: list[str] = []

    for match in _ROT_PRONE_COUNTS.finditer(new_text):
        if len(findings) >= _MAX_HITS_REPORTED:
            findings.append('… (more hits truncated)')
            break
        findings.append(
            f'rot-prone count "{match.group(0)}" — replace with a pointer '
            f'to the source directory (see doc-hygiene skill, rule R1)'
        )

    for pattern in _STALE_PHRASES:
        match = pattern.search(new_text)
        if match:
            findings.append(
                f'stale phrase "{match.group(0)}" — drop it; git blame and '
                f'tracker issues are authoritative (rules R3/R4)'
            )

    if not findings:
        return None

    body = '\n'.join(f'- {f}' for f in findings)
    return HookResponse(
        additional_context=(
            f'doc-hygiene warnings for `{file_path}`:\n{body}\n'
            f'Invoke the `doc-hygiene` skill for the full ruleset.'
        ),
    )


def _extract_new_text(payload: HookPayload) -> str:
    """Pull the freshly-written markdown out of the tool input.

    - Write: full `content`
    - Edit: just the `new_string` (we only care about what was added)
    - MultiEdit: concatenated `new_string` fields
    """
    tool_input = payload.tool_input or {}
    if payload.tool_name == 'Write':
        return tool_input.get('content', '') or ''
    if payload.tool_name == 'Edit':
        return tool_input.get('new_string', '') or ''
    if payload.tool_name == 'MultiEdit':
        edits = tool_input.get('edits') or []
        return '\n'.join(
            e.get('new_string', '') for e in edits if isinstance(e, dict)
        )
    return ''
