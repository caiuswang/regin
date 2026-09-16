import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { scopeParam } from './usePipelineScope'

// The defect list's whole state, held in `location.search` and nowhere else.
//
// A list is a predicate plus a position, and both are facts about what is on
// screen — so both have to be in the URL or the screen is not linkable. The
// vocabulary is the server's (`lib/defects/query.py`): scope, status, q, sort
// and the two followed keys, spelled exactly as the API spells them, because a
// second spelling here would be a second rulebook to keep in step.

export const SCOPES = [
  { value: 'all', label: 'All' },
  { value: 'unattributed', label: 'Unattributed' },
]

export const STATUSES = [
  { value: 'all', label: 'Any status' },
  { value: 'open', label: 'Open' },
  { value: 'closed', label: 'Closed' },
]

export const SORT_KEYS = [
  { value: 'opened', label: 'Opened' },
  { value: 'updated', label: 'Updated' },
  { value: 'round', label: 'Round' },
  { value: 'status', label: 'Status' },
  { value: 'feature', label: 'Feature' },
]

export const DEFAULT_SORT = 'opened:desc'
export const DEFAULT_SIZE = 50

const DEFAULTS = {
  scope: 'all', status: 'all', q: '', sort: DEFAULT_SORT,
  page: '0', size: String(DEFAULT_SIZE),
}

// The keys a reader arrives on rather than chooses. They survive every control
// change: a filter applied inside "the defects of feature 12" must not
// silently widen to the whole ledger. `project` is the shell's own axis (R1) —
// this tab reads and forwards it but never sets it, and dropping it on the
// next control change would widen the list under a header still naming one
// project.
const KEYS = ['feature_id', 'fix_item', 'project']

const AXES = ['scope', 'status', 'q', 'sort', 'page', 'size']

function read(query, axis) {
  const raw = String(query[axis] ?? '').trim()
  return raw || DEFAULTS[axis]
}

export function useDefectsQuery() {
  const route = useRoute()
  const router = useRouter()

  const state = computed(() => {
    const q = route.query
    return {
      scope: read(q, 'scope'),
      status: read(q, 'status'),
      q: read(q, 'q'),
      sort: read(q, 'sort'),
      page: Math.max(0, parseInt(read(q, 'page'), 10) || 0),
      size: Math.max(1, parseInt(read(q, 'size'), 10) || DEFAULT_SIZE),
      feature_id: String(q.feature_id ?? '').trim(),
      fix_item: String(q.fix_item ?? '').trim(),
      // `all` is the default scope, and a default never rides in a request:
      // the server spells that scope as "no parameter" and refuses the
      // literal, so the sentinel is dropped here exactly as `scopeQuery`
      // drops it from the URL.
      project: scopeParam(q.project),
    }
  })

  const sortKey = computed(() => state.value.sort.split(':')[0])
  const sortDir = computed(
    () => (state.value.sort.split(':')[1] === 'asc' ? 'asc' : 'desc'))

  // What the server is asked for. `page`/`size` are always sent; the rest only
  // when they narrow something, so a default never rides in the request.
  const apiQuery = computed(() => {
    const s = state.value
    const params = new URLSearchParams()
    for (const key of KEYS) if (s[key]) params.set(key, s[key])
    if (s.scope !== DEFAULTS.scope) params.set('scope', s.scope)
    if (s.status !== DEFAULTS.status) params.set('status', s.status)
    if (s.q) params.set('q', s.q)
    params.set('sort', s.sort)
    params.set('page', String(s.page))
    params.set('size', String(s.size))
    return params.toString()
  })

  /**
   * Move one or more axes. Anything but an explicit `page` is a change to the
   * predicate, so the position resets — page 4 of a different list is a
   * position nobody asked for. `replace`, not `push`: typing in a search box
   * must not build a history entry per keystroke.
   */
  function patch(next) {
    const merged = { ...state.value, ...next }
    if (!('page' in next)) merged.page = 0
    const query = {}
    for (const key of KEYS) if (merged[key]) query[key] = merged[key]
    for (const axis of AXES) {
      const value = String(merged[axis] ?? '')
      if (value && value !== DEFAULTS[axis]) query[axis] = value
    }
    router.replace({ path: route.path, query })
  }

  function reset() {
    patch({ scope: DEFAULTS.scope, status: DEFAULTS.status, q: '',
            sort: DEFAULT_SORT, size: String(DEFAULT_SIZE) })
  }

  // A control is "narrowing" only if it is off its default. The scope pills
  // are excluded on purpose: `all` and `unattributed` are both scopes, so
  // neither is a filter to clear.
  const narrowed = computed(
    () => state.value.status !== DEFAULTS.status
      || !!state.value.q
      || state.value.sort !== DEFAULT_SORT)

  return { state, apiQuery, sortKey, sortDir, patch, reset, narrowed }
}
