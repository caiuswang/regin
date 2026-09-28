import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'

// The project is the ONE axis the three Pipeline tabs share, so it lives in
// the shell and rides the URL: `?project=` is `all` (absent), `none`, or a
// repo id. "No project" is a scope, not the absence of one — a feature filed
// under nothing has to be reachable, or it is invisible under every scope.
export const SCOPE_ALL = 'all'
export const SCOPE_NONE = 'none'

const STORAGE_KEY = 'regin.pipeline.project'

/** The raw `?project=` value, or `all`. Never null, so callers never branch. */
export function scopeOf(query) {
  const raw = (query?.project ?? '').toString().trim()
  return raw || SCOPE_ALL
}

/** `all` carries no parameter — a default in the URL is noise, not state. */
export function scopeQuery(scope) {
  return scope && scope !== SCOPE_ALL ? { project: scope } : {}
}

/**
 * The scope as a *request* parameter, or `''` for "ask for no scope".
 *
 * The same rule as `scopeQuery`, one level down: every list endpoint spells
 * "every project" as an absent parameter and refuses the literal `all`
 * (`lib/defects/query.py::parse_project`, `lib/features/index.py`), so a tab
 * that forwards a hand-typed `?project=all` turns the default scope into a
 * 400 under a header still reading "All projects".
 */
export function scopeParam(raw) {
  const value = (raw ?? '').toString().trim()
  return value && value !== SCOPE_ALL ? value : ''
}

/** True when the scope names one project by id — not `all`, not `none`. */
export function isProjectScope(raw) {
  return /^\d+$/.test(scopeParam(raw))
}

/** The remembered scope, or `all`. */
export function storedScope() {
  try {
    return localStorage.getItem(STORAGE_KEY) || SCOPE_ALL
  } catch {
    return SCOPE_ALL
  }
}

/** Remember a scope as the one a bare path restores to. */
function persistScope(scope) {
  try {
    if (!scope || scope === SCOPE_ALL) localStorage.removeItem(STORAGE_KEY)
    else localStorage.setItem(STORAGE_KEY, scope)
  } catch {
    // Quota / private browsing. The scope still holds for this visit.
  }
}

export function usePipelineScope() {
  const route = useRoute()
  const router = useRouter()

  const scope = computed(() => scopeOf(route.query))

  function setScope(next) {
    const value = next || SCOPE_ALL
    persistScope(value)
    const query = { ...route.query, ...scopeQuery(value) }
    if (value === SCOPE_ALL) delete query.project
    // A scope change is not a place you go back to; it replaces the position
    // you are already at, and resets whatever page the old scope was on.
    delete query.page
    router.replace({ path: route.path, query })
  }

  /**
   * On a bare URL, restore the stored scope AND rewrite the URL, so what is
   * on screen is linkable. The URL always wins when it states a scope.
   */
  function restoreStored(isLegal) {
    if (route.query.project != null) return
    let stored = null
    try { stored = localStorage.getItem(STORAGE_KEY) } catch { stored = null }
    if (!stored || stored === SCOPE_ALL) return
    if (isLegal && !isLegal(stored)) {
      try { localStorage.removeItem(STORAGE_KEY) } catch { /* see above */ }
      return
    }
    router.replace({ path: route.path,
                     query: { ...route.query, project: stored } })
  }

  return { scope, setScope, restoreStored }
}
