"""The E2E suite must not be able to reach the developer's real database.

These are contract tests over `frontend/`, not over Python behaviour. They exist
because the failure they guard is silent: the Playwright suite drives a *separate*
`regin serve` process, so no pytest fixture can contain it, and for a long time
that server was the dev stack's — ~70 specs wrote sessions, spans and inbox
blockers into `db/regin.db`, and one spec dismissed the operator's real parked
decisions on every run.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
E2E_ENV = FRONTEND / "e2e-env.js"
PW_CONFIG = FRONTEND / "playwright.config.js"

pytestmark = pytest.mark.skipif(
    not PW_CONFIG.exists(), reason="frontend/ not present in this checkout",
)


def test_trace_db_path_is_redirectable_out_of_process(monkeypatch, tmp_path):
    """The seam the whole design rests on: an env var, not a monkeypatch.

    `lib.orm.engine` reads its path at import, so this asserts against a fresh
    interpreter rather than the already-imported module.
    """
    import subprocess
    import sys

    target = tmp_path / "scratch.db"
    out = subprocess.run(
        [sys.executable, "-c",
         "from lib.orm.engine import DB_PATH; print(DB_PATH)"],
        cwd=str(Path(__file__).resolve().parents[2]),
        env={**os.environ, "REGIN_TRACE_DB_PATH": str(target)},
        capture_output=True, text=True, check=True,
    )
    assert out.stdout.strip() == str(target)


def test_config_dir_is_redirectable_out_of_process(tmp_path):
    """The settings API writes settings.json; unredirected, a spec that saves a
    setting edits the committed config of the checkout under test."""
    import subprocess
    import sys

    out = subprocess.run(
        [sys.executable, "-c",
         "from lib.settings import SETTINGS_PATH, SETTINGS_LOCAL_PATH;"
         "print(SETTINGS_PATH);print(SETTINGS_LOCAL_PATH)"],
        cwd=str(Path(__file__).resolve().parents[2]),
        env={**os.environ, "REGIN_CONFIG_DIR": str(tmp_path)},
        capture_output=True, text=True, check=True,
    )
    shared, local = out.stdout.split()
    assert shared == str(tmp_path / "settings.json")
    assert local == str(tmp_path / "settings.local.json")


def test_e2e_env_redirects_every_store():
    """A store left un-redirected is a store the suite mutates for real."""
    src = E2E_ENV.read_text()
    for key in ("REGIN_TRACE_DB_PATH", "REGIN_AGENT_MEMORY__DB_PATH",
                "REGIN_DATA_DIR", "REGIN_CONFIG_DIR", "REGIN_WEB_PORT"):
        assert key in src, f"{key} missing from the E2E scratch env"


def test_playwright_never_reuses_a_running_server():
    """`reuseExistingServer: true` silently attaches to the dev stack — which
    is how the suite came to own the developer's database."""
    src = PW_CONFIG.read_text()
    # Line-anchored so prose in a comment can't satisfy or break the check.
    settings = re.findall(r"^\s*reuseExistingServer: (\w+),?$", src, re.M)
    assert settings == ["false", "false"], settings


def test_playwright_ports_are_off_the_dev_stack():
    """Sharing 8321/5173 would put the suite back on the dev server the moment
    someone flipped `reuseExistingServer` back."""
    src = E2E_ENV.read_text()
    api = re.search(r"API_PORT = (\d+)", src)
    vite = re.search(r"VITE_PORT = (\d+)", src)
    assert api and vite
    assert api.group(1) != "8321"
    assert vite.group(1) != "5173"


def test_no_spec_hardcodes_the_dev_server_origin():
    """Specs bypassing the vite proxy must go through `helpers/api-base.js`;
    a literal origin reaches past the suite's own server."""
    offenders = [
        p.name for p in sorted((FRONTEND / "tests").rglob("*.js"))
        if p.name != "api-base.js" and "localhost:8321" in p.read_text()
    ]
    assert offenders == []
