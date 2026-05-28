#!/usr/bin/env python3
"""regin: Pattern reference system for AI Agents.

Thin entrypoint. The real command definitions live under
`cli/commands/` and the Typer app is assembled in `cli/app.py`.
"""

import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_VENV_PYTHON = os.path.join(_PROJECT_ROOT, '.venv', 'bin', 'python')

# Re-exec under the local venv if it exists and we're not already using it.
# This lets the CLI work when invoked directly after setup, even though the
# shebang is portable.
if sys.executable != _VENV_PYTHON and os.path.exists(_VENV_PYTHON):
    os.execv(_VENV_PYTHON, [_VENV_PYTHON] + sys.argv)

# When invoked as `python cli/regin.py`, sys.path[0] is `cli/`, not the project
# root — so `cli.app` can't be resolved unless the project was installed via
# `pip install -e .`. Put the project root on the path so this works either way.
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from cli.app import app


def main() -> None:
    app()


if __name__ == '__main__':
    main()
