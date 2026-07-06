"""Constants, types, paths, IO, and lifecycle for the repo-local topic graph.

Topic data lives inside each repository under ``.regin/topics``. The
approved graph is human-governed; scans refresh refs for existing
topics but never create approved topics.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TOPICS_DIR = ".regin/topics"
TOPIC_FILE = "topic.json"
TOPIC_LOCAL_FILE = "topic.local.json"
TOPIC_SPLIT_DIR = "topics"
TOPIC_META_FILE = "_meta.json"
SCHEMA_VERSION = 1

REF_ROLES = {
    "overview", "architecture", "entrypoint", "api", "schema",
    "test", "migration", "implementation", "config", "docs",
}
# A ref's `tier` is an axis orthogonal to `role`: `role` says *what kind of
# file* it is, `tier` says *how central it is to the wiki narrative*. A
# `primary` ref is one the wiki actually explains (so a change under it can
# genuinely stale the narrative); a `reference` ref is a mere pointer/example
# the wiki names but doesn't narrate, so a change under it is drift noise.
# Kept a string enum (not a boolean) so a future middle tier is purely
# additive — no migration, existing rows keep their meaning.
REF_TIERS = {"primary", "reference"}
DEFAULT_REF_TIER = "primary"
# Tiers whose refs are excluded from content-drift detection. The single
# extension point for the drift filter: opting a future tier out of drift is a
# one-line change here.
NON_DRIFTING_REF_TIERS = {"reference"}
TOPIC_STATUSES = {"active", "draft", "deprecated", "archived"}
EDGE_TYPES = {"related", "depends_on", "part_of", "supersedes"}
IGNORED_DIRS = {
    ".git", ".hg", ".svn", ".tox", ".venv", "venv", "node_modules",
    "dist", "build", "target", ".next", ".nuxt", ".cache", "__pycache__",
    "coverage", "htmlcov", ".pytest_cache", ".mypy_cache",
}
IGNORED_SUFFIXES = {
    ".pyc", ".pyo", ".class", ".o", ".so", ".dylib", ".dll", ".exe",
    ".min.js", ".map", ".lock", ".sqlite", ".db",
}
DEFAULT_EXCLUDES = (
    ".git/**", "node_modules/**",
    "frontend/node_modules/**", "dist/**", "build/**", "target/**",
    ".venv/**", "venv/**", "__pycache__/**", ".pytest_cache/**",
    "coverage/**", "htmlcov/**",
)
ROLE_ORDER = [
    "overview", "architecture", "entrypoint", "api", "schema",
    "implementation", "test", "migration", "config", "docs",
]


class TopicGraphError(Exception):
    """Raised for invalid topic graph operations."""


@dataclass
class ValidationResult:
    errors: list[str]
    warnings: list[str]

    @property
    def ok(self) -> bool:
        return not self.errors


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def repo_name(repo_path: str | Path) -> str:
    return Path(repo_path).resolve().name


def topic_dir(repo_path: str | Path) -> Path:
    return Path(repo_path) / TOPICS_DIR


def topic_path(repo_path: str | Path) -> Path:
    return topic_dir(repo_path) / TOPIC_FILE


def topic_local_path(repo_path: str | Path) -> Path:
    return topic_dir(repo_path) / TOPIC_LOCAL_FILE


def topic_split_dir(repo_path: str | Path) -> Path:
    return topic_dir(repo_path) / TOPIC_SPLIT_DIR


def topic_meta_path(repo_path: str | Path) -> Path:
    return topic_split_dir(repo_path) / TOPIC_META_FILE


def split_layout_active(repo_path: str | Path) -> bool:
    """True when the repo carries the split per-topic layout
    (``.regin/topics/topics/`` with at least one ``*.json``). The split
    layout wins over a stray legacy ``topic.json`` when both exist."""
    d = topic_split_dir(repo_path)
    return d.is_dir() and any(d.glob("*.json"))


def graph_exists(repo_path: str | Path) -> bool:
    """True when an approved graph is on disk in either layout."""
    return split_layout_active(repo_path) or topic_path(repo_path).exists()


def empty_graph(repo_path: str | Path) -> dict[str, Any]:
    return {
        "version": SCHEMA_VERSION,
        "repo": repo_name(repo_path),
        "updated_at": utc_now(),
        "topics": {},
    }


def bootstrap(repo_path: str | Path, *, force: bool = False, seeds: bool = False) -> dict[str, Path]:
    repo = Path(repo_path)
    topic_dir(repo).mkdir(parents=True, exist_ok=True)
    existing = topic_split_dir(repo) if split_layout_active(repo) else topic_path(repo)
    if existing.exists() and not force:
        raise TopicGraphError(f"{existing} already exists")

    graph = empty_graph(repo)
    if seeds:
        graph["topics"] = seed_topics(repo)
    return {"topic": write_graph_to_disk(repo, graph)}


def seed_topics(repo_path: str | Path) -> dict[str, Any]:
    seeds: dict[str, Any] = {
        "overview": {
            "label": "Overview",
            "aliases": ["readme", repo_name(repo_path)],
            "intent": "High-level project orientation and primary documentation.",
            "status": "active",
            "refs": [],
            "edges": [],
            "commands": [],
            "include_globs": ["README*", "ARCHITECTURE*", "docs/**"],
            "exclude_globs": [],
        },
        "tests": {
            "label": "Tests",
            "aliases": ["testing", "pytest"],
            "intent": "Test fixtures, regression coverage, and verification commands.",
            "status": "active",
            "refs": [],
            "edges": [],
            "commands": ["pytest"],
            "include_globs": ["tests/**"],
            "exclude_globs": [],
        },
    }
    return seeds


def load_graph(repo_path: str | Path) -> dict[str, Any]:
    if split_layout_active(repo_path):
        return _load_split_graph(repo_path)
    path = topic_path(repo_path)
    if not path.exists():
        raise TopicGraphError(f"missing topic graph: {path}")
    return read_json(path)


def _load_split_graph(repo_path: str | Path) -> dict[str, Any]:
    """Assemble the graph dict from the split layout: top-level fields from
    the ``_meta.json`` sidecar (defaults synthesized when it's missing),
    topics from the per-id ``<topic_id>.json`` files."""
    meta_path = topic_meta_path(repo_path)
    graph = read_json(meta_path) if meta_path.exists() else {}
    if not isinstance(graph, dict):
        raise TopicGraphError(f"invalid topic meta sidecar (not an object): {meta_path}")
    graph.setdefault("version", SCHEMA_VERSION)
    graph.setdefault("repo", repo_name(repo_path))
    graph.setdefault("updated_at", utc_now())
    graph["topics"] = {
        f.stem: read_json(f)
        for f in sorted(topic_split_dir(repo_path).glob("*.json"))
        if f.name != TOPIC_META_FILE
    }
    return graph


def save_graph(repo_path: str | Path, graph: dict[str, Any]) -> None:
    graph["updated_at"] = utc_now()
    write_graph_to_disk(repo_path, graph)


def load_local_graph(repo_path: str | Path) -> dict[str, Any]:
    """Read the machine-local overlay (``topic.local.json``).

    The overlay is gitignored and holds topics produced by proposal
    approval / scan rather than hand-curated into ``topic.json``. A
    missing overlay is normal and returns an empty shell — never raises.
    """
    path = topic_local_path(repo_path)
    if not path.exists():
        return {"topics": {}, "deleted_topics": []}
    data = read_json(path)
    data.setdefault("topics", {})
    data.setdefault("deleted_topics", [])
    return data


def save_local_graph(repo_path: str | Path, overlay: dict[str, Any]) -> None:
    overlay["updated_at"] = utc_now()
    write_json(topic_local_path(repo_path), overlay)


def merge_graphs(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Overlay ``topic.local.json`` onto base ``topic.json``.

    Whole-topic override: an overlay topic entry fully replaces the base
    entry for the same id. ``deleted_topics`` tombstones drop base topics
    the overlay has retired. Top-level fields (``repo``, ``version``,
    ``updated_at``) come from the base.
    """
    merged = dict(base)
    topics = dict(base.get("topics") or {})
    topics.update(overlay.get("topics") or {})
    for tid in overlay.get("deleted_topics") or []:
        topics.pop(tid, None)
    merged["topics"] = topics
    return merged


def load_graph_merged(repo_path: str | Path) -> dict[str, Any]:
    """Effective graph: base ``topic.json`` merged with the local overlay."""
    return merge_graphs(load_graph(repo_path), load_local_graph(repo_path))


def read_json(path: str | Path) -> dict[str, Any]:
    try:
        return json.loads(Path(path).read_text())
    except json.JSONDecodeError as exc:
        raise TopicGraphError(f"invalid JSON in {path}: {exc}") from exc


def write_json(path: str | Path, data: dict[str, Any]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(_dumps(data))


def _dumps(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


def _atomic_write(path: Path, data: str) -> None:
    """Write-tmp + fsync + rename. POSIX-atomic on the target path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        # Best-effort cleanup of orphaned tmp file.
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_graph_to_disk(repo_path: str | Path, graph: dict[str, Any]) -> Path:
    """The one approved-graph serializer. Format follows the disk: a repo
    already on the split layout gets per-topic files, anything else keeps
    the legacy single ``topic.json``. Returns the path written (the split
    dir or the legacy file)."""
    if split_layout_active(repo_path):
        return write_split_graph(repo_path, graph)
    target = topic_path(repo_path)
    _atomic_write(target, _dumps(graph))
    return target


def _split_topic_filename(tid: Any) -> str:
    """Upstream id validation rejects these, but the writer must never
    clobber the sidecar or escape the dir on an unvalidated caller."""
    if f"{tid}.json" == TOPIC_META_FILE or "/" in str(tid) or str(tid).startswith("."):
        raise TopicGraphError(f"invalid topic id for split layout: {tid!r}")
    return f"{tid}.json"


def write_split_graph(repo_path: str | Path, graph: dict[str, Any]) -> Path:
    """Write ``graph`` in the split layout: ``_meta.json`` carries every
    top-level field except ``topics``; each topic gets ``<topic_id>.json``;
    files for topics no longer in the graph are deleted."""
    split_dir = topic_split_dir(repo_path)
    split_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write(topic_meta_path(repo_path),
                  _dumps({k: v for k, v in graph.items() if k != "topics"}))
    topics = graph.get("topics") or {}
    for tid, body in topics.items():
        _atomic_write(split_dir / _split_topic_filename(tid), _dumps(body))
    for stale in split_dir.glob("*.json"):
        if stale.name != TOPIC_META_FILE and stale.stem not in topics:
            stale.unlink()
    return split_dir


def match_glob(path: str, pattern: str) -> bool:
    return fnmatch.fnmatch(path, pattern)


def is_generated_path(path: str) -> bool:
    lower = path.lower()
    return any(lower.endswith(suffix) for suffix in IGNORED_SUFFIXES)


def normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value).lower()).strip()


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-")
    return slug or "topic"


def _valid_id(value: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9][a-z0-9._-]*", value))
