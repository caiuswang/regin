"""The viewer's error vocabulary and the HTTP status each one maps to."""

from __future__ import annotations

STATUS = {
    "bad_source": 400,
    "forbidden": 403,
    "outside_root": 403,
    "missing": 404,
    "unreadable": 422,
}


class FileAccessError(Exception):
    def __init__(self, error: str):
        super().__init__(error)
        self.error = error

    @property
    def status(self) -> int:
        return STATUS[self.error]
