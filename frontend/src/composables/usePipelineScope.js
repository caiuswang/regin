import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

// The project is the ONE axis the three Pipeline tabs share, so it lives in
// the shell and rides the URL: `?project=` is `all` (absent), `none`, a repo
// id, or `ws:<id>` — every repo under that workspace. "No project" is a scope, not the absence of one — a feature filed
// under nothing has to be reachable, or it is invisible under every scope.
export const SCOPE_ALL = 'all'
export const SCOPE_NONE = 'none'
export const WS_PREFIX = 'ws:'

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

/** True when the scope names a workspace (`ws:<id>`). */
export function isWorkspaceScope(raw) {
  return /^ws:\d+$/.test(scopeParam(raw))
}

/** The workspace id a `ws:<id>` scope names, or null. */
export function workspaceIdOf(raw) {
  return isWorkspaceScope(raw) ? Number(scopeParam(raw).slice(WS_PREFIX.length)) : null
}

export function workspaceScope(id) {
  return `${WS_PREFIX}${id}`
}

function readStored() {
  try {
    return localStorage.getItem(STORAGE_KEY) || SCOPE_ALL
  } catch {
    return SCOPE_ALL
  }
}

// A ref rather than a bare localStorage read, so a computed that names the
// stored scope (the shell's chip beside a detail page) relabels on a pick.
const stored = ref(readStored())

/** The remembered scope, or `all`. */
export function storedScope() {
  return stored.value
}

/** Remember a scope as the one a bare path restores to. */
export function persistScope(scope) {
  stored.value = scope || SCOPE_ALL
  try {
    if (!scope || scope === SCOPE_ALL) localStorage.removeItem(STORAGE_KEY)
    else localStorage.setItem(STORAGE_KEY, scope)
  } catch {
    // Quota / private browsing. The scope still holds for this visit.
  }
}

// The scope the mounted Pipeline shell names, or null when no shell is on
// screen. What a surface outside the shell — the app-wide quick capture —
// files a new row under, so a capture made while looking at one project lands
// in it rather than in no project at all.
const shellScope = ref(null)

export function publishShellScope(scope) {
  shellScope.value = scope
}

/** The shell's scope as a project id, or null for all / none / no shell. */
export function shellProjectId() {
  return /^\d+$/.test(shellScope.value || '') ? Number(shellScope.value) : null
}

export function useShellScope() {
  return shellScope
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
    const remembered = storedScope()
    if (remembered === SCOPE_ALL) return
    if (isLegal && !isLegal(remembered)) {
      persistScope(SCOPE_ALL)
      return
    }
    router.replace({ path: route.path,
                     query: { ...route.query, project: remembered } })
  }

  return { scope, setScope, restoreStored }
}
