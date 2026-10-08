// Where a feature's figures lead. One spelling per destination, because the
// index row and the ledger tile both link to the same three scopes and two
// spellings of one key is how a count comes to open a list that does not
// match it.

//: The id the evidence list carries, so "9 sessions" can reach the nine rows.
//: The sessions LIST (`/trace/sessions`) filters in component state rather
//: than in its URL, so it cannot be scoped to a feature by link at all.
export const SESSIONS_ANCHOR = 'feature-sessions'

//: The id the feature page's defect list carries.
export const DEFECTS_ANCHOR = 'feature-defects'

//: The defects a feature caused, listed on its own page — there is no defects
//: list outside it.
export function defectsLink(featureId) {
  return { path: `/features/${featureId}`, hash: `#${DEFECTS_ANCHOR}` }
}

//: Every defect blamed on a feature, on the `/defects` list page — past the
//: row cap the feature page itself draws.
export function defectListLink(featureId) {
  return { path: '/defects', query: { feature_id: String(featureId) } }
}

//: The tasks that are a feature's steps. `feature_id` is
//: `lib/tasks/query.py`'s own parameter name, and it wins over `root_only`
//: there, so the link shows every task the index counted.
export function tasksLink(featureId) {
  return { path: '/tasks', query: { feature_id: String(featureId) } }
}

//: The same feature-scoped backlog in the list view. The explicit `view=list`
//: matches the header contract on the feature ledger; callers that just need
//: the scope should use `tasksLink`.
export function tasksListLink(featureId) {
  return { path: '/tasks', query: { feature_id: String(featureId), view: 'list' } }
}

//: The same feature-scoped backlog drawn as lanes, including closed chains so
//: the preview shows the whole dependency graph, not only what is open.
export function tasksGraphLink(featureId) {
  return { path: '/tasks', query: { feature_id: String(featureId), view: 'graph', open: '0' } }
}

//: A feature's sessions, which live on its ledger page beside the figure.
export function sessionsLink(featureId) {
  return { path: `/features/${featureId}`, hash: `#${SESSIONS_ANCHOR}` }
}

/**
 * Put the evidence list on screen.
 *
 * The hash alone does nothing here: `router-link` preventDefaults the native
 * anchor jump, this router declares no `scrollBehavior`, and the page scrolls
 * inside `.content-scroll` rather than the window — so a `#hash` link is
 * inert on every one of the three counts. `scrollIntoView` walks to the
 * nearest scrollable ancestor, which is that container.
 */
export function reveal(anchor) {
  document.getElementById(anchor)
    ?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

export function revealSessions() {
  reveal(SESSIONS_ANCHOR)
}

//: The spec, slices and task cards — where a feature's `next_step` sends a
//: reader. `slices` is `lib/features/next_step.SLICES_ANCHOR`.
export const SPEC_ANCHOR = 'feature-spec'
export const SLICES_ANCHOR = 'slices'
export const TASKS_ANCHOR = 'feature-tasks'
export const IMAGES_ANCHOR = 'feature-images'

//: The Slices panel, where a feature's idea slices are armed.
export function slicesLink(featureId) {
  return { path: `/features/${featureId}`, hash: `#${SLICES_ANCHOR}` }
}

//: The anchors a ledger arrival may carry, and so may reveal.
export const LEDGER_ANCHORS = [
  SESSIONS_ANCHOR, DEFECTS_ANCHOR, SPEC_ANCHOR, SLICES_ANCHOR, TASKS_ANCHOR,
  IMAGES_ANCHOR,
]
