"""Language definition — the per-language config regin consumes.

A `Language` describes the minimum a source language needs to be useful
to regin: the file extensions that identify it, a structural metadata
parser, and an open-ended `framework_hooks` mapping for niche extensions.

Regin ships Java out of the box; new languages plug in by adding a
module under `lib.languages.<id>` that constructs a `Language` instance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Mapping, Tuple


@dataclass(frozen=True)
class Language:
    """Pluggable definition of one source language."""

    id: str
    file_extensions: Tuple[str, ...]

    # Parse one source file's content into structural metadata:
    # {class_name, package, extends, implements}.
    parse_class_metadata: Callable[[str], dict] | None = None

    # Optional language-specific callables looked up by name. Empty by
    # default; languages register their own keys.
    framework_hooks: Mapping[str, Callable] = field(default_factory=dict)
