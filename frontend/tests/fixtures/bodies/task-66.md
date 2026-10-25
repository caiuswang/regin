## Destination

**Images that render and a body you can edit, on `/tasks/:id`** — built, not specified. Pure frontend: the detail route already ships `task.images` (`_one_with_images`, `tasks.py:285`) and the page throws it away; `useTaskImages` already fetches, attaches and removes; `PatternContentEditor.vue` is already the source↔preview editing shape. **Zero backend change, zero new dependencies.**

This map was charted much wider — inline click-to-edit on every field, a WYSIWYG editor, and an inline image reference that *drives what a run is handed*. Ticket [TipTap and the agent-authored body](/tasks/67) priced the editor and the answer was to abort WYSIWYG; then the rest was weighed against the ask that opened the map — "we do not support image render and changing" — and the ambition turned out not to be in it. The four remaining tickets are closed out of scope and their questions parked below.

## Notes

**Domain**: `frontend/src/views/TaskDetailView.vue` (read-only today, 309 lines); the edit surfaces `components/tasks/TaskEditSheet.vue` + `composables/useTaskEdit.js` (whole-draft PATCH in a modal `Dialog` over a plain `Textarea`); `components/tasks/TaskImageStrip.vue` + `composables/useTaskImages.js`; the store `lib/tasks/images.py` (`agent_task_images`: `(task_id, idx)` unique, `bytes` BLOB, sha256, `idx` never reused) and its routes in `web/blueprints/tasks.py` (`POST/DELETE/GET /api/tasks/<id>/images[/<idx>]`, the GET `@require_auth`); the launch path `lib/tasks/runs.py` (`prompt_for`, `images.payload_for`); the renderer `components/MarkdownContent.vue` (`marked` + `v-html`, no sanitiser).

**Skills every session should consult**: `/grilling`, `/domain-modeling`; `/prototype` for the prototype ticket; `/research` for the research ticket. Honour `CLAUDE.md` root-cause discipline and scope control. **Check mature libraries before hand-rolling** (a standing preference — and the live question on this map).

**Tracker**: regin's own tasks, matching the sibling map [A terminal beside the conversation](/tasks/56) — Linear's free issue cap is reached. Child tasks are the tickets, `agent_task_deps` is the blocking relation, comments are the resolutions.

### What already exists

