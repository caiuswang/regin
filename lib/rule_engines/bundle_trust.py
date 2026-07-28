"""Trust gate for repo-shipped rule bundles.

A bundle discovered under `<repo>/.regin/rules/` names a runner script that
regin executes on every matching edit. Loading one straight from a cloned
repo would hand that repo's author code execution on your machine — the
objection `lib/repo_config.py` raises against overlaying `rule_engines` from
repo-local config. This module is the boundary that makes it safe:

  discovered  → regin parses the manifest and lists the rules (read-only)
  trusted     → regin also *runs* the bundle's runner

Trust is recorded per `(repo path, bundle id)` together with a fingerprint of
the bundle's **code** — the runner entry plus everything under `checkers_dir`.
Rule YAML is deliberately excluded: tightening a threshold or adding a rule is
data, and re-prompting for it would train users to click through the prompt
that actually matters. Changing a checker or the runner *is* new code, so the
fingerprint moves and the bundle drops back to discovered-only until the user
re-trusts it. That is what makes `git pull` safe.

Storage: a single JSON file at `<data_dir>/trusted_bundles.json`, shaped
`{"<repo-realpath>": {"<bundle-id>": "<fingerprint>"}}`.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Iterator

from lib.activity_log import get_activity_logger as _get_activity_logger
from lib.settings import settings


def _rules_log():
    return _get_activity_logger("rules")


_TRUST_FILENAME = "trusted_bundles.json"
# Dependency/junk directories never contribute to the code fingerprint: a
# bundle's `npm install` would otherwise invalidate trust on every machine.
_SKIP_DIR_NAMES = frozenset({"node_modules", ".git", "__pycache__"})


def _path() -> Path:
    return Path(settings.data_dir) / _TRUST_FILENAME


def _repo_key(repo_path: str | Path) -> str:
    return os.path.realpath(os.path.expanduser(str(repo_path))).rstrip(os.sep)


# ── Fingerprint ────────────────────────────────────────────────────────


def _iter_code_files(bundle_root: Path, manifest) -> Iterator[Path]:
    """Yield the bundle files whose contents constitute executable code.

    The runner entry plus every file under `checkers_dir`. Missing paths are
    simply not yielded — a bundle with a dangling runner fingerprints fine and
    fails later at execution, which is the honest failure mode.
    """
    entry = bundle_root / manifest.runner.entry
    if entry.is_file():
        yield entry
    stack = [bundle_root / manifest.checkers_dir]
    while stack:
        current = stack.pop()
        try:
            entries = sorted(current.iterdir())
        except OSError:
            continue
        for item in entries:
            if item.is_dir():
                if item.name not in _SKIP_DIR_NAMES and not item.name.startswith("."):
                    stack.append(item)
            elif item.is_file():
                yield item


def fingerprint(bundle_root: str | Path, manifest) -> str:
    """Stable sha256 over the bundle's executable code.

    Each file contributes its path relative to the bundle root plus its
    content hash, so a rename counts as a change.
    """
    root = Path(bundle_root).resolve()
    digest = hashlib.sha256()
    parts: list[tuple[str, str]] = []
    for path in _iter_code_files(root, manifest):
        try:
            body = path.read_bytes()
        except OSError:
            continue
        rel = os.path.relpath(path, root).replace(os.sep, "/")
        parts.append((rel, hashlib.sha256(body).hexdigest()))
    for rel, file_hash in sorted(parts):
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_hash.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


# ── Store ──────────────────────────────────────────────────────────────


def load() -> dict[str, dict[str, str]]:
    """The trust store, or `{}` when absent/corrupt (never raises)."""
    path = _path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text() or "{}")
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(repo): {str(bid): str(fp) for bid, fp in entries.items()}
        for repo, entries in data.items()
        if isinstance(entries, dict)
    }


def save(data: dict[str, dict[str, str]]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    cleaned = {repo: entries for repo, entries in data.items() if entries}
    path.write_text(json.dumps(cleaned, indent=2, sort_keys=True))


# ── Queries ────────────────────────────────────────────────────────────


def trusted_fingerprint(repo_path: str | Path, bundle_id: str) -> str | None:
    """The fingerprint the user approved for this bundle, if any."""
    return load().get(_repo_key(repo_path), {}).get(bundle_id)


def is_trusted(repo_path: str | Path, bundle_id: str, current: str) -> bool:
    """True when this exact code has been approved for this repo's bundle."""
    return trusted_fingerprint(repo_path, bundle_id) == current


def describe(repo_path: str | Path, bundle_id: str, current: str) -> dict:
    """Trust state for UI/CLI: never trusted, trusted, or code changed since."""
    approved = trusted_fingerprint(repo_path, bundle_id)
    return {
        "trusted": approved == current,
        "known": approved is not None,
        "code_changed": approved is not None and approved != current,
        "fingerprint": current,
    }


# ── Mutations ──────────────────────────────────────────────────────────


def trust(repo_path: str | Path, bundle_id: str, current: str) -> None:
    """Approve this exact bundle code for execution inside `repo_path`."""
    data = load()
    key = _repo_key(repo_path)
    data.setdefault(key, {})[bundle_id] = current
    save(data)
    _rules_log().write(
        "repo_bundle_trusted",
        repo_path=key, bundle_id=bundle_id, fingerprint=current[:12],
    )


def untrust(repo_path: str | Path, bundle_id: str | None = None) -> int:
    """Revoke trust for one bundle, or every bundle in the repo.

    Returns how many entries were removed.
    """
    data = load()
    key = _repo_key(repo_path)
    entries = data.get(key)
    if not entries:
        return 0
    if bundle_id is None:
        removed = len(entries)
        data.pop(key, None)
    else:
        removed = 1 if entries.pop(bundle_id, None) is not None else 0
        if not entries:
            data.pop(key, None)
    save(data)
    _rules_log().write(
        "repo_bundle_untrusted",
        repo_path=key, bundle_id=bundle_id or "*", removed=removed,
    )
    return removed


__all__ = [
    "fingerprint", "load", "save", "trusted_fingerprint", "is_trusted",
    "describe", "trust", "untrust",
]
