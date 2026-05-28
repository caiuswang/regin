"""Resolve which registered repo a file path belongs to.

Used by the rule-check hook to (a) record the canonical `Repo.name`
on each `rule_triggers` row (replacing the previous
`os.path.basename(effective_root)` heuristic) and (b) let the
`/api/rules?repo=<name>` filter validate the repo name.

The matching rule is longest-prefix: if a file lives at
`/a/b/c/file.java` and both `/a/b` and `/a/b/c` are registered, the
inner repo wins.
"""

from __future__ import annotations

import os
from typing import List, Optional, Tuple

from sqlmodel import select

from lib.orm import SessionLocal
from lib.orm.models import Repo


def _normalize(path: str) -> str:
    return os.path.realpath(os.path.expanduser(path)).rstrip(os.sep)


def normalize_repos(repos) -> List[Tuple[Repo, str]]:
    """Pre-normalize a repo list for repeated prefix matching.

    Returns `(repo, normalized_path)` pairs sorted longest-path-first so
    the first match in `repo_for_path_norm` is the longest prefix. Build
    this once per batch (e.g. per ingest run) and reuse it across many
    `repo_for_path_norm` calls instead of re-normalizing per call.
    """
    out: List[Tuple[Repo, str]] = []
    for repo in repos:
        if repo.path:
            out.append((repo, _normalize(repo.path)))
    out.sort(key=lambda pair: len(pair[1]), reverse=True)
    return out


def repo_for_path_norm(file_path: str, norm_repos: List[Tuple[Repo, str]]) -> Optional[Repo]:
    """Longest-prefix match against a pre-normalized repo list.

    Hot-path variant of `repo_for_path` — the caller passes the output of
    `normalize_repos()` so no DB query or repo-path normalization happens
    per call. `norm_repos` is sorted longest-first, so the first hit wins.
    """
    file_real = _normalize(file_path)
    for repo, repo_real in norm_repos:
        if file_real == repo_real or file_real.startswith(repo_real + os.sep):
            return repo
    return None


def repo_for_path(file_path: str) -> Optional[Repo]:
    """Return the registered Repo whose path is the longest prefix of
    `file_path`, or None if no registered repo covers it.

    A repo at `/a/b` covers `/a/b`, `/a/b/file`, and `/a/b/sub/file`,
    but not `/a/bb/file`.
    """
    with SessionLocal() as session:
        repos = session.exec(select(Repo)).all()
    return repo_for_path_norm(file_path, normalize_repos(repos))
