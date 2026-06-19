"""One canonical "compact a tool_input for display" helper.

Several call sites need a small, capped projection of a tool call's input —
the display-worthy keys only (command / file path / pattern / url), so a trace
card or a deny span can show *what* a tool did without storing whole file
bodies. This used to be copy-pasted three ways (the Claude and Codex provider
adapters, and the Kimi transcript parser) with slightly different key lists and
caps, which drift independently. Keep the projection here and parameterise the
few real differences instead.
"""

from __future__ import annotations

# The display-worthy string keys, in render order. `command` carries its own
# (larger) cap because a shell command is the one field worth showing in full.
_DEFAULT_KEYS = ("command", "description", "file_path", "path", "pattern", "url")


def summarize_tool_input(
    tool_input: object,
    *,
    keys: tuple[str, ...] = _DEFAULT_KEYS,
    command_cap: int = 500,
    str_cap: int = 500,
    include_replace_all: bool = False,
) -> dict:
    """Capped projection of `tool_input` over `keys` (string values only).

    `command_cap` caps the `command` key; every other key uses `str_cap`.
    `include_replace_all` carries the boolean `replace_all` through verbatim
    (Edit/Write). Returns `{}` for a non-dict or no matching keys — callers
    that prefer `None` can `summarize_tool_input(...) or None`.
    """
    if not isinstance(tool_input, dict):
        return {}
    summary: dict = {}
    for key in keys:
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            summary[key] = value[: command_cap if key == "command" else str_cap]
    if include_replace_all and "replace_all" in tool_input:
        summary["replace_all"] = tool_input["replace_all"]
    return summary
