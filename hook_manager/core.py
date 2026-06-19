"""Core types for the unified Claude Code hook manager.

Every spec event from https://code.claude.com/docs/en/hooks is represented
uniformly as a HookPayload fed into a list of Handler objects that return
HookResponse. The runner merges responses and writes a single JSON object
to stdout.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional
import functools
import re


# ── Spec events ────────────────────────────────────────────────────────
# Kept as a module-level constant so the test suite can assert that every
# event registered in REGISTRY is a real spec event, and so unknown events
# from stdin can be flagged.

# agent_type Claude Code stamps on every hook event fired by a Workflow-tool
# subagent. See HookPayload.is_workflow_subagent.
WORKFLOW_SUBAGENT_TYPE = 'workflow-subagent'

SPEC_EVENTS: frozenset[str] = frozenset({
    'Setup',
    'SessionStart',
    'SessionEnd',
    'UserPromptSubmit',
    'UserPromptExpansion',
    'PreToolUse',
    'PermissionRequest',
    'PermissionDenied',
    'PostToolUse',
    'PostToolUseFailure',
    'Notification',
    'SubagentStart',
    'SubagentStop',
    'TaskCreated',
    'TaskCompleted',
    'Stop',
    'StopFailure',
    'TeammateIdle',
    'InstructionsLoaded',
    'ConfigChange',
    'CwdChanged',
    'FileChanged',
    'WorktreeCreate',
    'WorktreeRemove',
    'PreCompact',
    'PostCompact',
    'Elicitation',
    'ElicitationResult',
})

# Events where exit-code 2 produces a blocking behavior per the spec table.
BLOCKABLE_VIA_EXIT_2: frozenset[str] = frozenset({
    'PreToolUse',
    'PermissionRequest',
    'UserPromptSubmit',
    'Stop',
    'SubagentStop',
    'TeammateIdle',
    'TaskCreated',
    'TaskCompleted',
    'ConfigChange',
    'PreCompact',
    'WorktreeCreate',
    'Elicitation',
    'ElicitationResult',
})


@dataclass
class HookPayload:
    """Normalized view of a hook stdin payload.

    Unknown fields are preserved under `raw` so unusual handlers can reach in.
    """

    event: str
    session_id: Optional[str] = None
    cwd: Optional[str] = None
    permission_mode: Optional[str] = None
    tool_name: Optional[str] = None
    tool_input: dict = field(default_factory=dict)
    tool_response: dict = field(default_factory=dict)
    prompt: str = ''
    permission_request: Optional['PermissionRequestInfo'] = None
    raw: dict = field(default_factory=dict)

    @functools.cached_property
    def resolved_provider(self):
        from lib.providers.registry import resolve_provider
        return resolve_provider(self.raw)

    @property
    def is_workflow_subagent(self) -> bool:
        """True when this event was fired by a Claude Code Workflow-tool
        subagent.

        The background dynamic-workflow runtime fires the FULL hook suite
        (PreToolUse/PostToolUse/PostToolUseFailure/SubagentStart/SubagentStop)
        into the *launching* session for every one of its agents, each tagged
        ``agent_type='workflow-subagent'``. Those runs are captured
        independently as their own ``wf_`` session by
        `lib.trace.workflow_ingest` from on-disk artifacts, so re-recording
        their per-tool / per-turn activity off these hooks just duplicates the
        whole run and floods the launching session's conversation view. Trace
        handlers use this to skip emission (keeping only the lightweight
        ``subagent.start``/``subagent.stop`` markers)."""
        raw = self.raw or {}
        agent_type = raw.get('subagent_type') or raw.get('agent_type')
        return agent_type == WORKFLOW_SUBAGENT_TYPE

    @classmethod
    def from_stdin_json(cls, event_hint: str, data: dict) -> 'HookPayload':
        """Build a payload from parsed JSON. `event_hint` is the CLI-arg
        fallback when stdin is missing `hook_event_name`."""
        normalized = _normalize_payload(data)
        event = normalized.get('hook_event_name') or event_hint
        payload = cls(
            event=event,
            session_id=normalized.get('session_id'),
            cwd=normalized.get('cwd'),
            permission_mode=normalized.get('permission_mode'),
            tool_name=normalized.get('tool_name'),
            tool_input=normalized.get('tool_input') or {},
            tool_response=normalized.get('tool_response') or {},
            prompt=_extract_prompt(normalized),
            raw=normalized,
        )
        payload.permission_request = payload.resolved_provider.build_permission_request_info(payload)
        return payload


def _extract_prompt(data: dict) -> str:
    """Prompt text can appear in several fields. Pick the first populated one."""
    candidates = [
        data.get('prompt'),
        data.get('text'),
        data.get('message'),
        (data.get('tool_input') or {}).get('text'),
        (data.get('tool_input') or {}).get('message'),
        (data.get('tool_input') or {}).get('prompt'),
        (data.get('tool_input') or {}).get('description'),
        data.get('input'),
    ]
    for c in candidates:
        if isinstance(c, str) and c.strip():
            return c.strip()
    return ''


def _to_snake(name: str) -> str:
    """Convert camelCase / PascalCase / mixed keys to snake_case."""
    if not isinstance(name, str) or not name:
        return name
    # Handle acronym boundaries first, then lower/upper boundaries.
    s1 = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1_\2', name)
    s2 = re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', s1)
    return s2.replace('-', '_').lower()


def _snake_alias(container: dict) -> dict:
    """Return a copy of `container` with snake_case aliases for str keys."""
    out = dict(container)
    for key, value in list(container.items()):
        if isinstance(key, str):
            out.setdefault(_to_snake(key), value)
    return out


def _apply_tool_field_aliases(out: dict) -> None:
    """Coalesce known tool-field synonyms onto regin's canonical names, in place.

    Some agents name the post-tool result `tool_output` and the tool-call id
    `tool_call_id` (Kimi), where the rest of regin reads `tool_response` and
    `tool_use_id`. Fill the canonical names only when absent so payloads that
    already use them (Claude/Codex) are untouched. A non-dict `tool_output`
    (sometimes a bare string) is wrapped so downstream handlers can keep doing
    `.get(...)`.

    Once the canonical name is populated the provider-native alias is dropped:
    the normalized payload is meant to be a single canonical shape, and keeping
    the redundant original around makes it (and the size-capped JSONL mirror)
    drift against the per-tool schemas as a spurious `unknown_field`. The raw
    pre-normalization stdin is still recoverable elsewhere if ever needed.
    """
    if 'tool_response' not in out and out.get('tool_output') is not None:
        tool_output = out.get('tool_output')
        out['tool_response'] = tool_output if isinstance(tool_output, dict) else {'output': tool_output}
    if 'tool_use_id' not in out and out.get('tool_call_id') is not None:
        out['tool_use_id'] = out.get('tool_call_id')
    # The canonical names now carry the value (above or already present); drop
    # the provider-native aliases so the normalized payload stays canonical.
    out.pop('tool_output', None)
    out.pop('tool_call_id', None)


def _normalize_payload(data: dict) -> dict:
    """Normalize provider payload keys to the snake_case hook schema.

    Codex hooks payloads are commonly camelCase (`sessionId`,
    `toolInput`, `transcriptPath`, ...) and Kimi uses a couple of differently
    named tool fields. Existing handlers expect the Claude-style snake_case
    names. We preserve original keys while adding canonical aliases so every
    provider's format works.
    """
    if not isinstance(data, dict):
        return {}

    out = _snake_alias(data)

    # Canonical event key used by runner + tests.
    if 'hook_event_name' not in out:
        out['hook_event_name'] = (
            out.get('hook_event_name')
            or out.get('event_name')
            or out.get('event')
        )

    _apply_tool_field_aliases(out)

    # Deep-normalize tool_input/tool_response keys used by handlers.
    for field_name in ('tool_input', 'tool_response'):
        container = out.get(field_name)
        if isinstance(container, dict):
            out[field_name] = _snake_alias(container)

    return out


@dataclass(frozen=True)
class PermissionOption:
    """One selectable way to resolve a permission request."""

    id: str
    label: str
    description: str = ''
    updated_permissions: Optional[list[dict]] = None


@dataclass(frozen=True)
class PermissionRequestInfo:
    """Provider-neutral permission request details.

    This lets handlers and tracing code talk about the requested permission
    without knowing whether the provider has a native PermissionRequest event
    or only a PreToolUse ask-style fallback.
    """

    tool_name: Optional[str]
    tool_input_summary: dict
    cwd: Optional[str]
    permission_mode: Optional[str]
    requested_permission: str
    suggestions: list[dict] = field(default_factory=list)
    options: list[PermissionOption] = field(default_factory=list)
    default_option_id: Optional[str] = None


@dataclass
class PermissionRequestDecision:
    """Rich decision object for PermissionRequest output per spec.

    This is structurally different from the flat `permission_decision` string
    used on PreToolUse/PermissionDenied — it allows programmatically modifying
    permission rules and interrupting tool calls. Use this when writing a
    handler for the PermissionRequest event; leave it None for other events.
    """

    behavior: str                              # 'allow' or 'deny'
    updated_input: Optional[dict] = None       # allow only — rewrites tool input
    updated_permissions: Optional[list[dict]] = None  # allow or deny — rule updates
    message: Optional[str] = None              # deny only — shown to Claude
    interrupt: Optional[bool] = None           # deny only — stops current tool call


@dataclass
class HookResponse:
    """A single handler's contribution. None fields = "I don't care"."""

    continue_: Optional[bool] = None
    stop_reason: Optional[str] = None
    suppress_output: Optional[bool] = None
    system_message: Optional[str] = None
    additional_context: Optional[str] = None
    permission_decision: Optional[str] = None  # allow|deny|ask|defer
    permission_reason: Optional[str] = None
    updated_input: Optional[dict] = None
    decision: Optional[str] = None             # 'block' or None
    decision_reason: Optional[str] = None
    exit_code: int = 0

    # Event-scoped fields (only meaningful on specific events per spec)
    session_title: Optional[str] = None        # UserPromptSubmit only
    retry: Optional[bool] = None               # PermissionDenied only
    updated_mcp_tool_output: object = None     # PostToolUse MCP-only
    permission_request_decision: Optional[PermissionRequestDecision] = None  # PermissionRequest only


