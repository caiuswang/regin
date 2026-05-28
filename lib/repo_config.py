"""Repo-local config overlay (`<repo>/.regin/config.json`).

A registered repo may carry a `.regin/config.json` that overlays global
settings for edits inside that repo. v1 scope is `language_extensions`
only — a map of language id → file extensions, merged over the global
`settings.language_extensions` (repo keys win). This lets one repo route a
rule engine to a language without touching global config or code, and the
config travels with the repo (version-controlled, team-shared).

Read on the PostToolUse hot path, so the parsed result is cached per file
mtime: a repo with no `.regin/config.json` costs one cheap `stat`; an
unchanged file is never re-parsed; a newly added or edited file is picked
up on its next mtime change without a process restart.

Deliberately narrow: it does NOT overlay `rule_engines`. A repo-local file
that could introduce a bundle engine (which executes arbitrary runner
scripts) or repoint `grit_dir` would be a code-execution surface for any
repo you didn't author. Extending to engine overrides needs an explicit
safety boundary and a real use case first.
"""

from __future__ import annotations

import json
import os

from pydantic import BaseModel, Field, ValidationError

from lib.activity_log import get_activity_logger
from lib import settings as _settings_mod

log = get_activity_logger("rules")

_CONFIG_RELPATH = os.path.join(".regin", "config.json")


class RepoConfig(BaseModel):
    """The repo-local overlay. Unknown keys are ignored for forward-compat."""

    model_config = {"extra": "ignore"}

    language_extensions: dict[str, list[str]] = Field(default_factory=dict)


_EMPTY = RepoConfig()
# Keyed by absolute config path → (mtime, parsed RepoConfig).
_cache: dict[str, tuple[float, RepoConfig]] = {}


def reset_cache() -> None:
    _cache.clear()


def load_repo_config(repo_root: str) -> RepoConfig:
    """Load `<repo_root>/.regin/config.json`, or `_EMPTY` if absent/invalid.

    Never raises: a malformed file logs an error and is treated as empty so
    a bad repo config can't break the edit hook.
    """
    path = os.path.join(repo_root, _CONFIG_RELPATH)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return _EMPTY
    cached = _cache.get(path)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    try:
        with open(path) as f:
            cfg = RepoConfig.model_validate(json.load(f))
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        log.error("repo_config_invalid", path=path, error=str(exc))
        cfg = _EMPTY
    _cache[path] = (mtime, cfg)
    return cfg


def effective_language_extensions(repo_root: str | None) -> dict[str, list[str]]:
    """Global `language_extensions` merged with the repo's (repo wins per key)."""
    base = dict(_settings_mod.settings.language_extensions)
    if repo_root is not None:
        base.update(load_repo_config(repo_root).language_extensions)
    return base
