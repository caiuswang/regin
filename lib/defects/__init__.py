"""Defect records and what escaped (regin:82).

A defect is something a feature got wrong, addressed by the feature's **id** —
an integer primary key, so every reader joins on it rather than on a string
five tables had to agree about.

An **escape** is a defect blamed on a feature that had already shipped. It is
read as `blamed_event.kind == 'shipped'` — a pointer at an immutable row, which
is what lets the reading compare no two clocks and carry no gap counters
(`lib.defects.escapes`). It is reported beside rounds-to-done and never folded
into it.

`Defect.round` counts fix attempts on one defect and is never aggregated to a
feature; the feature's own rounds live in `lib/features/events.py`.

Writes go through `lib.defects.store`; nothing else should touch the table.
"""

from lib.defects.store import DefectError

__all__ = ["DefectError", "escapes", "store"]

from lib.defects import escapes, store  # noqa: E402  (after DefectError)
