"""Where a checkout's virtualenv keeps its executables.

Stdlib-only on purpose: `cli/regin.py` imports it from the system interpreter,
before it has re-executed under the venv that holds regin's dependencies.
"""

from __future__ import annotations

import os

WINDOWS = os.name == "nt"


def venv_bin(root) -> str:
    return os.path.join(str(root), ".venv", "Scripts" if WINDOWS else "bin")


def venv_python(root) -> str:
    return os.path.join(venv_bin(root), "python.exe" if WINDOWS else "python")


def venv_script(root, name: str) -> str:
    """A console-script entry point pip installed into the venv."""
    return os.path.join(venv_bin(root), name + (".exe" if WINDOWS else ""))
