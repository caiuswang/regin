"""Sessions regin launches and owns, via the Claude Agent SDK.

The second capture tier. `lib/agent_bridge` reaches a session the user started
by typing into its tmux pane; this package starts the session itself and keeps a
typed channel to it, so an `AskUserQuestion` is answered by handing structured
input back to the tool rather than by driving its on-screen widget.

Gated off by default (`settings.agent_sdk.enabled`). See `client.py` for why the
raw SDK import is confined to one module.
"""

from .registry import (
    interrupt_run,
    is_sdk_owned,
    is_starting,
    queued_prompts,
    resolve_ask,
    resolve_permission,
    stop_run,
    submit_prompt,
)

__all__ = ['interrupt_run', 'is_sdk_owned', 'is_starting', 'queued_prompts',
           'resolve_ask', 'resolve_permission', 'stop_run', 'submit_prompt']
