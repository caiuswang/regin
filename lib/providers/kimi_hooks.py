"""Install/uninstall regin's hook_manager router into Kimi Code's config.toml.

Kimi Code CLI reads lifecycle hooks from a TOML ``[[hooks]]`` array inside
``~/.kimi-code/config.toml`` — not the JSON ``settings.json`` map that Claude
and Codex use. Rather than depend on a TOML *writer* (none ships in the venv),
we manage our entries inside a clearly delimited block appended to the file.
That keeps the user's hand-written config (providers, models, oauth) intact
byte-for-byte and makes uninstall an exact, reversible operation.

State (which events are routed to us) is read back with stdlib ``tomllib`` so
detection is robust even if the block markers are edited away.

The blueprint owns command construction and the "is this command ours"
predicate; this module is given them as callables so the per-checkout
interpreter-prefix scoping stays in one place.
"""

from __future__ import annotations

import json
import os
import re
import tomllib
from typing import Callable

# The default managed block (hook_manager router). A `label` lets independent
# installers (e.g. the debug fan-out hook) own a *separate* delimited block in
# the same config.toml so they never clobber each other on install/uninstall.
_DEFAULT_LABEL = "hook_manager"


def _begin(label: str) -> str:
    return f"# >>> regin {label} (managed — edit via regin, not by hand) >>>"


def _end(label: str) -> str:
    return f"# <<< regin {label} (managed) <<<"


def _block_re(label: str) -> re.Pattern:
    # Greedy-safe: match the whole managed block plus any surrounding blank lines.
    return re.compile(
        r"\n*" + re.escape(_begin(label)) + r".*?" + re.escape(_end(label)) + r"\n?",
        re.DOTALL,
    )


# Back-compat aliases for the default (hook_manager) block.
_BEGIN = _begin(_DEFAULT_LABEL)
_END = _end(_DEFAULT_LABEL)
_BLOCK_RE = _block_re(_DEFAULT_LABEL)


def _read_text(path: str) -> str:
    try:
        with open(path, "r") as f:
            return f.read()
    except FileNotFoundError:
        return ""


def _read_hooks(path: str) -> list[dict]:
    """Parsed ``[[hooks]]`` entries, or [] when the file is missing/invalid."""
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except (FileNotFoundError, tomllib.TOMLDecodeError):
        return []
    hooks = data.get("hooks")
    if not isinstance(hooks, list):
        return []
    return [h for h in hooks if isinstance(h, dict)]


def routed_events(path: str, is_ours: Callable[[str], bool]) -> set[str]:
    """Events whose installed hook command belongs to this regin checkout."""
    out: set[str] = set()
    for hook in _read_hooks(path):
        command = hook.get("command")
        event = hook.get("event")
        if isinstance(command, str) and isinstance(event, str) and is_ours(command):
            out.add(event)
    return out


def _toml_str(value: str) -> str:
    """Render a TOML basic string. JSON string escaping is a valid subset for
    the ASCII paths/commands we emit."""
    return json.dumps(value, ensure_ascii=False)


def _render_block(
    events: list[str],
    command_for_event: Callable[[str], str],
    timeout: int,
    label: str,
) -> str:
    lines = [_begin(label)]
    for event in events:
        lines.append("[[hooks]]")
        lines.append(f"event = {_toml_str(event)}")
        lines.append(f"command = {_toml_str(command_for_event(event))}")
        lines.append(f"timeout = {int(timeout)}")
        lines.append("")
    lines.append(_end(label))
    return "\n".join(lines)


def install(
    path: str,
    events: list[str],
    command_for_event: Callable[[str], str],
    *,
    timeout: int = 60,
    label: str = _DEFAULT_LABEL,
) -> None:
    """Write (or rewrite) one managed hook block, preserving all other config.

    `label` scopes the delimited block, so independent installers (the
    hook_manager router and the debug fan-out hook) each own their own block
    in the same config.toml and never overwrite one another.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    existing = _read_text(path)
    cleaned = _block_re(label).sub("", existing).rstrip()
    block = _render_block(sorted(events), command_for_event, timeout, label)
    body = f"{cleaned}\n\n{block}\n" if cleaned else f"{block}\n"
    with open(path, "w") as f:
        f.write(body)


def uninstall(path: str, *, label: str = _DEFAULT_LABEL) -> bool:
    """Strip the labelled managed hook block. Returns True if anything was removed."""
    existing = _read_text(path)
    if not existing:
        return False
    cleaned = _block_re(label).sub("", existing)
    if cleaned == existing:
        return False
    with open(path, "w") as f:
        f.write(cleaned.rstrip() + "\n")
    return True
