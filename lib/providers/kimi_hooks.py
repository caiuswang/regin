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


def installed_commands(path: str, is_ours: Callable[[str], bool]) -> set[str]:
    """The distinct command strings installed for one hook, as written.

    Lets a caller tell "already installed" from "installed, but with a stale
    command that needs rewriting".
    """
    return {
        hook["command"]
        for hook in _read_hooks(path)
        if isinstance(hook.get("command"), str) and is_ours(hook["command"])
    }


def _installed_field_map(path: str, is_ours: Callable[[str], bool], field: str) -> dict[str, list]:
    out: dict[str, list] = {}
    for hook in _read_hooks(path):
        command = hook.get("command")
        event = hook.get("event")
        if isinstance(command, str) and isinstance(event, str) and is_ours(command):
            out.setdefault(event, []).append(hook.get(field))
    return out


def installed_command_map(path: str, is_ours: Callable[[str], bool]) -> dict[str, list[str]]:
    """Event → the commands of ours written for it, as they appear on disk.

    Per-event granularity is what lets a caller tell a stale command from a
    missing route; `installed_commands` flattens that away.
    """
    return _installed_field_map(path, is_ours, "command")


def installed_timeout_map(path: str, is_ours: Callable[[str], bool]) -> dict[str, list]:
    """Event → the timeout written beside each of our commands."""
    return _installed_field_map(path, is_ours, "timeout")


def _toml_str(value: str) -> str:
    """Render a TOML basic string. JSON string escaping is a valid subset for
    the ASCII paths/commands we emit."""
    return json.dumps(value, ensure_ascii=False)


# A whole-line table header: `[foo]`, `[[hooks]]`, `[a."b c"]`, with an
# optional trailing comment. A row of a multi-line array (`  [1, 2]`) is
# indistinguishable from one by shape, so the regex alone is not enough —
# `_entry_span` only consults it at bracket depth 0. Treating an array row as
# the next table would split an entry mid-value and leave its tail behind as
# unparseable TOML, costing the user their models/providers/oauth config.
_TABLE_HEADER_RE = re.compile(r"^\s*\[\[?[^\[\]]+\]\]?\s*(?:#.*)?$")
_HOOKS_HEADER_RE = re.compile(r"^\s*\[\[\s*hooks\s*\]\]\s*(?:#.*)?$")
_COMMAND_LINE_RE = re.compile(r"^\s*command\s*=\s*(.+?)\s*$")
_STRING_SPAN_RE = re.compile(r'"(?:\\.|[^"\\])*"' + r"|'[^']*'")


def _entry_command(line: str) -> str:
    """The command string of a ``command = "…"`` line, or ''."""
    m = _COMMAND_LINE_RE.match(line)
    if not m:
        return ""
    raw = m.group(1)
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return raw.strip("'\"")


def _multiline_state(line: str, state: str | None) -> str | None:
    """Track an open ``\"\"\"``/``'''`` string, so a `[foo]` line *inside* a
    multi-line value is not read as the next table header."""
    for delim in ('"""', "'''"):
        count = line.count(delim)
        if not count or count % 2 == 0:
            continue
        if state is None:
            state = delim
        elif state == delim:
            state = None
    return state


def _bracket_delta(line: str) -> int:
    """Net unclosed ``[`` contributed by a value line, ignoring brackets that
    live inside a quoted string or a comment."""
    bare = _STRING_SPAN_RE.sub("", line).split("#", 1)[0]
    return bare.count("[") - bare.count("]")


def _entry_span(lines: list[str], start: int) -> tuple[int, int]:
    """``(end of the entry's content, start of the next table)``.

    Content ends at the entry's last key line: trailing comments and blank
    lines belong to the user, not to us, and removing an entry must not take
    them with it.
    """
    last_content = start
    state: str | None = None
    depth = 0
    j = start + 1
    while j < len(lines):
        line = lines[j]
        if state is None and depth == 0 and _TABLE_HEADER_RE.match(line):
            break
        state = _multiline_state(line, state)
        if state is None:
            depth = max(0, depth + _bracket_delta(line))
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            last_content = j
        j += 1
    return last_content + 1, j


def strip_entries(text: str, is_ours: Callable[[str], bool]) -> str:
    """Drop every ``[[hooks]]`` entry whose command satisfies `is_ours`.

    Entries written before the delimited managed block existed carry no
    markers, so `_block_re` cannot see them: install would append a second
    (marked) copy and uninstall would leave the originals firing forever.
    Matching on the command itself makes both operations reach them.
    """
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        if not _HOOKS_HEADER_RE.match(lines[i]):
            out.append(lines[i])
            i += 1
            continue
        end, next_table = _entry_span(lines, i)
        if not any(is_ours(_entry_command(ln)) for ln in lines[i:end]):
            out.extend(lines[i:next_table])
            i = next_table
            continue
        # Drop the entry plus the blank line that separated it from what
        # follows; anything else in the gap is the user's and stays.
        tail = end
        while tail < next_table and not lines[tail].strip():
            tail += 1
        out.extend(lines[tail:next_table])
        i = next_table
    body = "\n".join(out)
    return body + "\n" if body and text.endswith("\n") else body


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
    is_ours: Callable[[str], bool] | None = None,
) -> None:
    """Write (or rewrite) one managed hook block, preserving all other config.

    `label` scopes the delimited block, so independent installers (the
    hook_manager router and the debug fan-out hook) each own their own block
    in the same config.toml and never overwrite one another.

    `is_ours` additionally clears unmarked legacy entries for the same hook, so
    a reinstall refreshes a stale command instead of duplicating it.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    existing = _read_text(path)
    cleaned = _block_re(label).sub("", existing)
    if is_ours is not None:
        cleaned = strip_entries(cleaned, is_ours)
    cleaned = cleaned.rstrip()
    block = _render_block(sorted(events), command_for_event, timeout, label)
    body = f"{cleaned}\n\n{block}\n" if cleaned else f"{block}\n"
    with open(path, "w") as f:
        f.write(body)


def uninstall(
    path: str,
    *,
    label: str = _DEFAULT_LABEL,
    is_ours: Callable[[str], bool] | None = None,
) -> bool:
    """Strip the labelled managed hook block. Returns True if anything was removed.

    `is_ours` also removes unmarked legacy entries for the same hook, which
    predate the delimited block and are otherwise unreachable.
    """
    existing = _read_text(path)
    if not existing:
        return False
    cleaned = _block_re(label).sub("", existing)
    if is_ours is not None:
        cleaned = strip_entries(cleaned, is_ours)
    if cleaned == existing:
        return False
    with open(path, "w") as f:
        f.write(cleaned.rstrip() + "\n")
    return True
