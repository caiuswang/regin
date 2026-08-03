"""The child environment for a process regin launches on its own behalf.

A launched process inherits this one's environment, `TMUX_PANE` included, so
its own SessionStart hook would register the pane regin's *server* runs in as
that session's bridge pane. A steer aimed at the agent would then be typed into
the operator's terminal — into a pane running `regin serve`, not claude. A
launched session has no pane of its own (the typed channel or the SDK stream is
how it is reached), so the bridge is switched off for every such child, at every
spawn site.
"""

from __future__ import annotations

from collections.abc import Mapping


def child_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """`base` with the bridge forced off.

    Forced, not defaulted: the flag is a safety property of *who launched the
    process*, which no per-run option is in a position to overrule. Called with
    no base it returns the overlay alone, for launch surfaces (the Agent SDK)
    that merge their `env` over the parent environment themselves.
    """
    return {**(base or {}), "REGIN_BRIDGE": "0"}
