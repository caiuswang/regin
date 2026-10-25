## Question

**Which bodies may be WYSIWYG-edited at all, and what is the operator told when a save would rewrite text they did not touch?**

Ticket [TipTap and the agent-authored body](/tasks/67) settled the editor and then measured its price: over the 25 real bodies >800 chars in this install, `@tiptap/markdown` leaves **8 byte-identical** and touches **11% of lines**. Three losses are structural, not cosmetic:

* **HTML comments are deleted.** `<!-- one line per closed ticket -->` is a structural marker on the wayfinder maps themselves — tasks 56 and **66, this very map**. Opening it in the editor would eat it.
* **Raw XML-ish blocks come back entity-escaped as visible text** — agent-pasted `<issue identifier=…>` wrappers (tasks 22, 23).
* **A link destination containing a space becomes invalid markdown.**

A raw-markdown escape hatch is cheap and possible, but it only makes the loss **opt-in per task**. Something still has to decide who opts in.

Decide, with `/grilling` and `/domain-modeling`:

1. **Detect, or declare?** The research proposes *detect-and-default-to-raw*: round-trip the body in the browser before offering the editor, and fall back to source mode when it would change. Is a detector the right instrument, or does the task itself declare its mode (a field, a tag, `source`)? A detector is a heuristic that can be wrong in the expensive direction.
2. **What the operator sees.** A page that silently opens in a different editing mode for some tasks and not others is a UI that behaves differently for reasons the operator cannot see. What is shown, and where?
3. **The safe-by-default mechanism.** `onUpdate` fires only on `docChanged` (verified in `@tiptap/core`), so *viewing* a lossy body writes nothing. Confirm that is enough to make merely opening a spec safe, and say what else must be gated on it.
4. **The self-referential case.** Wayfinder maps are `agent_tasks` rows edited through this same page, and their `<!-- -->` markers are load-bearing. Is "do not lose a map's own structure" a constraint the design must satisfy, or is the answer that maps are simply always raw?
5. **Does the loss shrink?** Is any of the 11% recoverable with configuration (marker/wrap settings already bought 8 byte-identical bodies), and is chasing it worth it — or is the policy the answer and the fidelity what it is?

Resolution: the mode-selection rule, the mechanism that enforces it, and what the operator is shown.