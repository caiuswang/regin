"""One implementation per concern, across all three Pipeline tabs (regin:73).

R5 is the rule that the three tabs are one page rather than three that share a
header, and its cheapest violation is the one nobody notices: a column that
means the same thing on every tab, rendered by three different expressions.
The day a date needs a timezone, or a relative form, or an em dash for the
empty case, two of the three get it and the third quietly keeps saying
something else.

A date is not observable as a *rule* from a request — every tab would return
the same ISO string either way — so the assertion is read off the source, the
way `tests/defects/test_web_surface.py` reads the client's own constants. It
names the import and the absence of a hand-rolled substitute, never the
formatter's output, so changing what a short date looks like stays a one-line
change in one file.
"""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]

#: The row component each tab renders its list through — the one place a date
#: reaches the screen on that tab.
ROWS = {
    "tasks": ROOT / "frontend/src/components/tasks/TaskRow.vue",
    "features": ROOT / "frontend/src/components/pipeline/FeatureTrackTable.vue",
    "defects": ROOT / "frontend/src/components/defects/DefectRow.vue",
}

#: `iso.slice(0, 10)` and its spelling variants — `shortDate`'s body, inlined.
HAND_ROLLED = re.compile(r"\.slice\(\s*0\s*,\s*10\s*\)")

#: A date through `ledgerFormat::shortDate`, or an age through
#: `pipelineFormat::ago` — the Features tab's Active column is an age (#110).
SHARED_IMPORT = re.compile(
    r"import\s*\{[^}]*\b(shortDate|ago)\b[^}]*\}\s*from\s*['\"][^'\"]*(ledgerFormat|pipelineFormat)")


def test_every_tab_uses_the_shared_date_formatter():
    """All three tabs date a row through `utils/ledgerFormat::shortDate`.

    Features and Defects rendered the same column two different ways before
    this feature; a third spelling on Tasks is the same defect with a different
    address, so the test covers the tab that still has to move as well as the
    two that already did.
    """
    for tab, path in ROWS.items():
        source = path.read_text()
        assert SHARED_IMPORT.search(source), (
            f"the {tab} row must date its rows through the shared "
            f"`shortDate`, not a formatter of its own ({path.name})")
        assert not HAND_ROLLED.search(source), (
            f"the {tab} row inlines shortDate's body ({path.name}); a second "
            "copy is what lets one tab drift from the other two")