Task images are already a **first-class attachment list**, not a body concern: `agent_task_images` stores the bytes, `idx` addresses them forever (never reused, so `(task, idx)` is cacheable-hard), and they are attached by paste/drop/file-picker from `TaskImageStrip` inside the **edit modal and the composer only**. `TaskDetailView` renders the body through `MarkdownContent` and **never renders an image at all**. Editing is a modal that PATCHes every field at once on Save; images, by contrast, are written the instant they are pasted (deliberately — `TaskEditSheet`'s own comment explains why the two clocks differ). At launch, `runs.py:307` delivers images **beside** the prompt as content blocks and pointedly does *not* name them in it, "so the prompt stays the idea as it was written".

No editor library is installed — `marked` 18 renders, nothing composes. `reka-ui` 2.9.10 and `primevue` 4.5.5 are present; TipTap/ProseMirror is not.

### Constraints settled while charting

1. **The destination is the detail page, not a better modal.** Inline click-to-edit: click the title or body and it becomes editable in place; status / priority / project become inline controls. This is what "like Linear" was asked for, and it is the widest of the four framings that were on the table.
2. **Images are both gallery and inline.** The attachment list stays the store; the body may additionally *reference* one, and the renderer resolves it in place. Neither replaces the other.
3. **An inline reference drives delivery.** A referenced image rides in the prompt **at its position**; attachments not referenced still ride beside it as today. This is a deliberate contract change to `runs.py` / `images.payload_for`, and it reverses the "never name them in the prompt" rule for the referenced case only.
4. **The body editor is markdown source with a rendered preview — not WYSIWYG.** Live rich rendering while typing was **aborted** after ticket [TipTap and the agent-authored body](/tasks/67) measured its price (below). Markdown stays the truth in `body`, and because nothing round-trips it, **nothing is lost** — the editor reads and writes the exact bytes. What this gives up is seeing an image *while typing*; it does not give up constraints 2 or 3, which a source editor answers fine (paste attaches the image and inserts a reference at the cursor; the preview renders it).
5. **Inline click-to-edit still applies to the non-body fields** — title, status, priority, project. Those have no rendering problem, so the preview decision does not touch them.

## Decisions so far

<!-- one line per closed ticket -->

* [TipTap and the agent-authored body](/tasks/67) — **markdown stays the truth in `body`**; ProseMirror-JSON-as-truth ruled out on measurement (**2.30× inflation** puts a real 29,293-char spec at 82,485 against `BODY_MAX` 64,000, and `prompt_for`'s `text[:8000]` slice becomes invalid JSON). It recommended WYSIWYG via `@tiptap/markdown` and priced the round trip: 8 of 25 real bodies byte-identical, 11% of lines touched, **HTML comments deleted** — including this map's own `<!-- -->` markers. **On that evidence the destination was redrawn** (constraint 4) and its **named runner-up promoted**: a markdown-source editor over a `marked` preview, the only zero-round-trip option. Findings: `docs/research/task-body-editor.md` @ `ae3b7700`.

## Not yet specified

<!-- parked, not lost: each was a live ticket, closed out of scope when the destination shrank -->

* **An inline reference driving delivery** — was [ticket 68](/tasks/68). The minimal build renders `![](image:N)` in the body **for display only**; a run still receives every attachment beside the prompt, exactly as `runs.py:307` has always done. Making a reference decide *position and inclusion* is a real contract change to `images.payload_for`, and nothing has yet needed it.
* **Inline click-to-edit on every field, and its conflict model** — was [ticket 69](/tasks/69). Per-field PATCH vs whole-draft, an `updated_at` precondition, where a refusal goes on a page with eight editable fields, and what happens when `regin task ship` writes the row with the server stopped. The minimal build edits the body through one explicit Edit/Save, so none of it is load-bearing yet.
* **The fate of `TaskEditSheet` and `LiveTaskSheet`.** The detail page now edits the body too, so there are two body editors. They have not drifted yet. When one of them changes and the other does not, this becomes real.
* **Gallery depth** — reordering, captions/alt text, drag-to-reorder, replacing an image in place.
* **Comments.** Linear's take images and rich text; `TaskComments` takes neither.
* **Sanitisation.** `MarkdownContent` is `marked` + `v-html` with no sanitiser, across ~20 components. The preview is now the primary way a body is read, and it renders images. Repo-wide, not this map's to fix.


## Out of scope

* **Everything the destination shed.** Tickets [68](/tasks/68), [69](/tasks/69), [70](/tasks/70) and [71](/tasks/71) are closed unresolved — not answered, out-scoped. Their questions are parked above and return only if something actually needs them.
* **WYSIWYG / live rich rendering of the body.** Aborted deliberately, on ticket 67's measurement — an 11%-of-lines round trip that deletes HTML comments is not worth paying in a column that holds specs and is written by agents. The preview page gives the same reading experience for zero loss.
* [Which bodies may be WYSIWYG-edited, and what the operator is told](/tasks/72) — closed unresolved. It existed only to police round-trip loss; with no round trip there is no loss and no policy to write.
* **Building the feature.** The destination is a verdict and a spec; `/feature-pipeline` builds from it.
* **Replacing `MarkdownContent` repo-wide.** Memory, inbox, patterns, trace cards and present-mode all render through it; swapping the renderer everywhere is a separate effort with its own blast radius.
* **Collaborative / multiplayer editing** (Yjs, presence cursors, live co-edit). regin is a single-operator tool; Linear's collaboration layer answers a question nobody here has.
* **Image editing or annotation** (crop, markup, arrows). Linear does not do it either.
* **Moving `agent_task_images` off the BLOB column** to object storage or the filesystem. Storage is not what is broken; rendering is.