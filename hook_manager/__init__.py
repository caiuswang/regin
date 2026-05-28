"""Claude Code unified hook manager.

Public API:
    from hook_manager import HookPayload, HookResponse, Handler
    from hook_manager.runner import run, main
"""

from .core import (
    BLOCKABLE_VIA_EXIT_2,
    Handler,
    HookPayload,
    HookResponse,
    PermissionRequestDecision,
    SPEC_EVENTS,
    always,
    match_bash_command,
    match_tool,
    match_tool_regex,
)
from .merge import merge_responses, response_to_json

__all__ = [
    'BLOCKABLE_VIA_EXIT_2',
    'Handler',
    'HookPayload',
    'HookResponse',
    'PermissionRequestDecision',
    'SPEC_EVENTS',
    'always',
    'match_bash_command',
    'match_tool',
    'match_tool_regex',
    'merge_responses',
    'response_to_json',
]
