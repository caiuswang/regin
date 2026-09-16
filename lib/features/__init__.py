"""A feature: its own row, its lifecycle log, and what it cost (regin:82).

  * `store.py`       — the `features` table. **The population is this table**,
                       not a union over five others
  * `events.py`      — the append-only lifecycle. `stage`, `rounds`,
                       `claimed_at` and the escape verdict are all queries
                       over it, so no counter can drift from the fact
  * `attribution.py` — which sessions built it: declared rows, plus the
                       two-join lane `run → task → feature`
  * `cost.py`        — what they cost, and what could not be measured
  * `ledger.py`      — the one report `regin feature ledger` prints
  * `index.py`       — the whole population in one statement
  * `sync.py`        — pulling a remote tracker's state, on an operator's
                       ask and on nothing else

The two statistics are the whole point; nothing else may shape the schema.
Every figure states its own gaps, and a gap is always a count of sessions —
never a substituted zero.
"""

from lib.features import (  # noqa: F401
    attribution, cost, events, index, ledger, store, sync,
)

__all__ = ["attribution", "cost", "events", "index", "ledger", "store", "sync"]
