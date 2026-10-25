## Question

Nothing to decide — this is the map's destination, written down. When every decision ticket is closed, write the spec as **its own issue** (not a section of this map), because a feature *is* a Linear issue and its key is the `feature_key` the ledger will be read under. [CAI-242](<https://linear.app/caiuswang/issue/CAI-242>) is the shape to follow.

It carries:

* thin prose, no file paths — the implementation is not specified before the design exists;
* the vocabulary this map settled, and a **What it refuses to know** section holding the refusals, which are the actual design;
* the CLI/API/route surface — new endpoints, new routes, new nav entry — and any schema change as **two edits plus the model** (an `alembic/versions/00NN_*.py` revision *and* `db/schema.sql`), or fresh and migrated installs diverge;
* **acceptance items as names of tests that do not yet exist**, grouped by the ticket that decided each, including the E2E items — this surface is the first half of the ledger that can be wrong *visually*, so Playwright specs are acceptance items, not extras;
* the test fixtures the tracker-facing half needs, with `test_tracker_rounds.py:84-91` as the precedent for an unreachable tracker;
* corrections found while charting, and the known limits — starting with coverage at 0.4% and the 794 sessions carrying no usage rows.

Then hand it to `/feature-pipeline`, which owns the build.