## Question

Which mechanism gives the trace page a drag-resizable split between the conversation feed and a terminal pane, in a shape that (a) can later host the existing agent companion pane and (b) does not fight the page's three breakpoint modes?

Survey the candidates against the real constraints, not in the abstract:

* **Libraries**: `splitpanes` (Vue 3, most used), Reka UI's `Splitter` (`reka-ui` is already a dependency — check whether the splitter primitive exists in the pinned version), `dockview-vue` (full dock system — oversized here, but note what a later app-wide effort would inherit), anything else with >1k stars and a 2025+ release.
* **Hand-rolled**: a pointer-capture resizer on a CSS grid track, the way `TraceAgentPane.vue`'s fixed-basis split would be extended.

For each: bundle size, Tailwind v4 / CSS-variable friendliness, keyboard accessibility of the handle (the repo's `frontend-style-convention` engine will flag an unlabelled drag handle), whether it emits a resize event xterm's `FitAddon` can hook (a terminal that does not refit on drag is unusable), horizontal *and* vertical orientation (the prototype renders both a right column and a bottom drawer), min/max pane constraints, and whether the container can be made `overflow-y: auto` per pane the way the companion pane already is.

Deliverable: a findings file at `docs/research/side-terminal-split.md` on a throwaway `research/side-terminal-split` branch, ending with one recommendation and the runner-up, plus a sentence on what each would cost to swap out later. Verify the Reka UI claim against `frontend/node_modules/reka-ui`, not the docs.
