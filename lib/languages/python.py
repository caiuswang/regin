"""Python language definition."""

from __future__ import annotations

from lib.languages.base import Language


PYTHON = Language(
    id='python',
    file_extensions=('.py',),
)
