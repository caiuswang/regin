"""F158 S1 — the P0 probe results live in the specs, not in code.

The specs are rows of the install DB (`features.body`), so this reads that DB
read-only and skips where a checkout has none.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

from lib.settings import settings


def _spec(feature_id: int) -> str:
    db = Path(settings.main_worktree) / "db" / "regin.db"
    if not db.is_file():
        pytest.skip(f"no install DB at {db}")
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        row = conn.execute("SELECT body FROM features WHERE id = ?", (feature_id,)).fetchone()
    finally:
        conn.close()
    if not row or not row[0]:
        pytest.skip(f"feature {feature_id} has no spec in {db}")
    return row[0]


def _probe_rows(spec: str) -> dict[str, list[str]]:
    rows = {}
    for line in spec.splitlines():
        found = re.match(r"^\|\s*(Q[1-5])\s*\|", line)
        if found:
            rows[found.group(1)] = [c.strip() for c in line.strip().strip("|").split("|")]
    return rows


def test_probe_findings_are_written_back():
    spec = _spec(158)
    assert "2.1.295" in spec, "P0 section does not name the CC version probed"
    rows = _probe_rows(spec)
    assert sorted(rows) == ["Q1", "Q2", "Q3", "Q4", "Q5"]
    # columns: # | question | conclusion | evidence | impact
    unfinished = [q for q, cells in rows.items()
                  if len(cells) < 5 or not cells[2] or not cells[3]]
    assert not unfinished, f"P0 rows without a conclusion or evidence: {unfinished}"

    f134 = _spec(134)
    assert "F158 P0 Q2" in f134, "F134 mapping row 3 does not carry the Q2 verdict"
