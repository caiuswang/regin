import { computed, nextTick, ref, watch } from 'vue'
import { dayFromIso, dayToIso, daySerial, formatSpan } from '../utils/dateRange.js'
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
const CUSTOM_KEY = 'regin_sessions_custom_span'
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

// Not a preset: `range === CUSTOM_RANGE` means the bounds come from the
// calendar's `customStart`/`customEnd` instead of a rolling window.
export const CUSTOM_RANGE = 'custom'

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

// A span with no end yet is OPEN-ENDED, not a single day — that is what the
// "From Jul 3" label promises, and capping it at the 4th would silently hide
// everything since. `until` is exclusive, so a span ending on the 9th must run
// to the 10th's midnight or the whole of the 9th falls outside it.
function customBounds(start, end) {
  if (!start) return { since: undefined, until: undefined }
  return {
    since: toLocalIso(new Date(start.y, start.m, start.d)),
    until: end ? toLocalIso(new Date(end.y, end.m, end.d + 1)) : undefined,
  }
}

// An end with no start can't be filtered on, and leaving it loaded would paint
// a selected day in the calendar that nothing is filtering by.
function storedSpan() {
  const [a, b] = (localStorage.getItem(CUSTOM_KEY) || '').split('|')
  const start = dayFromIso(a)
  return { start, end: start ? dayFromIso(b) : null }
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
  const span = storedSpan()
  const customStart = ref(span.start)
  const customEnd = ref(span.end)
  // `custom` is absent from RANGE_OPTIONS, so `stored()` would demote a saved
  // custom span back to Today — restore it only when its start survived.
  const range = ref(localStorage.getItem(RANGE_KEY) === CUSTOM_RANGE && customStart.value
    ? CUSTOM_RANGE
    : stored(RANGE_KEY, RANGE_OPTIONS, 'today'))
  // A session carries one intrinsic builtin category tag (user / system /
  // topic-proposal, derived server-side from `origin`) plus any custom tags;
  // '' means "all".
  const tagFilter = ref(localStorage.getItem(TAG_KEY) || '')
  const repoFilter = ref(localStorage.getItem(REPO_KEY) || 'all')

  const cursor = useCursor({
    path: '/sessions',
    size: 50,
    buildQuery: () => {
      const { since, until } = range.value === CUSTOM_RANGE
        ? customBounds(customStart.value, customEnd.value)
        : rangeBounds(range.value)
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

  const rangeLabel = computed(() => {
    if (range.value === CUSTOM_RANGE) {
      return formatSpan(customStart.value, customEnd.value) || 'Custom range'
    }
    return (RANGE_OPTIONS.find(o => o.value === range.value) || RANGE_OPTIONS[0]).label
  })

  // Slug → display label for the tag facet, supplied by the view (which is
  // where the builtin/custom tag options are assembled) so a chip can read
  // "Tag: #linear" rather than the bare slug.
  const tagLabels = ref({})

  const FILTER_LABELS = {
    kind: { test: 'Tests only', all: 'Real + tests' },
    status: { active: 'Active', inactive: 'Ended' },
  }

  // One entry per narrowing axis that is off its default — same six axes
  // `filterCount` counts, so the chip row and the Filters badge can never
  // disagree about how narrowed the list is.
  const activeFilters = computed(() => {
    const out = []
    if (range.value !== DEFAULTS.range) out.push({ key: 'range', label: rangeLabel.value })
    if (kind.value !== DEFAULTS.kind) out.push({ key: 'kind', label: FILTER_LABELS.kind[kind.value] })
    if (activeFilter.value !== DEFAULTS.active) {
      out.push({ key: 'status', label: FILTER_LABELS.status[activeFilter.value] })
    }
    if (tagFilter.value !== DEFAULTS.tag) {
      out.push({ key: 'tag', label: `Tag: ${tagLabels.value[tagFilter.value] || tagFilter.value}` })
    }
    if (repoFilter.value !== DEFAULTS.repo) out.push({ key: 'repo', label: `Repo: ${repoFilter.value}` })
    if (activeTraceId.value) out.push({ key: 'trace', label: `Trace: ${activeTraceId.value}` })
    return out
  })

  // Set while a mutator assigns several facets in one go, so the watchers
  // below persist their value but collapse into ONE refetch instead of firing
  // several overlapping requests whose last response wins. It must stay set
  // across an `await nextTick()`: these are default `flush: 'pre'` watchers, so
  // they run as microtasks AFTER the assigning function yields — clearing the
  // flag in a synchronous `finally` would let every one of them through.
  let batching = false
  let reloadFn = async () => {}

  async function batched(mutate, reload = reloadFn) {
    batching = true
    try {
      mutate()
      await nextTick()
    } finally {
      batching = false
    }
    await reload()
  }

  function persistAndReload(reload) {
    reloadFn = reload
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
    // Persist-only: every span mutation already refetches through `batched`,
    // and the second click of a span leaves `range` at 'custom' — so a reload
    // here would be a duplicate on one path and absent on the other.
    watch([customStart, customEnd], () => {
      const text = `${dayToIso(customStart.value)}|${dayToIso(customEnd.value)}`
      localStorage.setItem(CUSTOM_KEY, text === '|' ? '' : text)
    })
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

  function clearSpan() {
    customStart.value = null
    customEnd.value = null
  }

  function selectRangePreset(value) {
    return batched(() => { clearSpan(); range.value = value })
  }

  // First click opens a span, second closes it — in either direction, so
  // picking the 9th then the 3rd yields the same span as the reverse.
  function pickRangeDay(day) {
    const opening = !customStart.value || Boolean(customEnd.value)
    return batched(() => {
      if (opening) {
        customStart.value = day
        customEnd.value = null
      } else {
        const forward = daySerial(customStart.value) <= daySerial(day)
        customEnd.value = forward ? day : customStart.value
        customStart.value = forward ? customStart.value : day
      }
      range.value = CUSTOM_RANGE
    })
  }

  const CLEARERS = {
    range: () => { clearSpan(); range.value = DEFAULTS.range },
    kind: () => { kind.value = DEFAULTS.kind },
    status: () => { activeFilter.value = DEFAULTS.active },
    tag: () => { tagFilter.value = DEFAULTS.tag },
    repo: () => { repoFilter.value = DEFAULTS.repo },
    trace: () => { traceIdInput.value = ''; activeTraceId.value = '' },
  }

  function clearFilter(key, reload) {
    const clear = CLEARERS[key]
    return clear ? batched(clear, reload ?? reloadFn) : Promise.resolve()
  }

  // Reset every narrowing axis at once, then refetch exactly once.
  function resetFilters(reload) {
    return batched(() => {
      searchInput.value = ''
      activeSearch.value = ''
      traceIdInput.value = ''
      activeTraceId.value = ''
      kind.value = DEFAULTS.kind
      activeFilter.value = DEFAULTS.active
      range.value = DEFAULTS.range
      clearSpan()
      tagFilter.value = DEFAULTS.tag
      repoFilter.value = DEFAULTS.repo
    }, reload ?? reloadFn)
  }

  return {
    ...cursor,
    searchInput, activeSearch, traceIdInput, activeTraceId, searchScope,
    kind, activeFilter, range, tagFilter, repoFilter,
    customStart, customEnd, rangeLabel, tagLabels, activeFilters,
    serverClock, tagCounts, repoCounts, totalCount, activeCount, builtinTags,
    filterCount, persistAndReload, commitSearch, resetFilters,
    selectRangePreset, pickRangeDay, clearFilter,
  }
}
