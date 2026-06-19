"""In-memory data model for the rubric grader pipeline.

Plain dataclasses, not SQLModel: these objects live only for the duration
of one grading run. The persisted shape is `lib.orm.models.grades.SessionGrade`,
which stores the ledger and per-claim verdicts as a JSON `detail` blob.

Vocabulary (mirrors the survey's rubric schema):

* Claim types — `state` (code does X), `result` (a runnable produced Y),
  `external` (a library/API behaves Z), `diagnostic` (X is caused by Y),
  plus the synthetic `aggregate` claim `c0` grounded by the coverage
  checklist rather than a single span.
* Groundedness verdicts — GROUNDED / UNGROUNDED / CONTRADICTED / STALE.
* Coverage verdicts — COVERED / PARTIAL / MISSING.
* Source-quality verdicts — AUTHORITATIVE / PROXY / UNVERIFIED.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


CLAIM_TYPES: tuple[str, ...] = (
    "state", "result", "external", "diagnostic", "aggregate",
)

GROUNDED = "GROUNDED"
UNGROUNDED = "UNGROUNDED"
CONTRADICTED = "CONTRADICTED"
STALE = "STALE"

COVERED = "COVERED"
PARTIAL = "PARTIAL"
MISSING = "MISSING"

AUTHORITATIVE = "AUTHORITATIVE"
PROXY = "PROXY"
UNVERIFIED = "UNVERIFIED"

APPROPRIATE = "APPROPRIATE"
SUBOPTIMAL = "SUBOPTIMAL"
WASTED = "WASTED"

PROPORTIONATE = "PROPORTIONATE"
ELEVATED = "ELEVATED"
RUNAWAY = "RUNAWAY"


@dataclass
class Claim:
    """One checkable assertion extracted from the session's artifact."""

    id: str
    raw_text: str            # verbatim substring — the provenance-guard anchor
    normalized_text: str     # self-contained restatement the grounder checks
    type: str                # one of CLAIM_TYPES
    referents: dict = field(default_factory=dict)   # file/symbol/command/url
    provenance: dict = field(default_factory=dict)  # surface/span_id/offset
    load_bearing: bool = True
    parent_sentence: str = ""
    extraction_confidence: float = 1.0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ClaimVerdict:
    """Groundedness verdict for one claim, with mandatory evidence."""

    claim_id: str
    verdict: str                       # GROUNDED | UNGROUNDED | CONTRADICTED | STALE
    evidence_span_id: str | None = None
    evidence_ref: str = ""             # `file:line` / command / url backing it
    reason: str = ""                   # one-line why-it-supports / what's missing
    source_kind: str = ""              # read|grep|bash|webfetch|checklist|none

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CoverageItem:
    """One required-item checklist entry, fixed before grading."""

    item: str
    verdict: str = MISSING             # COVERED | PARTIAL | MISSING
    reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SourceVerdict:
    """Source-quality classification for one grounded claim's source."""

    claim_id: str
    source: str                        # human-readable source description
    verdict: str                       # AUTHORITATIVE | PROXY | UNVERIFIED
    reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AxisGrade:
    """The result of grading one axis of one session."""

    axis: str                          # 'correctness' | 'process'
    verdict: str
    tier: str                          # 'screen' | 'deep'
    scoreboard: dict = field(default_factory=dict)
    report: str = ""
    detail: dict = field(default_factory=dict)
    rubric_version: str = ""
    judge: str = "mechanical"

    def to_dict(self) -> dict:
        return asdict(self)


__all__ = [
    "CLAIM_TYPES",
    "GROUNDED", "UNGROUNDED", "CONTRADICTED", "STALE",
    "COVERED", "PARTIAL", "MISSING",
    "AUTHORITATIVE", "PROXY", "UNVERIFIED",
    "APPROPRIATE", "SUBOPTIMAL", "WASTED",
    "PROPORTIONATE", "ELEVATED", "RUNAWAY",
    "Claim", "ClaimVerdict", "CoverageItem", "SourceVerdict", "AxisGrade",
]
