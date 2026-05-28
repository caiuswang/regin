"""Handler implementations for the hook_manager registry.

Explicit submodule re-exports so static analyzers (Pyright, mypy) can
resolve `from hook_manager.handlers import commit_guard` without ambiguity.
At runtime Python would resolve these submodules lazily anyway; this just
makes the contract visible.
"""

from . import commit_guard as commit_guard
from . import compact_lifecycle as compact_lifecycle
from . import cwd_changed as cwd_changed
from . import file_changed as file_changed
from . import rule_check as rule_check
from . import misc_events as misc_events
from . import permission_events as permission_events
from . import plan_trace as plan_trace
from . import post_tool_failure as post_tool_failure
from . import post_tool_trace as post_tool_trace
from . import prompt_trace as prompt_trace
from . import session_lifecycle as session_lifecycle
from . import skill_read as skill_read
from . import subagent_lifecycle as subagent_lifecycle
from . import task_lifecycle as task_lifecycle
from . import trace_payload as trace_payload
from . import turn_trace as turn_trace

__all__ = [
    'commit_guard',
    'compact_lifecycle',
    'cwd_changed',
    'file_changed',
    'rule_check',
    'misc_events',
    'permission_events',
    'plan_trace',
    'post_tool_failure',
    'post_tool_trace',
    'prompt_trace',
    'session_lifecycle',
    'skill_read',
    'subagent_lifecycle',
    'task_lifecycle',
    'trace_payload',
    'turn_trace',
]
