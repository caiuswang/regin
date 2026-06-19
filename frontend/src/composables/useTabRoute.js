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
export function useTabRoute({ param = 'tab', default: def = '', valid = null } = {}) {
  const route = useRoute()
  const router = useRouter()
  return computed({
    get() {
      const v = route.query[param]
      if (v == null) return def
      if (valid && !valid.includes(v)) return def
      return v
    },
    set(v) {
      const q = { ...route.query }
      if (v === def || v == null) delete q[param]
      else q[param] = v
      router.push({ query: q })
    },
  })
}
