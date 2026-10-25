## Question

If TipTap/ProseMirror edits a task's body, **what does the `body` column then hold** — and what does that cost every other reader of it?

The field is not a UI string. Agents write it (a feature's whole spec lives there), agents read it (`lib/tasks/runs.py:prompt_for` turns it into the launch prompt), the CLI prints it, the capture surface writes it bounded at 8,000 chars and the authenticated edit path at 64,000. A WYSIWYG editor that normalises on every save will rewrite text no human touched.

Investigate and report, with evidence, not opinion:

1. **Markdown-as-truth.** Which TipTap markdown path is actually maintained (`tiptap-markdown`, `@tiptap/static-renderer`, prosemirror-markdown directly) — maturity, maintenance, bundle cost, and whether images/tables/fenced code survive a round trip. Prove it: round-trip **real** bodies from this install (`sqlite3 db/regin.db "select body from agent_tasks where length(body) > 800"`) and **diff** them. Report what changed, per construct.
2. **ProseMirror-JSON-as-truth.** What a migration costs: the `body` column, `parse_update`'s 64,000 bound, `prompt_for`, the CLI's `task show`, the capture surface, and every agent that has ever written markdown into this field. Name the concrete call sites.
3. **The escape hatch.** Does the chosen path admit a raw-markdown toggle, and does toggling lose anything?
4. **Anything already in the tree.** `marked` 18 is installed and `reka-ui` 2.9.10 / `primevue` 4.5.5 are present — does either already ship an editor that clears the bar? (`CLAUDE.md`: check mature libraries before reinventing.)

A **recommendation with a named runner-up** and the reason, in the shape of ticket 57's resolution on the sibling map.

Capture findings as Markdown in the repo on a throwaway `research/task-body-editor` branch; the resolution comment links it.