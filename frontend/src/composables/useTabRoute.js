import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'

// Make an in-view subtab / section selector deep-linkable by syncing it with a
// URL query param (e.g. /settings?section=triggers, /memory?tab=recall).
//
// Returns a computed you can hand straight to `v-model` on <Tabs> or assign to
// (`model.value = 'x'`). The default value is kept *out* of the URL so canonical
// URLs stay clean, and unknown values (stale links, typos) fall back to it when
// a `valid` allow-list is given. Uses router.push so the browser Back button
// steps through subtab history, matching the existing RulesView convention.
//
// `resets` names sibling params scoped to one tab's content (a filter that only
// means something inside that tab); switching tabs drops them.
export function useTabRoute({ param = 'tab', default: def = '', valid = null, resets = [] } = {}) {
  const route = useRoute()
  const router = useRouter()
  function current() {
    const v = route.query[param]
    if (v == null) return def
    if (valid && !valid.includes(v)) return def
    return v
  }
  return computed({
    get: current,
    set(v) {
      const q = { ...route.query }
      if (v !== current()) for (const r of resets) delete q[r]
      if (v === def || v == null) delete q[param]
      else q[param] = v
      router.push({ query: q })
    },
  })
}
