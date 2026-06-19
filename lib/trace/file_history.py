"""Code-rewind support: read Claude Code's `file-history-snapshot` rows and
the on-disk backup store so a `/rewind` can report *which files it reverted*
and serve a before/after diff.

Two data sources, both written by Claude Code:

  * `type:"file-history-snapshot"` transcript rows. Each carries
    `snapshot.trackedFileBackups` — `{ <abs path>: {backupFileName:
    "<pathhash>@v<N>", version: N, backupTime} }` — keyed by `messageId`.
    This is a *pointer*, not content: it records which stored version of
    each tracked file was current at that message.
  * `~/.claude/file-history/<sessionId>/<pathhash>@v<N>` — the physical
    store, holding a COMPLETE copy of the file at each version (not a diff),
    so any two versions reconstruct a real before/after.

The pure half (`parse_snapshot_rows`, `version_map_for`,
`rolled_back_files`) runs during transcript parsing — no disk. The disk half
(`backup_path`, `read_backup`, `diff_versions`) is lazy: only the
`/spans/<id>/rewind` route touches it, so the map payload never inlines file
contents.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# Each snapshot row's tracked map, keyed by messageId.
SnapshotMap = dict[str, "dict[str, TrackedBackup]"]


@dataclass(frozen=True)
class TrackedBackup:
    """One file's backup pointer within a snapshot. `backup_file_name` is the
    `<pathhash>@v<N>` store filename (may be None for an as-yet-unbacked
    first version); `version` increases as the file is re-edited."""

    backup_file_name: str | None
    version: int
    backup_time: str | None


def parse_snapshot_rows(rows: list[dict]) -> SnapshotMap:
    """Parse raw `file-history-snapshot` entries into `messageId -> {path:
    TrackedBackup}`. Tolerant of partial rows (missing keys are skipped)."""
    out: SnapshotMap = {}
    for row in rows:
        snap = row.get("snapshot") or {}
        mid = snap.get("messageId")
        tracked = snap.get("trackedFileBackups")
        if not isinstance(mid, str) or not isinstance(tracked, dict):
            continue
        parsed: dict[str, TrackedBackup] = {}
        for path, v in tracked.items():
            if not isinstance(v, dict):
                continue
            parsed[path] = TrackedBackup(
                backup_file_name=v.get("backupFileName"),
                version=int(v.get("version") or 0),
                backup_time=v.get("backupTime"),
            )
        out[mid] = parsed
    return out


def version_map_for(
    uuids: list[str],
    snapshots: SnapshotMap,
) -> dict[str, TrackedBackup]:
    """The file state reached across a set of messages: per path, the
    highest-version backup seen in any of their snapshots. `uuids` is given
    in file order so equal versions resolve to the latest occurrence."""
    out: dict[str, TrackedBackup] = {}
    for uuid in uuids:
        snap = snapshots.get(uuid)
        if not snap:
            continue
        for path, backup in snap.items():
            cur = out.get(path)
            if cur is None or backup.version >= cur.version:
                out[path] = backup
    return out


def rolled_back_files(
    abandoned: dict[str, TrackedBackup],
    baseline: dict[str, TrackedBackup],
) -> list[dict]:
    """Files whose edits the rewind undid: every path the abandoned branch
    advanced to a version that differs from the fork-point baseline.

    `before_ref` = the abandoned-tip backup (rolled back FROM); `after_ref`
    = the baseline backup restored TO (None when the file did not exist at
    the fork — i.e. the abandoned branch created it). Paths the abandoned
    branch merely tracked without changing (version unchanged) are omitted.
    """
    out: list[dict] = []
    for path, ab in abandoned.items():
        base = baseline.get(path)
        if base is not None and base.version == ab.version:
            continue
        out.append({
            "path": path,
            "before_ref": ab.backup_file_name,
            "after_ref": base.backup_file_name if base else None,
        })
    out.sort(key=lambda d: d["path"])
    return out


# ---- disk half (lazy; only the /rewind route reaches here) ----------------

def _store_root(base_dir: str | None) -> str:
    return base_dir or os.path.expanduser("~/.claude/file-history")


def backup_path(
    session_id: str,
    backup_file_name: str,
    *,
    base_dir: str | None = None,
) -> str:
    """Absolute path of a `<pathhash>@v<N>` backup in the per-session store."""
    return os.path.join(_store_root(base_dir), session_id, backup_file_name)


def read_backup(
    session_id: str,
    backup_file_name: str | None,
    *,
    base_dir: str | None = None,
) -> str | None:
    """Full text of a stored backup version, or None when the ref is absent
    (a file that did not exist at that version) or unreadable."""
    if not backup_file_name:
        return None
    path = backup_path(session_id, backup_file_name, base_dir=base_dir)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def diff_versions(
    session_id: str,
    file_entry: dict,
    *,
    base_dir: str | None = None,
) -> dict:
    """Resolve one `rolled_back_files` entry into before/after text for the
    diff view. `file_entry` is `{path, before_ref, after_ref}`."""
    return {
        "path": file_entry.get("path"),
        "before_ref": file_entry.get("before_ref"),
        "after_ref": file_entry.get("after_ref"),
        "before_text": read_backup(
            session_id, file_entry.get("before_ref"), base_dir=base_dir,
        ),
        "after_text": read_backup(
            session_id, file_entry.get("after_ref"), base_dir=base_dir,
        ),
    }
