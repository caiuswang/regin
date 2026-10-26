"""What is wrong with a spec, one entry per fault, with the line it sits on."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional

PROBLEM_CODES = (
    "no_acceptance", "untitled_block", "gherkin_syntax", "no_test_name",
    "duplicate_name", "table_shape", "uncovered", "double_owned",
    "not_an_acceptance_name",
)


@dataclass(frozen=True)
class Problem:
    code: str
    #: 1-based line in the spec, or None when the fault has no single place.
    line: Optional[int]
    #: The scenario title, slice id, or Tests item the problem is about.
    subject: str
    #: One sentence, shown as-is to an agent and in the UI.
    message: str

    def to_dict(self) -> dict:
        return asdict(self)

    def render(self) -> str:
        where = f"L{self.line} " if self.line is not None else ""
        return f"{where}{self.code} {self.subject}: {self.message}"
