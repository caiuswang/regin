"""CLI output helpers.

Extracted per the framework-led refactor plan's Phase D.1 so command
handlers can be tested against a captured writer instead of real
stdout. Each helper emits to a module-level sink (`_stdout`, `_stderr`)
that tests can monkey-patch to a `io.StringIO`.

Keep these helpers intentionally thin — CLI output is UX, not
diagnostics. Use `lib.logging_setup.get_logger()` for anything that
should flow through structured logging.
"""

from __future__ import annotations

import sys
from typing import Iterable, Sequence


_stdout = sys.stdout
_stderr = sys.stderr


def echo(*parts: object, sep: str = " ", end: str = "\n") -> None:
    """Write `parts` to the CLI stdout sink.

    Equivalent to `print` but routed through the `_stdout` module
    attribute so tests can redirect output via `monkeypatch`.
    """
    print(*parts, sep=sep, end=end, file=_stdout)


def error(*parts: object, sep: str = " ", end: str = "\n") -> None:
    """Write `parts` to the CLI stderr sink."""
    print(*parts, sep=sep, end=end, file=_stderr)


def table(rows: Iterable[Sequence[object]],
          headers: Sequence[str] | None = None,
          *, min_col_width: int = 2) -> None:
    """Render a simple whitespace-aligned table.

    `rows` is an iterable of row tuples; `headers` is an optional
    column-title row printed above the data. Column widths are the max
    of each column's stringified cell widths (floor'd at `min_col_width`).

    Not a full ASCII box — just padded columns for readability, no
    heavyweight dependency. For anything fancier, use Rich directly.
    """
    data = [[str(c) for c in row] for row in rows]
    if not data and not headers:
        return
    header_row = list(headers) if headers else None
    all_rows = ([header_row] if header_row else []) + data

    col_count = max(len(r) for r in all_rows)
    widths = [min_col_width] * col_count
    for r in all_rows:
        for i, cell in enumerate(r):
            if len(cell) > widths[i]:
                widths[i] = len(cell)

    def _fmt(row):
        return "  ".join(str(c).ljust(widths[i]) for i, c in enumerate(row))

    if header_row:
        echo(_fmt(header_row))
        echo(_fmt(["-" * w for w in widths]))
    for r in data:
        echo(_fmt(r))


__all__ = ["echo", "error", "table"]
