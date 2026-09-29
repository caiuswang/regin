"""I8: no code path reads `missing_check` to decide whether a check exists."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def test_no_check_oracle_remains():
    assert importlib.util.find_spec("lib.defects.checks") is None
    offenders = [
        str(path.relative_to(_ROOT))
        for base in ("lib", "web", "cli")
        for path in (_ROOT / base).rglob("*.py")
        if "defects import checks" in path.read_text(encoding="utf-8")
        or "defects.checks" in path.read_text(encoding="utf-8")]
    assert offenders == []
