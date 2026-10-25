## Question

**When does an inline edit save, and what happens when it collides with a write it did not make?**

Today one modal PATCHes every field at once (`useTaskEdit.save` — "Every field is sent, including an emptied one: clearing the details is an edit, and omitting the key would read as 'leave it'"), and Cancel really cancels. Editing in place breaks that model: there is no Save button and no Cancel.

Decide, with `/grilling` and `/domain-modeling`:

1. **Grain of a save.** Per-field PATCH on blur/⌘↵, or a debounced whole-draft PATCH? The existing "send every key" rule exists to make *clearing* a field expressible — a per-field PATCH must keep that property.
2. **The collision.** A task is not edited only by its operator: a run writes `status`, `regin task ship` writes `claimed_at` directly with the server stopped, and another browser tab may hold the same row. Does a save carry an `updated_at` precondition and get refused, or last-write-win? A refusal needs somewhere to *go* when there is no modal to show it in. (`CLAUDE.md` root-cause discipline: identifier/state-based, not a poll or a skew window.)
3. **What the two clocks become.** Text is a draft until Save; an image is written the instant it is pasted. Inline editing collapses the text clock toward the image one — say whether they unify, and what the operator is then told.
4. **Refusal surface.** `parse_update` names the offending field and the current code deliberately renders the server's own sentence. Where does that sentence appear on a page with eight independently-editable fields?
5. **`canWrite`.** A viewer must not get an edit affordance that 403s on blur. Read off the stored role, as today.

Resolution: the save grain, the conflict rule, the refusal surface, and which of `useTaskEdit` / `TaskEditSheet` this reshapes.