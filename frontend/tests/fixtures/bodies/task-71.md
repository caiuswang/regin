## Question

With the prototype in hand: **does this become a `/feature-pipeline` spec, and what does that spec fix?**

Decide, with `/grilling`:

1. **Go / no-go**, in one sentence each way, from the prototype's own evidence — not from the ambition that opened the map.
2. If go: which decisions are **locked into the spec** (save grain, conflict rule, reference format and delivery contract, editor and what `body` holds, gallery shape), which of the map's **Not yet specified** items graduate into it — the fate of `TaskEditSheet` / `LiveTaskSheet`, the image `src` scheme and whether external sources are allowed, comments gaining rich text, sanitisation — and which stay explicitly out.
3. **Whether the delivery contract change ships in the same feature.** Inline-reference-drives-delivery touches `lib/tasks/runs.py` and `images.payload_for`, not the SPA; it could be its own feature with its own tests. The prototype reports the cost.
4. **Split or one feature.** "Images render" and "editing in place" are separable and the first is far cheaper. Say whether they ship together or the render half goes first.
5. **Where the spec lives**: a fresh top-level task for `/feature-pipeline`, acceptance items as test names, per `docs/feature-pipeline.md`.

Resolution: the verdict, and — if go — the link to the spec task. This is the destination; the map closes with it.