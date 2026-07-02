"""Surface definitions — importing this package registers every prompt surface.

Each submodule owns one area's surfaces and calls ``register_surface`` at import
time. ``registry._ensure_loaded`` imports this package on first access, so the
registrations are lazy (no import cycle at ``lib.prompts`` import time).
"""

from __future__ import annotations

from lib.prompts.surfaces import (  # noqa: F401  (import side effect: registration)
    drafting,
    grader,
    memory,
    review,
    triage,
)

__all__ = ["drafting", "grader", "memory", "review", "triage"]
