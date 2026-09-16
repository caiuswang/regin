"""The declarations a session leaves in its own trace.

`session_spans` is append-only, so withdrawing an attribution cannot erase the
span that recorded it — it is countered by a second span rather than rewritten.
Both are emitted here rather than at each call site, because declaring and
un-declaring exist on two surfaces now and a surface that forgot to leave one
would make the trace disagree with the table.

Neither is left by a *read*. A second span on the ledger would let an idle
report satisfy `regin gate pipeline-ran`, which exists to prove the stage was
consulted.
"""

from __future__ import annotations


def attributed(row) -> None:
    """The declaration, as the trace records it."""
    from lib.hook_plugin import post_span  # noqa: PLC0415
    post_span(trace_id=row.trace_id, name="feature.attributed",
              attributes={"feature_key": row.feature_key,
                          "provenance": row.provenance,
                          "is_primary": bool(row.is_primary),
                          "round": row.round})


def unattributed(feature_key: str, trace_id: str) -> None:
    """The withdrawal.

    It hangs on the *attributed* trace, the same one the declaration did, so
    the pair reads as one session's history rather than as an event in
    whichever session happened to press the button.
    """
    from lib.hook_plugin import post_span  # noqa: PLC0415
    post_span(trace_id=trace_id, name="feature.unattributed",
              attributes={"feature_key": feature_key})
