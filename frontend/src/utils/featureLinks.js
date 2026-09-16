// Where a feature's figures lead. One spelling per destination, because the
// index row and the ledger tile both link to the same three scopes and two
// spellings of one key is how a count comes to open a list that does not
// match it.

//: The id the evidence list carries, so "9 sessions" can reach the nine rows.
//: The sessions LIST (`/trace/sessions`) filters in component state rather
//: than in its URL, so it cannot be scoped to a feature by link at all.
export const SESSIONS_ANCHOR = 'feature-sessions'

//: The defects a feature caused. `feature_id` is `lib/defects/query.py`'s own
//: parameter name.
export function defectsLink(featureId) {
  return { path: '/defects', query: { feature_id: String(featureId) } }
}

//: The tasks that are a feature's steps. `feature_id` is
//: `lib/tasks/query.py`'s own parameter name, and it wins over `root_only`
//: there, so the link shows every task the index counted.
export function tasksLink(featureId) {
  return { path: '/tasks', query: { feature_id: String(featureId) } }
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
export function revealSessions() {
  document.getElementById(SESSIONS_ANCHOR)
    ?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}
