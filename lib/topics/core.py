"""Constants, types, paths, IO, and lifecycle for the repo-local topic graph.

Topic data lives inside each repository under ``.regin/topics``. The
approved graph is human-governed; scans refresh refs for existing
topics but never create approved topics.
"""

from __future__ import annotations

import fnmatch
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TOPICS_DIR = ".regin/topics"
TOPIC_FILE = "topic.json"
TOPIC_LOCAL_FILE = "topic.local.json"
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
    graph_path = topic_path(repo)
    if graph_path.exists() and not force:
        raise TopicGraphError(f"{graph_path} already exists")

    graph = empty_graph(repo)
    if seeds:
        graph["topics"] = seed_topics(repo)
    write_json(graph_path, graph)
    return {"topic": graph_path}


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
    path = topic_path(repo_path)
    if not path.exists():
        raise TopicGraphError(f"missing topic graph: {path}")
    return read_json(path)


def save_graph(repo_path: str | Path, graph: dict[str, Any]) -> None:
    graph["updated_at"] = utc_now()
    write_json(topic_path(repo_path), graph)


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
    Path(path).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


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
