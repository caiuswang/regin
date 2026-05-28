"""Shared CLI command decorators.

Extracted per the framework-led refactor plan's Phase D.1 so every
command that needs a ready database expresses the requirement once.
The pre-refactor version duplicated the same three-line
`db_exists()` guard in five handler bodies.
"""

from __future__ import annotations

import functools
import sys

from lib.orm.engine import db_exists


def require_db(fn):
    """Reject the invocation if the SQLite database has not been
    initialised. Prints the same error message the open-coded guard
    used and exits 1.

    Usage:
        @require_db
        def cmd_status(repo: str | None = None): ...

    Works with both argparse-style handlers (first positional arg is
    an ``argparse.Namespace``) and Typer-style handlers (keyword args).
    The decorator inspects nothing about the call signature — it only
    runs the db presence check, then forwards every argument untouched.
    """
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        if not db_exists():
            print("Database not initialized. Run 'init' first.")
            sys.exit(1)
        return fn(*a, **kw)
    return wrapper


__all__ = ["require_db"]
