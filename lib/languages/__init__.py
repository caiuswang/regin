"""Language registry — maps language id → Language definition.

The Java entry is registered by default. Additional languages can be
appended by callers (typically in a settings-driven init in Phase 5).
"""

from __future__ import annotations

from typing import Iterable

from lib.languages.base import Language
from lib.languages.java import JAVA
from lib.languages.python import PYTHON

_REGISTRY: dict[str, Language] = {JAVA.id: JAVA, PYTHON.id: PYTHON}


def get(lang_id: str) -> Language:
    """Return the registered Language, raising KeyError if unknown."""
    return _REGISTRY[lang_id]


def all_ids() -> list[str]:
    return list(_REGISTRY.keys())


def find_by_extension(path: str) -> Language | None:
    """Return the first registered Language whose file extensions match `path`."""
    for lang in _REGISTRY.values():
        if any(path.endswith(ext) for ext in lang.file_extensions):
            return lang
    return None


def register(lang: Language) -> None:
    """Register (or replace) a language definition."""
    _REGISTRY[lang.id] = lang


def registered() -> Iterable[Language]:
    return _REGISTRY.values()


__all__ = ['Language', 'get', 'all_ids', 'find_by_extension', 'register', 'registered']
