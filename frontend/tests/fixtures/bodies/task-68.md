## Question

**What names an attachment from inside the body, and what does a run then actually receive?**

The map has settled that an inline reference *drives delivery* (constraint 3) — which reverses, for the referenced case, the rule `runs.py:307` states outright: images ride **beside** the prompt and are pointedly never named in it, "so the prompt stays the idea as it was written".

Decide, with `/grilling` and `/domain-modeling`:

1. **The reference itself.** It must be resolvable by *two* readers that share nothing: the SPA renderer (an `<img>` in a browser holding a session cookie) and `lib/tasks/images.payload_for` (assembling content blocks for a run). `(task_id, idx)` is the only stable name the store offers and `idx` is never reused. Is the reference an `idx`, a sha256, a synthetic URL — and does the *body* carry the task id at all, given a body can be copied between tasks?
2. **What the agent receives.** A referenced image becomes a content block at that position; what stands in its place in the text? Nothing? A caption? An `@path`, as `bridge.py:_with_image_paths` already does for the keystroke transport?
3. **The four broken cases**, each answered: a body referencing an image that was removed; an attachment referenced twice; a reference copied into a *different* task's body; an image attached but never referenced (must still ride beside — constraint 3).
4. **Who resolves it.** Does `payload_for` learn to parse the body, or does `prompt_for` hand it an already-parsed reference list? These are two different owners of the same rule.
5. **What the operator does to create one.** The editor is markdown source over a preview (map constraint 4), so a reference is text the operator can also type by hand. Decide what pasting an image into the body does: attach, then insert the reference at the cursor — in what syntax, and what happens on paste into a task that is not yet saved.

Resolution: the reference format, the delivery rule with all four cases decided, and the owning module.