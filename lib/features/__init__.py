"""Cost-per-feature and defects-per-feature, from declared attributions.

`attribution` answers *which sessions built this*, `cost` answers *what did
they cost and what could we not measure*, and `ledger` assembles both into the
one report `regin feature ledger` prints. The stage a feature is in stays in
`lib/defects/stage.py`, which `regin feature stage` keeps to itself.

The two statistics above are the whole point; nothing else may shape the
schema. Every figure states its own gaps, and a gap is always a count of
sessions — never a substituted zero.
"""

from lib.features import attribution, cost, ledger  # noqa: F401

__all__ = ["attribution", "cost", "ledger"]
