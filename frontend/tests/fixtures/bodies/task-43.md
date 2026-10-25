## Question

Every existing read is keyed by a feature key the caller **already knows**. A browser user does not know one, so a window over the ledger implies a list — and **no distinct-feature-key query exists anywhere** in `lib/`, `cli/` or `web/`.

* **Is there an index at all**, or is a feature page only ever reached from the task link that names it? An index is a new read with no CLI counterpart; deciding it exists means deciding regin can enumerate its own features, which nothing has needed until now.
* If it exists, **what is a feature regin knows about?** The union spans three tables — `session_features`, `defects.feature_key`, `agent_task_links.external_key` — and they disagree: a feature with a linked task but no declaration, or with only a defect blamed on it, is real but has never been attributed. `cost.coverage()` is the existing precedent for that union.
* Where do **unattributed defects** — `feature_key IS NULL`, a first-class state — appear in a list organised by feature? A bucket row, a separate surface, or nowhere?
* `ledger.build()` **cannot be looped**: it does a network stage fetch plus per-trace SQL per key. So what does a row show that is cheap — and does a row without a stage (the expensive field) still tell you anything worth listing?
* What **orders** the list, given the answer decides what the surface is *for*: most recent, most expensive, most defects, or unresolved-first?

Decide whether the index exists, what populates it, and what one row is allowed to cost. Pagination and filtering stay in the map's fog until the row is known.