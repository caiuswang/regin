A defect is a **declared** bug report stored as a `defects` row. Nothing infers one: an inferred row would make the metric measure the inference. `lib/defects/store.py` is the only writer, so its rules hold for the CLI and the web API alike.

### Lifecycle (status axis)

```
open --close(missing_check)--> closed --reopen--> open   (round += 1, missing_check cleared)
open --decline(reason?)------> closed (triage_state = declined)
open|closed --merge(into)----> [row deleted]  (paths + fix item folded into keeper; audit in activity log)
```

`merge` deletes the duplicate on purpose. Routing it through `close` would force a fabricated `missing_check`, and the row would still count against the feature.

### Triage axis (orthogonal to status)

| `triage_state` | Set by | In queue? |
|---|---|---|
| `NULL` | new row, `attribute`, `reopen` | yes, while open and unattributed |
| `snoozed` | `snooze` (open, unattributed only) | no, until any write bumps `activity_seq` past `snooze_watermark` |
| `declined` | `decline` (also closes, `missing_check` stays NULL) | no |

`store.in_queue()` is the one queue predicate: `feature_id IS NULL AND status = open AND (triage NULL OR snoozed with watermark ≠ activity_seq)`. Snooze is event-driven. It uses no timer.

### Rules

| Rule | Why |
|---|---|
| Recording an already-open symptom (same `symptom_key` under the same feature, or the same reporting session if unattributed) dedups and returns `DEDUPED` | one bug, one row |
| A dedup hit on a **closed** row is refused and points at `reopen` | re-typing a string must not move the round |
| `feature_id` names the feature that **caused** the bug; `reported_by_trace` only records who reported it | nothing propagates a session's feature onto a defect |
| Attribution is late-binding; `attribute` refuses a symptom clash and suggests `merge` | a guessed parent is worse than a gap |
| Closing requires `missing_check`, which is non-NULL iff closed | summed per subsystem, these rank the missing gates |
| `round` moves only through `reopen` and is never summed into a feature's rounds | separate axis from rounds-to-done ([[feature-lifecycle-ledger]]) |

### Escapes (`escapes.py`)

When a defect's feature is first set, `_bind_blame` freezes that feature's **current last event id** onto the row. Re-attribution to a different feature re-takes the pointer. The defect is an escape iff the blamed event's kind is `shipped`. Clocks are never compared. Known distortion: a bug found during the build but blamed after ship counts as an escape. Filing a defect never walks the feature back.

### Missing-check redemption (`checks.py`)

`missing_check` is a claim. At read time `checks.py` looks for it among the checks the repo actually holds (a pytest name, a Playwright title, a file, and so on), trying the most specific spelling first. Results are counted as written, not written, unverified or unnamed. No column or cache stores them.

### Fix items & images

`fix_items.create_for` creates the task that will close a defect. The title is the symptom verbatim, the body comes from the `defect-fix-task` prompt surface, and the defect's images are copied to the task. `set_fix_item` / `clear_fix_item` are pointer writes. `close` never touches the pointer. `images.py` binds `lib/attached_images` to `defect_images`. These images are evidence for a human reader and are never delivered to a model.

### Gotcha

`rounds.py` / `tracker.py` count completions that were walked back in a remote tracker's state history. They exist **only** to fill a Linear snapshot's `rounds` field. When the tracker is unreachable, `tracker.py` raises `TrackerUnavailable` instead of returning `1`. They are not the feature rounds path.