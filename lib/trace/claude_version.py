"""Detect the active Claude Code CLI version, cached.

Used by payload schema drift tracking so each finding records which
Claude version was running when the drift was observed. This lets a
single schema represent multiple compatible versions over time, and
lets reviewers tell whether a `tool_response` shape change correlates
with a client upgrade or is a server-side change at fixed client.
"""

from __future__ import annotations

import functools
import os
import subprocess


@functools.lru_cache(maxsize=1)
def current_claude_version() -> str | None:
    """Return e.g. '1.0.42' or None if the CLI isn't on PATH.

    Honours $CLAUDE_VERSION as an override so tests can pin a value
    without invoking a subprocess.
    """
    override = os.environ.get("CLAUDE_VERSION")
    if override:
        return override.strip() or None
    try:
        result = subprocess.run(
            ["claude", "--version"],
            capture_output=True, text=True, timeout=2, check=False,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    out = (result.stdout or "").strip() or (result.stderr or "").strip()
    if not out:
        return None
    # Output looks like "1.0.42 (Claude Code)" — keep the leading token.
    return out.split()[0] if out.split() else out
