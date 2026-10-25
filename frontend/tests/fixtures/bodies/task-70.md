## Question

**What does a Linear-grade `TaskDetailView` actually feel like — and is inline editing better than the modal it replaces, or just different?**

Build a throwaway prototype on a `prototype/task-detail-linear` branch, reachable in the browser, and react to it. Use `/prototype`.

Make concrete, at whatever fidelity answers the question fastest:

1. **Editing in place.** Title and body become editable on click; status / priority / project become inline controls. Wire the save grain and conflict rule that ticket [When an inline edit saves](/tasks/69) settled — the prototype is where that rule gets *felt*, including a refused save.
2. **The body editor: markdown source over a rendered preview** (map constraint 4 — WYSIWYG is out of scope). **Start from `frontend/src/components/PatternContentEditor.vue`** — 107 lines, already does exactly this shape (rendered `MarkdownContent` when idle, plain `<textarea>` when editing) with **zero dependencies**. Only reach for CodeMirror 6 (ticket 67's runner-up) if the plain textarea demonstrably fails; load a **real** long spec body to find out (`sqlite3 db/regin.db "select body from agent_tasks where length(body) > 2000"` — this map's own body will do, and it carries the `<!-- -->` markers that must come back unharmed). Report which one you needed and why. Decide the preview affordance too: tab, split, or toggle.
3. **Images, both ways.** The existing attachments rendered as a gallery on the page, *and* one referenced inline in the body per ticket [What names an attachment inside the body](/tasks/68). Paste an image straight into the body and see where it lands.
4. **The gallery interaction** — enough of grid / click-to-zoom / keyboard nav to judge it. This graduates the map's fog on lightbox detail, or proves it does not matter.

Report, for the verdict ticket to use:

* What the prototype **settled** and what it **broke** — especially any constraint on the map that turned out wrong.
* The **cost** of the editor once real: bundle delta (zero, if the textarea held), and whether `vue-complexity` thresholds survive `TaskDetailView` growing an editor and a gallery (it is 309 lines today).
* Whether the modal still has a job, or is now dead weight (the map's fog on `TaskEditSheet` / `LiveTaskSheet`).
* A screenshot or three, linked from the resolution.