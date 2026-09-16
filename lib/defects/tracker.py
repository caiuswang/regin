"""Rounds-to-done, read from the issue tracker (CAI-214).

The counting lives in `lib.defects.rounds`, which is pure. This module is the
thin part that asks the tracker for the history to count, and — importantly —
refuses to answer at all when no tracker is reachable.

That refusal is the point. A metric that silently returns a plausible number
when it has no data is worse than one that fails: the caller cannot tell the
floor from the truth. `1` is what a real never-reopened feature reads, so
`1` must never double as "we could not look".
"""

from __future__ import annotations

from lib.activity_log import get_activity_logger
from lib.tasks.integrations import IntegrationError

log = get_activity_logger("defects")

# The registry is imported inside each function below, not at module scope: a
# provider module imports `lib.defects.rounds`, which runs this package's
# `__init__`, which imports this module — so a module-scope import of the
# registry closes a cycle, and whichever of the two is imported first loses.


class TrackerUnavailable(RuntimeError):
    """No configured tracker could answer, so there is no count to report.

    Two different facts wear this one exception, and the *message* is what
    tells them apart: the tracker was unreachable, or it holds no such issue.
    On a local provider only the second is possible — a read against regin's
    own database cannot be unreachable — so a caller that renders this must
    render the message rather than a fixed "did not answer" sentence.
    """


def resolve(feature_key: str):
    """The provider and reference for a feature key, or `TrackerUnavailable`."""
    from lib.tasks.integrations.registry import resolve_ref  # noqa: PLC0415

    try:
        return resolve_ref(feature_key)
    except IntegrationError as exc:
        raise TrackerUnavailable(str(exc)) from exc


def name_ref(text: str):
    """The reference `text` names, with no tracker call and no credential.

    For recording a pointer rather than reading one — see
    `registry.name_ref`. Still refuses an unrecognized or ambiguous shape.
    """
    from lib.tasks.integrations.registry import name_ref as _name_ref  # noqa: PLC0415

    try:
        return _name_ref(text)
    except IntegrationError as exc:
        raise TrackerUnavailable(str(exc)) from exc


def rounds_for(feature_key: str) -> int:
    """Rounds-to-done for one feature, from the tracker's state history."""
    provider, ref = resolve(feature_key)
    try:
        issue = provider.fetch_issue(ref)
    except IntegrationError as exc:
        raise TrackerUnavailable(str(exc)) from exc
    log.read("tracker_rounds", feature_key=feature_key, rounds=issue.rounds)
    return issue.rounds
