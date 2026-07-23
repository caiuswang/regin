#!/usr/bin/env python3
"""Drop temp-directory project entries from ``~/.claude.json``.

The `claude` CLI registers every cwd it is launched in under
``projects``, and never evicts. Anything that spawns it in a scratch
workspace (pytest tmp_path, the topics proposal/review agent, bridge
scratch dirs) therefore leaves a permanent entry behind, and the file
is re-read on every startup.

Default policy is conservative: only entries rooted in a temp directory
*whose path no longer exists* are removed. ``--include-live`` also drops
temp entries that still exist on disk.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

TEMP_ROOTS = tuple(
    os.path.realpath(p) for p in {tempfile.gettempdir(), "/tmp", "/var/folders"}
)


def is_temp(path: str) -> bool:
    try:
        resolved = os.path.realpath(path)
    except OSError:
        return False
    return any(
        resolved == root or resolved.startswith(root + os.sep) for root in TEMP_ROOTS
    )


def _doomed(projects: dict, include_live: bool) -> list[str]:
    keep_live = not include_live
    return [
        p for p in projects
        if is_temp(p) and not (keep_live and os.path.isdir(p))
    ]


def _rewrite(config: Path, data: dict, doomed: list[str]) -> Path:
    backup = config.with_suffix(".json.bak")
    shutil.copy2(config, backup)
    for path in doomed:
        data["projects"].pop(path, None)
    tmp_out = config.with_suffix(".json.prune-tmp")
    tmp_out.write_text(json.dumps(data, indent=2))
    os.replace(tmp_out, config)
    return backup


def prune(config: Path, *, include_live: bool, apply: bool) -> int:
    data = json.loads(config.read_text())
    projects = data.get("projects") or {}
    doomed = _doomed(projects, include_live)
    total = len(projects)
    if not doomed:
        print(f"{config}: nothing to prune ({total} projects)")
        return 0

    for path in doomed:
        print(("dropped " if apply else "would drop ") + path)
    if not apply:
        print(f"\n{len(doomed)} of {total} projects; re-run with --apply")
        return 0

    before = config.stat().st_size
    backup = _rewrite(config, data, doomed)
    print(
        f"\npruned {len(doomed)} of {total} projects; "
        f"{before} -> {config.stat().st_size} bytes (backup: {backup})"
    )
    return 0


def default_config() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home())) / ".claude.json"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", type=Path, default=default_config())
    ap.add_argument("--include-live", action="store_true",
                    help="also drop temp entries whose directory still exists")
    ap.add_argument("--apply", action="store_true",
                    help="write the file (default is a dry run)")
    args = ap.parse_args(argv)
    if not args.config.exists():
        print(f"{args.config}: not found", file=sys.stderr)
        return 1
    return prune(args.config, include_live=args.include_live, apply=args.apply)


if __name__ == "__main__":
    raise SystemExit(main())
