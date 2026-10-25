## Question

`ledger.build()` calls `stage_for_key`, which is a **live Linear fetch**. Today's web reads deliberately never do that: `GET /api/tasks/<id>/links` serialises `stage_from_snapshot` from the cached payload, and only an explicit `POST …/refresh` goes to the network. A ledger page that calls `build()` per view would be the first read endpoint in regin to block on a third party.

* Does the ledger endpoint fetch **live**, read the **cached snapshot**, or return the local half immediately and let the stage arrive separately?
* If cached: **what refreshes it, and on whose action?** `CLAUDE.md` forbids a TTL, a polling interval or a skew window, so the answer has to be event- or action-driven. Is the existing task-link refresh the same event, or a second one?
* If live: what does the page render while it waits, and what does it render when Linear is down? The CLI already settled the vocabulary — `feature ledger` **exits 0** printing `stage unknown` inline, while `feature stage` **exits 2**, because there the tracker *is* the answer. What is the HTTP analogue: does an unreachable tracker produce 200 with `stage: null`, or a non-200?
* Constraint 4 says `stage: null` is **"no tracker answered"**, distinct from `spec` and never rendered as a stage name. Where does that distinction have to be visible — the payload, the component, or both?
* The same question in the other direction: `store.fix_states()` does **network I/O per fix item**, which is why the CLI hides it behind `--with-fix-state`. Does any web read get to call it?

Decide the read's contract with the tracker. This blocks the prototype, because what the stage line can *say* decides what it can *show*.