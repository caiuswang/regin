"""Agent bridge: HTTP POST → guarded tmux keystroke injection.

Resolves a live claude session's tmux pane from the SessionStart registry
(`bridge_panes`, written by `hook_manager/handlers/bridge_registry.py`) and
delivers a sanitized message into it under the design's delivery guards
(see `docs/agent-bridge-design.md`). Nothing here runs unless
`settings.agent_bridge.enabled` is on and the target session opted in with
`REGIN_BRIDGE=1` at launch.
"""
