import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'

// A Pipeline route: a tab, the defects list, or any of their detail pages.
const IN_SHELL = /^\/(overview|tasks|features|defects)(\/\d+)?(\?|#|$)/

function labelOf(fullPath) {
  const [, section, id] = fullPath.match(IN_SHELL) || []
  if (!section) return 'Back'
  if (id) return `${section[0].toUpperCase()}${section.slice(1).replace(/s$/, '')} #${id.slice(1)}`
  return section[0].toUpperCase() + section.slice(1)
}

/**
 * useDetailBack — where a detail page's back link goes.
 *
 * The route the reader came from, with its query, when that route is in the
 * Pipeline: the history entry vue-router recorded as this one's `back` is an
 * identifier of exactly that place, so returning is `router.back()` — no
 * forward entry is pushed, and two details linking to each other cannot turn
 * Back into a loop. A deep link, or an arrival from outside the Pipeline, has
 * no such entry and goes to `fallback()` — the parent tab.
 *
 * @param {() => { to: object|string, label: string }} fallback
 */
export function useDetailBack(fallback) {
  const route = useRoute()
  const router = useRouter()

  const origin = computed(() => {
    // Read so the computed re-evaluates per navigation; history.state is not
    // reactive, but it is written before the route is.
    const here = route.path
    const back = window.history.state?.back
    if (typeof back !== 'string' || !IN_SHELL.test(back)) return null
    // An in-page anchor (`#feature-defects`) is a history entry on this same
    // page; going "back" to it would leave the reader where they are.
    return back.split(/[?#]/)[0] === here ? null : back
  })

  const to = computed(() => origin.value || fallback().to)
  const label = computed(() => (origin.value ? labelOf(origin.value) : fallback().label))

  /** Leave for the back link's target — also what a page does once its row is gone. */
  function leave() {
    if (origin.value) router.back()
    else router.push(to.value)
  }

  function go(event) {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button) return
    event.preventDefault()
    leave()
  }

  return { to, label, go, leave }
}
