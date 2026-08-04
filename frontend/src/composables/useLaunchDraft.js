import { reactive, toRefs } from 'vue'

// The /live launch form's in-progress input, held OUTSIDE the component that
// renders it. `LiveLaunchSheet` lives in a bottom sheet whose slot is a v-if,
// so every dismissal unmounts it — including the backdrop tap or Escape an
// operator naturally reaches for to back out of the "continue a session"
// picker. Owning the draft in the component meant that gesture silently threw
// away a prompt they had already typed and the session they had already
// picked, with no way to get either back.
//
// Module scope rather than a provide/inject from the view: the sheet has no
// never-unmounting ancestor that is meaningfully "the launch form's owner",
// and one draft per browser tab is what an operator expects from a form they
// reopen.
const blank = () => ({
  prompt: '',
  cwd: '',
  // The menu pick. `CUSTOM_MODEL` means "the id typed into `modelCustom`" —
  // held apart so switching to the menu and back does not lose what was typed,
  // and so the menu itself never has to carry an arbitrary string.
  model: '',
  modelCustom: '',
  effort: '',
  mode: '',
  oneShot: false,
  // The picked session row, or null for a fresh run. Holding the whole row
  // (not just its id) is what lets the form say which trace a pick lands on
  // and adopt its cwd without a second fetch.
  resume: null,
  // cwd/model as they stood before a pick overwrote them, so dropping the pick
  // puts them back. In the draft, not the component: a pick made, the sheet
  // dismissed and the pick dropped on reopen is the same undo.
  beforePick: null,
})

const draft = reactive(blank())
// The card the draft was written on. Surviving a dismissal is the point;
// surviving a walk to a different session is not — a resume target and the cwd
// adopted from it would follow the operator to an unrelated card and read as a
// choice they made there, one Launch away from continuing the wrong session.
let scope = null

export function useLaunchDraft(sessionId = '') {
  // Cleared only by a launch that actually started: anything short of that —
  // a refusal, a dismissal — leaves the operator's input where they left it.
  function resetDraft() {
    Object.assign(draft, blank())
  }
  // A falsy id is "not known yet", never "a different card": the view resolves
  // the session row asynchronously and the launch button is live before it
  // lands, so treating '' as a scope would wipe a draft typed on a slow load
  // the moment the id arrived — BUG 2 again, by a different route.
  if (sessionId) {
    if (scope !== null && scope !== sessionId) resetDraft()
    scope = sessionId
  }
  return { ...toRefs(draft), resetDraft }
}
