import { onUnmounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

const SCROLLER = '.content-scroll'

// Module scope, not instance scope: a task's detail is a route OUTSIDE this
// shell, so the shell unmounts on the way to it — and a map that died with the
// shell could not tell the detail's back link which list it was opened from.
//
// Deliberately NOT the same map the tab strip navigates with below. A back
// link answers "the list this row was opened from", which outlives the shell;
// a tab link answers "where this tab was when you last switched away", which
// does not — merging them would carry a search someone typed in an earlier
// visit into a tab they clicked in this one.
const leftQueries = new Map()

/**
 * The query a Pipeline tab was last left under, or `{}` for a path nobody has
 * left yet. What a detail view's back link navigates with, so returning lands
 * on the list you opened the row from rather than on the default one.
 */
export function rememberedQuery(path) {
  return { ...(leftQueries.get(path) || {}) }
}

/**
 * usePipelineTabState — what a Pipeline tab is owed when you come back to it.
 *
 * Keeping the component alive preserves its refs, but not the two things that
 * live outside it: the query string the router drops on navigation, and the
 * scroll offset the app-shell scroller owns. Both are remembered against the
 * tab's own path — an identifier, so the restore cannot land on the wrong tab
 * however the navigations interleave.
 *
 * @param {(path: string) => boolean} isTab — is this path one of the tabs?
 */
export function usePipelineTabState(isTab) {
  const route = useRoute()
  const router = useRouter()
  const queries = new Map()
  const offsets = new Map()

  // A guard, not a watcher: by the time a watcher on `route.path` fires the
  // outgoing view is already gone and its scroller reads 0.
  const stop = router.beforeEach((to, from) => {
    if (!isTab(from.path)) return
    queries.set(from.path, { ...from.query })
    leftQueries.set(from.path, { ...from.query })
    const el = document.querySelector(SCROLLER)
    if (el) offsets.set(from.path, el.scrollTop)
  })
  onUnmounted(stop)

  // `flush: 'post'` is the whole of the timing story: it runs after the render
  // that re-activates the tab has put its DOM back, so the scroller has the
  // height to hold the offset. Restoring any earlier clamps it to 0.
  watch(() => route.path, (path) => {
    if (!isTab(path)) return
    const el = document.querySelector(SCROLLER)
    if (el) el.scrollTop = offsets.get(path) ?? 0
  }, { flush: 'post' })

  /** The query to navigate a tab with: what it had, under the live scope. */
  function queryFor(path, scopeQuery) {
    const remembered = { ...(queries.get(path) || {}) }
    delete remembered.project
    return { ...remembered, ...scopeQuery }
  }

  return { queryFor }
}
