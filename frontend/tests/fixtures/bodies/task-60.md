## Question

Does a real shell beside the conversation feel worth having — and where does it dock?

Build with `/prototype`, on `prototype/side-terminal`, reachable at `/prototype/side-terminal` (the `frontend/src/views/prototype/` convention — see the native-stage prototype for the `?v=` variant switch):

* **Variant A — right column.** The terminal takes the slot the agent companion pane uses at ≥xl (`xl:basis-[45%]`), with the drag handle from the split ticket's recommendation. Show what happens at ≥2xl when the detail rail is also open (three columns become four?) and what the companion pane does when both want the slot.
* **Variant B — bottom drawer.** A VS Code-style panel under the feed, height-resizable, feed keeps full width. Works below xl too.
* Both: a real shell via the spike's spawn path, `xterm` refitting on every drag, the existing read-only / in-control toggle, a close button that kills the shell per the lifecycle ticket, and the ratio remembered in `localStorage`.

React to it together; record in the resolution comment: which dock wins and why, what the split primitive's API turned out to be, what broke, and the honest cost of turning it into a spec (files touched in `SessionTraceView.vue`, whether the companion pane must move onto the same primitive first). Link the branch and the route.
