"""S9 — feature 156's acceptance: every Gherkin scenario has a test in the suite.

The suite run this test sits inside (`pytest -q -n auto --dist=loadfile`) is
what proves those tests pass; running pytest again from here would only re-run
itself. What the outer run cannot report is a scenario that never got a test —
a missing test is not a failing one. This pins each scenario of the spec's
acceptance block to a `def test_…` somewhere under `tests/`.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

SCENARIOS = (
    "Rerunning setup keeps the database",
    "Setup refuses python older than 3.11",
    "Base install provides mcp",
    "Migrate refuses an unstamped incomplete database",
    "Migrate stamps an unstamped complete database",
    "Rebuild keeps non-cache tables",
    "Upgraded schema matches fresh schema",
    "Init force spares another checkout's hooks",
    "Kimi install spares another checkout's block",
    "Hook commands survive a path with spaces",
    "Repair points at adopt after a checkout move",
    "Uninstall dry run lists only owned artifacts",
    "Uninstall removes owned artifacts and keeps data",
    "Uninstall keeps shared artifacts while another checkout is wired",
    "Skill deploy refuses an unrecorded directory",
    "Doctor expands home in bundle paths",
    "Jwt secret follows the config dir",
    "Shipped plugins carry no author path",
    "Cli reference is current",
)


def _test_name(scenario: str) -> str:
    return "test_" + re.sub(r"[^a-z0-9]+", "_", scenario.lower()).strip("_")


def test_full_suite_passes():
    defined = set()
    for path in (REPO_ROOT / "tests").rglob("test_*.py"):
        defined.update(re.findall(r"^def (test_\w+)\(", path.read_text(), re.M))
    missing = [s for s in SCENARIOS if _test_name(s) not in defined]
    assert not missing, "acceptance scenarios without a test: " + "; ".join(missing)
