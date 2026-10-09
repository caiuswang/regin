"""S8 — feature 154's acceptance: the website builds and its whole e2e suite passes.

The other two legs of the acceptance — `tests/docs` and the parallel pytest
suite — are the run this test sits inside: it is collected by
`pytest -q -n auto --dist=loadfile`, so the outer run reports those legs itself.
Running pytest again from here would only re-run itself.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
WEBSITE = REPO_ROOT / "website"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _run(cmd: list[str], env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=WEBSITE, env=env, capture_output=True,
                          text=True, timeout=300)


def test_full_suite_passes(tmp_path):
    if not (WEBSITE / "node_modules").exists() or shutil.which("npx") is None:
        pytest.skip("website/node_modules not installed — run `cd website && npm ci`")

    # Build into a private dir: test_website_builds_and_routes_resolve may be
    # rewriting website/dist on another xdist worker at the same moment.
    dist = tmp_path / "dist"
    env = {**os.environ, "WEBSITE_E2E_PORT": str(_free_port()), "WEBSITE_E2E_DIST": str(dist)}

    build = _run(["npx", "vite", "build", "--outDir", str(dist), "--emptyOutDir"], env)
    assert build.returncode == 0, build.stdout + build.stderr

    e2e = _run(["npx", "playwright", "test", "--reporter=line",
                f"--output={tmp_path / 'test-results'}"], env)
    assert e2e.returncode == 0, e2e.stdout[-4000:] + e2e.stderr[-2000:]