@dataclass
class Handler:
    """A registry entry. Every hook behavior is one of these."""

    name: str
    events: list[str]          # ['*'] for match-all
    kind: str                  # 'trace' | 'gate' | 'enrich' | 'notify'
    fn: Callable[[HookPayload], Optional[HookResponse]]
    priority: int = 100
    predicate: Callable[[HookPayload], bool] = field(default=lambda p: True)
    label: Optional[str] = None
    summary: Optional[str] = None
    match_hint: Optional[str] = None

    def matches(self, payload: HookPayload) -> bool:
        if '*' not in self.events and payload.event not in self.events:
            return False
        try:
            return bool(self.predicate(payload))
        except Exception:
            return False


# ── Predicate helpers ──────────────────────────────────────────────────

def match_tool(*names: str) -> Callable[[HookPayload], bool]:
    """Match if payload.tool_name is any of `names` (exact match)."""
    wanted = set(names)
    return lambda p: p.tool_name in wanted


def match_tool_regex(pattern: str) -> Callable[[HookPayload], bool]:
    """Match if payload.tool_name matches a regex."""
    import re
    rx = re.compile(pattern)
    return lambda p: bool(p.tool_name and rx.search(p.tool_name))


def match_bash_command(pattern: str) -> Callable[[HookPayload], bool]:
    """Match a Bash tool call whose `command` matches a regex."""
    import re
    rx = re.compile(pattern)
    def _m(p: HookPayload) -> bool:
        if p.tool_name != 'Bash':
            return False
        cmd = (p.tool_input or {}).get('command') or ''
        return bool(rx.search(cmd))
    return _m


def always() -> Callable[[HookPayload], bool]:
    return lambda p: True
