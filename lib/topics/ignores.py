"""Ignore-rule loading for repo topic scanning.

This module intentionally does not integrate with ``lib.topics`` yet. It
provides a small matcher that future scanners can call before considering a
repository path as topic evidence.
"""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_IGNORE_PATTERNS: tuple[str, ...] = (
    ".git/",
    ".hg/",
    ".svn/",
    ".tox/",
    ".venv/",
    "venv/",
    "node_modules/",
    "dist/",
    "build/",
    "target/",
    ".next/",
    ".nuxt/",
    ".cache/",
    "__pycache__/",
    ".pytest_cache/",
    ".mypy_cache/",
    "coverage/",
    "htmlcov/",
    "*.pyc",
    "*.pyo",
    "*.class",
    "*.o",
    "*.so",
    "*.dylib",
    "*.dll",
    "*.exe",
    "*.min.js",
    "*.map",
    "*.sqlite",
    "*.db",
)

IGNORE_FILES: tuple[str, ...] = (".gitignore", ".reginignore")


@dataclass(frozen=True)
class IgnoreRule:
    pattern: str
    negated: bool = False
    directory_only: bool = False
    rooted: bool = False
    has_slash: bool = False
    source: str = "<default>"
    line_number: int = 0


class IgnoreMatcher:
    """Ordered gitignore-style matcher with negation support."""

    def __init__(self, rules: list[IgnoreRule] | tuple[IgnoreRule, ...], repo_path: str | Path | None = None):
        self.rules = list(rules)
        self.repo_path = Path(repo_path).resolve() if repo_path is not None else None

    def is_ignored(self, path: str | Path) -> bool:
        normalized = _normalize_path(path, self.repo_path)
        if not normalized:
            return False

        ignored = False
        for rule in self.rules:
            if _matches_rule(normalized, rule):
                ignored = not rule.negated
        return ignored


def load_ignore_rules(repo_path: str | Path) -> IgnoreMatcher:
    """Load default ignores, then repo ``.gitignore`` and ``.reginignore``.

    Later rules win, so repo-local negation patterns can re-include defaults
    for callers that still pass those paths to the matcher.
    """

    repo = Path(repo_path)
    rules = _parse_rules(DEFAULT_IGNORE_PATTERNS, source="<default>")
    for filename in IGNORE_FILES:
        ignore_path = repo / filename
        if ignore_path.exists():
            rules.extend(_parse_rules(ignore_path.read_text().splitlines(), source=filename))
    return IgnoreMatcher(rules, repo_path=repo)


def is_ignored(path: str | Path, rules: IgnoreMatcher | list[IgnoreRule] | tuple[IgnoreRule, ...]) -> bool:
    """Return whether ``path`` is ignored by a matcher or rule list."""

    matcher = rules if isinstance(rules, IgnoreMatcher) else IgnoreMatcher(rules)
    return matcher.is_ignored(path)


def _parse_rules(lines: tuple[str, ...] | list[str], *, source: str) -> list[IgnoreRule]:
    rules: list[IgnoreRule] = []
    for index, raw in enumerate(lines, start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        negated = line.startswith("!")
        if negated:
            line = line[1:].strip()
            if not line:
                continue

        rooted = line.startswith("/")
        if rooted:
            line = line.lstrip("/")

        directory_only = line.endswith("/")
        if directory_only:
            line = line.rstrip("/")

        line = line.replace("\\", "/")
        if not line:
            continue

        rules.append(IgnoreRule(
            pattern=line,
            negated=negated,
            directory_only=directory_only,
            rooted=rooted,
            has_slash="/" in line,
            source=source,
            line_number=index,
        ))
    return rules


def _matches_rule(path: str, rule: IgnoreRule) -> bool:
    if rule.directory_only:
        return _matches_directory_rule(path, rule)

    candidates = _candidates_for_rule(path, rule)
    return any(fnmatch.fnmatchcase(candidate, rule.pattern) for candidate in candidates)


def _matches_directory_rule(path: str, rule: IgnoreRule) -> bool:
    candidates = _candidates_for_rule(path, rule)
    for candidate in candidates:
        if candidate == rule.pattern or candidate.startswith(f"{rule.pattern}/"):
            return True
        if fnmatch.fnmatchcase(candidate, rule.pattern):
            return True
        if fnmatch.fnmatchcase(candidate, f"{rule.pattern}/*"):
            return True
    return False


def _candidates_for_rule(path: str, rule: IgnoreRule) -> list[str]:
    if rule.rooted:
        return [path]
    if rule.has_slash:
        parts = path.split("/")
        return ["/".join(parts[index:]) for index in range(len(parts))]
    return [path, *path.split("/")]


def _normalize_path(path: str | Path, repo_path: Path | None = None) -> str:
    candidate = Path(path)
    if repo_path is not None and candidate.is_absolute():
        try:
            candidate = candidate.resolve().relative_to(repo_path)
        except ValueError:
            pass

    normalized = os.fspath(candidate).replace("\\", "/")
    normalized = normalized.lstrip("/")
    parts = [part for part in normalized.split("/") if part not in {"", "."}]
    return "/".join(parts)
