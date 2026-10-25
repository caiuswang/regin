## Question

`Ledger.to_dict()` is already the wire shape, so this ticket is not about data — it is about whether the **design survives being rendered**. Every refusal in the shipped ledger is carried by a *qualifier*, and a UI drops qualifiers far more easily than a CLI line does:

```
cost (upper bound)  $13.78 across 1 session
coverage  13 of 2935 sessions (0.4%) · $57.24 of $23,251.49 (0.2%)
```

A stat tile reading **$13.78** in 32px type, with "upper bound" as 11px grey underneath, has quietly become a precise number.

Build a **rough, throwaway** page — `/prototype`, against real data from `regin feature ledger CAI-242 --json` — and react to it. It has to answer:

* **The stage line leads, cost second** in the CLI, because the stage frames every number under it. Does that ordering hold in two dimensions, where a stat band competes with it for the top of the page?
* How does **upper bound** stay attached to the dollar figure — a label, a tooltip, a different glyph, a different type treatment?
* Each figure carries **its own gap counter** (`no-usage-captured`, `model-unpriced`, `zero-duration`, `traceless-runs`, `state-unknown`). Beside the figure, folded into one strip, or on demand? How do they read when — as today — they are larger than the figure itself?
* `none declared` **is not 0,** `stage: null` **is not** `spec`**.** What do the empty states actually look like, given most features will be empty for a long time?
* The **session list is evidence**, not decoration — it is what makes the upper bound legible. The CLI identifies a row as `date · trace_id[:8] · title[:44]` so an id carries by eye between surfaces. Does the web row keep that shape, and do rows link into the trace view?
* **Coverage is a permanent fixture** of every ledger read. Where does it sit so it is unmissable but not the headline?
* Reuse `TaskLinkCard.vue`'s `STAGE_COLOR` map so a stage chip reads identically on both surfaces — or decide deliberately that it should not.

`GradesView.vue` (stat band + evidence rows) is the closest existing analogue. Consult `ui-ux-regin-surfaces` and `frontend-style-convention`. Link the prototype as an asset; **do not ship it**.

---

**Constraint from [CAI-244](<https://linear.app/caiuswang/issue/CAI-244>)**: the declare control now has a settled contract — explicit `primary`, a refusal on an unknown trace, and an un-declare that must be reachable wherever declaring is. Two things land on the pixels: the session picker must offer **canonical traces only** (`canonical_trace` collapses a run onto the conversation it drove, so a picker listing raw run ids would let a human declare one id and see another stored), and **removing** an attribution needs a place to live on this page, not just an endpoint.

---

**Constraint from [CAI-245](<https://linear.app/caiuswang/issue/CAI-245>)**: the stage line is now **local-only and dated**. It has three renderings to design, not one — a stage *as of T*, **never fetched** (`stage: null`, `stage_synced_at: null`), and the moment after a **failed refresh** (the old stage and its old timestamp survive; the failure is only in the POST's response, never persisted). `STAGE_COLOR` **must not gain an** `unknown` **key** — a missing stage may not look like a stage chip — and the freshness rides *beside* the stage, since `Stage.to_dict()` stays flat. The page also owns a **Refresh stage** control (`POST /api/features/<key>/stage/refresh`), which is the only thing on this surface that reaches Linear: how it reads while in flight, and what a down tracker looks like next to a stage that is still on screen, is a pixel decision this prototype has to make.