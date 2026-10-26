"""Small spec bodies for the spec-reader and slicing-gate tests."""

from __future__ import annotations

HEADER = ("| # | Slice | Delivers | Spec refs | Tests | Depends on | Base |\n"
          "|---|---|---|---|---|---|---|\n")


def gherkin(*names: str) -> str:
    """An Acceptance section with one titled scenario per name."""
    blocks = [f"Scenario: {name}\n  Given x\n  Then y\n" for name in names]
    return "## Acceptance\n\n```gherkin\n" + "\n".join(blocks) + "```\n"


def row(n: str, tests: str, deps: str = "—", base: str = "f1/integration") -> str:
    return f"| {n} | slice {n} | delivers {n} | R1 | {tests} | {deps} | {base} |\n"


def spec(acceptance: str, *rows: str) -> str:
    return acceptance + "\n## Slices\n\n" + HEADER + "".join(rows)
