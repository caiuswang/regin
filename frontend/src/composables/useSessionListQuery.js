import { computed, nextTick, ref, watch } from 'vue'
import { useCursor } from './useCursor'
import { useServerClock } from './useServerClock'

// Every narrowing axis of the sessions list — its persisted values, its
// options, the request it builds and the envelope facets it reads back.
// Extracted from SessionsView so that view stays under the vue-complexity
// surface-area threshold and so the toolbar can be driven by plain v-models.

const TEST_TOGGLE_KEY = 'regin_sessions_show_tests'  // legacy; migrates into KIND_KEY on first read
const KIND_KEY = 'regin_sessions_kind'
const ACTIVE_KEY = 'regin_sessions_active'
const RANGE_KEY = 'regin_sessions_range'
const SCOPE_KEY = 'regin_sessions_search_scope'
const TAG_KEY = 'regin_sessions_tag'
const REPO_KEY = 'regin_sessions_repo'

export const SCOPE_OPTIONS = [
  { value: 'title', label: 'Title' },
  { value: 'prompt', label: 'Prompt' },
  { value: 'both', label: 'Both' },
]

export const KIND_OPTIONS = [
  { value: 'real', label: 'Real only' },
  { value: 'test', label: 'Tests' },
  { value: 'all', label: 'All' },
]

export const ACTIVE_OPTIONS = [
  { value: 'all', label: 'Any' },
  { value: 'active', label: 'Active' },
  { value: 'inactive', label: 'Ended' },
]

export const RANGE_OPTIONS = [
  { value: 'all', label: 'All time' },
  { value: 'today', label: 'Today' },
  { value: 'yesterday', label: 'Yesterday' },
  { value: '7d', label: 'Last 7 days' },
  { value: '30d', label: 'Last 30 days' },
]

const DEFAULTS = { kind: 'real', active: 'all', range: 'today', tag: '', repo: 'all' }

function stored(key, options, fallback) {
  const v = localStorage.getItem(key)
  return options.some(o => o.value === v) ? v : fallback
}

function pad(n) { return String(n).padStart(2, '0') }

