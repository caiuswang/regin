#!/usr/bin/env python3
"""regin: A workbench for building software with AI coding agents.

Thin entrypoint. The real command definitions live under
`cli/commands/` and the Typer app is assembled in `cli/app.py`.
"""

import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# When invoked as `python cli/regin.py`, sys.path[0] is `cli/`, not the project
# root — so neither `lib` nor `cli.app` resolves unless the project was
# installed via `pip install -e .`. Put the root on the path either way.
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from lib.venv_paths import WINDOWS, venv_python  # noqa: E402

_VENV_PYTHON = venv_python(_PROJECT_ROOT)

# Re-exec under the local venv if it exists and we're not already using it.
# This lets the CLI work when invoked directly after setup, even though the
# shebang is portable.
#
# The sentinel bounds it to one attempt: in a worktree whose `.venv/bin` is
# hardlinked to the main checkout's, the interpreter reports the *main* path as
# sys.executable, so the equality below never holds and an unguarded execv spins
# forever — hanging any git operation that runs a regin hook.
_REEXEC_SENTINEL = '_REGIN_REEXEC'
_wrong_interpreter = os.path.normcase(sys.executable) != os.path.normcase(_VENV_PYTHON)
# Windows defaults open() and stdio to the ANSI code page (cp936 on a Chinese
# install), which cannot decode the UTF-8 regin and the agents write.
_needs_utf8 = WINDOWS and not sys.flags.utf8_mode
if ((_wrong_interpreter or _needs_utf8) and os.path.exists(_VENV_PYTHON)
        and not os.environ.get(_REEXEC_SENTINEL)):
    os.environ[_REEXEC_SENTINEL] = '1'
    if WINDOWS:
        # Windows has no exec: os.execv spawns a child and returns to the
        # shell at once, so the prompt comes back mid-command and the exit
        # code is lost. Wait on the child instead. The script path, not
        # argv[0], because argv[0] may be the pip launcher `regin.exe`.
        import subprocess
        sys.exit(subprocess.call([_VENV_PYTHON, '-X', 'utf8', os.path.abspath(__file__)]
                                 + sys.argv[1:]))
    os.execv(_VENV_PYTHON, [_VENV_PYTHON] + sys.argv)

from cli.app import app  # noqa: E402


def main() -> None:
    app()


if __name__ == '__main__':
    main()