function toLocalIso(d) {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
    + `T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

// Boundaries are computed in the browser (user's local clock) and serialized
// as naive local ISO so the lexicographic compare on the server matches the
// stored text format.
function rangeBounds(key) {
  const now = new Date()
  const startOfDay = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const tomorrow = new Date(startOfDay); tomorrow.setDate(tomorrow.getDate() + 1)
  const back = (days) => {
    const s = new Date(startOfDay); s.setDate(s.getDate() - days); return s
  }
  const windows = {
    today: () => ({ since: toLocalIso(startOfDay), until: toLocalIso(tomorrow) }),
    yesterday: () => ({ since: toLocalIso(back(1)), until: toLocalIso(startOfDay) }),
    '7d': () => ({ since: toLocalIso(back(6)), until: toLocalIso(tomorrow) }),
    '30d': () => ({ since: toLocalIso(back(29)), until: toLocalIso(tomorrow) }),
  }
  return (windows[key] || (() => ({ since: undefined, until: undefined })))()
}

export function useSessionListQuery() {
  const searchInput = ref('')
  const activeSearch = ref('')
  const traceIdInput = ref('')
  const activeTraceId = ref('')
  const searchScope = ref(stored(SCOPE_KEY, SCOPE_OPTIONS, 'title'))
  // Real-only is the historic default; seeded once from the legacy
  // `regin_sessions_show_tests` flag so users who had tests enabled don't
  // lose that preference on upgrade.
  const kind = ref(stored(KIND_KEY, KIND_OPTIONS,
    localStorage.getItem(TEST_TOGGLE_KEY) === '1' ? 'all' : 'real'))
  const activeFilter = ref(stored(ACTIVE_KEY, ACTIVE_OPTIONS, 'all'))
  const range = ref(stored(RANGE_KEY, RANGE_OPTIONS, 'today'))
  // A session carries one intrinsic builtin category tag (user / system /
  // topic-proposal, derived server-side from `origin`) plus any custom tags;
  // '' means "all".
  const tagFilter = ref(localStorage.getItem(TAG_KEY) || '')
  const repoFilter = ref(localStorage.getItem(REPO_KEY) || 'all')

  const cursor = useCursor({
    path: '/sessions',
    size: 50,
    buildQuery: () => {
      const { since, until } = rangeBounds(range.value)
      return {
        // 'real' is the server default; only send when narrowing/widening.
        kind: kind.value !== 'real' ? kind.value : undefined,
        tag: tagFilter.value || undefined,
        active: activeFilter.value !== 'all' ? activeFilter.value : undefined,
        trace_id: activeTraceId.value || undefined,
        q: activeSearch.value || undefined,
        // Only send `scope` when a search term is active — keeps the URL tidy
        // and lets the backend apply its 'title' default unchanged.
        scope: activeSearch.value ? searchScope.value : undefined,
        repo: repoFilter.value !== 'all' ? repoFilter.value : undefined,
        since,
        until,
      }
    },
  })

  const { extras } = cursor
  const { serverClock } = useServerClock(extras)
  const tagCounts = computed(() => extras.value?.tag_counts || {})
  const repoCounts = computed(() => extras.value?.repo_counts || {})
  const totalCount = computed(() => extras.value?.total_count ?? null)
  const activeCount = computed(() => extras.value?.active_count ?? 0)
  const builtinTags = computed(() => extras.value?.builtin_tags || [])

  const filterCount = computed(() => {
    const on = [
      range.value !== DEFAULTS.range,
      kind.value !== DEFAULTS.kind,
      tagFilter.value !== DEFAULTS.tag,
      activeFilter.value !== DEFAULTS.active,
      repoFilter.value !== DEFAULTS.repo,
      Boolean(activeTraceId.value),
    ]
    return on.filter(Boolean).length
  })

  // Set while `resetFilters` assigns several facets in one go, so the five
  // watchers below persist their value but collapse into ONE refetch instead
  // of firing five overlapping requests whose last response wins. It must stay
  // set across an `await nextTick()`: these are default `flush: 'pre'` watchers,
  // so they run as microtasks AFTER the assigning function yields — clearing
  // the flag in a synchronous `finally` would let every one of them through.
  let batching = false

  function persistAndReload(reload) {
    const bindings = [
      [kind, KIND_KEY], [activeFilter, ACTIVE_KEY], [range, RANGE_KEY],
      [tagFilter, TAG_KEY], [repoFilter, REPO_KEY],
    ]
    for (const [source, key] of bindings) {
      watch(source, (v) => {
        localStorage.setItem(key, v ?? '')
        if (!batching) reload()
      })
    }
    watch(searchScope, (v) => {
      localStorage.setItem(SCOPE_KEY, v)
      // Toggling the scope with an empty box doesn't change the result set.
      if (activeSearch.value) reload()
    })
  }

  function commitSearch() {
    activeSearch.value = searchInput.value.trim()
    activeTraceId.value = traceIdInput.value.trim()
  }

  // Reset every narrowing axis at once, then refetch exactly once.
  async function resetFilters(reload) {
    batching = true
    try {
      searchInput.value = ''
      activeSearch.value = ''
      traceIdInput.value = ''
      activeTraceId.value = ''
      kind.value = DEFAULTS.kind
      activeFilter.value = DEFAULTS.active
      range.value = DEFAULTS.range
      tagFilter.value = DEFAULTS.tag
      repoFilter.value = DEFAULTS.repo
      // Let the pre-flush watchers drain while `batching` still suppresses
      // them; only then is it safe to clear the flag.
      await nextTick()
    } finally {
      batching = false
    }
    await reload()
  }

  return {
    ...cursor,
    searchInput, activeSearch, traceIdInput, activeTraceId, searchScope,
    kind, activeFilter, range, tagFilter, repoFilter,
    serverClock, tagCounts, repoCounts, totalCount, activeCount, builtinTags,
    filterCount, persistAndReload, commitSearch, resetFilters,
  }
}
