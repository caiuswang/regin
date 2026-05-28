<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import api from '../api'
import CursorControls from '../components/CursorControls.vue'
import { useConfirm } from '../composables/useConfirm'
import { useFlash } from '../composables/useFlash'
import { useCursor } from '../composables/useCursor'
import { useStickyHeader } from '../composables/useStickyHeader'
import { fmtTokens } from '../utils/traceFormatters.js'

const { confirm } = useConfirm()
const { flash } = useFlash()

const TEST_TOGGLE_KEY = 'regin_sessions_show_tests'  // legacy; migrates into KIND_KEY on first read
const KIND_KEY = 'regin_sessions_kind'
const ACTIVE_KEY = 'regin_sessions_active'
const RANGE_KEY = 'regin_sessions_range'
const SCOPE_KEY = 'regin_sessions_search_scope'
const searchInput = ref('')
const activeSearch = ref('')
const SCOPE_OPTIONS = [
  { value: 'title', label: 'Title' },
  { value: 'prompt', label: 'Prompt' },
  { value: 'both', label: 'Both' },
]
const searchScope = ref(
  SCOPE_OPTIONS.some(o => o.value === localStorage.getItem(SCOPE_KEY))
    ? localStorage.getItem(SCOPE_KEY)
    : 'title'
)

// Kind: real-only is the historic default. We seed it from the legacy
// `regin_sessions_show_tests` flag the first time so users who had tests
// enabled don't lose that preference on upgrade.
const KIND_OPTIONS = [
  { value: 'real', label: 'Real only' },
  { value: 'test', label: 'Test only' },
  { value: 'all', label: 'Real + test' },
]
const kind = ref(
  KIND_OPTIONS.some(o => o.value === localStorage.getItem(KIND_KEY))
    ? localStorage.getItem(KIND_KEY)
    : (localStorage.getItem(TEST_TOGGLE_KEY) === '1' ? 'all' : 'real')
)

const ACTIVE_OPTIONS = [
  { value: 'all', label: 'Any status' },
  { value: 'active', label: 'Active only' },
  { value: 'inactive', label: 'Inactive only' },
]
const activeFilter = ref(
  ACTIVE_OPTIONS.some(o => o.value === localStorage.getItem(ACTIVE_KEY))
    ? localStorage.getItem(ACTIVE_KEY)
    : 'all'
)

// Trace-id prefix filter. Two refs so the box doesn't fire a request on
// every keystroke — mirrors the title/prompt search pattern.
const traceIdInput = ref('')
const activeTraceId = ref('')

// Time-range presets keyed by `last_seen`. Boundaries are computed in the
// browser (user's local clock) and serialized as naive local ISO so the
// lexicographic compare on the server matches the stored text format.
const RANGE_OPTIONS = [
  { value: 'all', label: 'All time' },
  { value: 'today', label: 'Today' },
  { value: 'yesterday', label: 'Yesterday' },
  { value: '7d', label: 'Last 7 days' },
  { value: '30d', label: 'Last 30 days' },
]
const range = ref(localStorage.getItem(RANGE_KEY) || 'today')

// Repo filter. Options come from /api/repos (active repos only); the
// selected value is the unique repo name, persisted across visits. A
// multi-repo session matches every repo it touched.
const REPO_KEY = 'regin_sessions_repo'
const repoFilter = ref(localStorage.getItem(REPO_KEY) || 'all')
const repoOptions = ref([])

async function loadRepoOptions() {
  try {
    const res = await api.get('/repos')
    repoOptions.value = (res.repos || []).map(r => r.name)
    // Drop a stale saved filter if that repo is no longer registered.
    if (repoFilter.value !== 'all' && !repoOptions.value.includes(repoFilter.value)) {
      repoFilter.value = 'all'
    }
  } catch {
    repoOptions.value = []
  }
}

function pad(n) { return String(n).padStart(2, '0') }
function toLocalIso(d) {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}
function rangeBounds(key) {
  const now = new Date()
  const startOfDay = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const tomorrow = new Date(startOfDay); tomorrow.setDate(tomorrow.getDate() + 1)
  switch (key) {
    case 'today':
      return { since: toLocalIso(startOfDay), until: toLocalIso(tomorrow) }
    case 'yesterday': {
      const y = new Date(startOfDay); y.setDate(y.getDate() - 1)
      return { since: toLocalIso(y), until: toLocalIso(startOfDay) }
    }
    case '7d': {
      const s = new Date(startOfDay); s.setDate(s.getDate() - 6)
      return { since: toLocalIso(s), until: toLocalIso(tomorrow) }
    }
    case '30d': {
      const s = new Date(startOfDay); s.setDate(s.getDate() - 29)
      return { since: toLocalIso(s), until: toLocalIso(tomorrow) }
    }
    default:
      return { since: undefined, until: undefined }
  }
}

// Keyset-paginated session list. Load-more appends, filter change resets.
// `items` is the reactive row array — aliased as `sessions` below for
// compatibility with the rest of this view's mental model.
const {
  items: sessions, loading, loadingMore, hasNext,
  load, loadMore,
} = useCursor({
  path: '/sessions',
  size: 50,
  buildQuery: () => {
    const { since, until } = rangeBounds(range.value)
    return {
      // 'real' is the server default; only send when narrowing/widening.
      kind: kind.value !== 'real' ? kind.value : undefined,
      active: activeFilter.value !== 'all' ? activeFilter.value : undefined,
      trace_id: activeTraceId.value || undefined,
      q: activeSearch.value || undefined,
      // Only send `scope` when a search term is active — keeps the URL
      // tidy and lets the backend apply its 'title' default unchanged.
      scope: activeSearch.value ? searchScope.value : undefined,
      repo: repoFilter.value !== 'all' ? repoFilter.value : undefined,
      since,
      until,
    }
  },
})

const { stickyHeaderEl, stickyHeaderHeight } = useStickyHeader(loading)

async function reload() {
  await load()
  // Selection was computed against the previous page set; drop entries
  // that are no longer visible so a later batch-delete can't target rows
  // the user can't currently see.
  const visible = new Set(sessions.value.map(s => s.trace_id))
  const pruned = new Set()
  for (const id of selectedIds.value) if (visible.has(id)) pruned.add(id)
  selectedIds.value = pruned
}

function runSearch() {
  activeSearch.value = searchInput.value.trim()
  activeTraceId.value = traceIdInput.value.trim()
  reload()
}

function clearSearch() {
  searchInput.value = ''
  activeSearch.value = ''
  traceIdInput.value = ''
  activeTraceId.value = ''
  reload()
}

function clearTraceId() {
  traceIdInput.value = ''
  activeTraceId.value = ''
  reload()
}

function titlePreview(title) {
  if (!title) return ''
  const firstLine = title.split('\n')[0].trim()
  return firstLine.length > 70 ? firstLine.slice(0, 70) + '…' : firstLine
}

const deleting = ref(null)  // trace_id currently being deleted
const selectedIds = ref(new Set())  // trace_ids checked for batch delete
const batchDeleting = ref(false)

const selectionCount = computed(() => selectedIds.value.size)

const allSelected = computed(() => {
  if (!sessions.value.length) return false
  return sessions.value.every(s => selectedIds.value.has(s.trace_id))
})

function isSelected(traceId) {
  return selectedIds.value.has(traceId)
}

function toggleOne(traceId, checked) {
  const next = new Set(selectedIds.value)
  if (checked) next.add(traceId)
  else next.delete(traceId)
  selectedIds.value = next
}

function toggleSelectAll(e) {
  selectedIds.value = e.target.checked
    ? new Set(sessions.value.map(s => s.trace_id))
    : new Set()
}

const STALE_FALLBACK_WINDOW_MS = 10 * 60 * 1000

function isActive(s) {
  if (s.status === 'active') return true
  if (s.status === 'ended') return false
  const d = parseLocalIso(s.last_seen)
  if (!d) return false
  const age = Date.now() - d.getTime()
  return age >= 0 && age < STALE_FALLBACK_WINDOW_MS
}

async function deleteSession(s) {
  const label = titlePreview(s.title) || s.trace_id.slice(0, 12) + '...'
  const active = isActive(s)
  const header = active
    ? `⚠️  This session appears to still be ACTIVE (last span ${fmtDuration(Date.now() - parseLocalIso(s.last_seen).getTime())} ago). Deleting now will remove its trace data mid-session; subsequent spans will reappear as a new, partial trace.\n\n`
    : ''
  const msg = `${header}Delete "${label}"? This removes all spans, skill reads, plan sessions, and rule triggers for trace ${s.trace_id.slice(0, 12)}...`
  const ok = await confirm('Delete session', msg, true)
  if (!ok) return
  deleting.value = s.trace_id
  try {
    const res = await api.del(`/sessions/${s.trace_id}`)
    if (res.ok === false) {
      flash(`Delete failed: ${res.msg || 'unknown error'}`, 'error')
      return
    }
    flash(`Deleted session ${s.trace_id.slice(0, 12)}...`)
    await reload()
  } finally {
    deleting.value = null
  }
}

async function batchDelete() {
  const ids = Array.from(selectedIds.value)
  if (!ids.length) return
  const idSet = new Set(ids)
  const activeCount = sessions.value.filter(
    s => idSet.has(s.trace_id) && isActive(s)
  ).length
  const header = activeCount > 0
    ? `⚠️  ${activeCount} of the selected session(s) still appear ACTIVE. Deleting now removes their trace data mid-session; subsequent spans will reappear as new partial traces.\n\n`
    : ''
  const msg = `${header}Delete ${ids.length} session${ids.length === 1 ? '' : 's'}? This removes all spans, skill reads, plan sessions, and rule triggers for every selected trace.`
  const ok = await confirm(`Delete ${ids.length} session${ids.length === 1 ? '' : 's'}`, msg, true)
  if (!ok) return
  batchDeleting.value = true
  try {
    const res = await api.post('/sessions/batch-delete', { trace_ids: ids })
    if (res.ok === false) {
      flash(`Batch delete failed: ${res.msg || 'unknown error'}`, 'error')
      return
    }
    selectedIds.value = new Set()
    flash(`Deleted ${res.processed || ids.length} session${(res.processed || ids.length) === 1 ? '' : 's'}`)
    await reload()
  } finally {
    batchDeleting.value = false
  }
}

onMounted(() => {
  loadRepoOptions()
  reload()
})

watch(repoFilter, (v) => {
  localStorage.setItem(REPO_KEY, v)
  reload()
})

watch(kind, (v) => {
  localStorage.setItem(KIND_KEY, v)
  reload()
})

watch(activeFilter, (v) => {
  localStorage.setItem(ACTIVE_KEY, v)
  reload()
})

watch(range, (v) => {
  localStorage.setItem(RANGE_KEY, v)
  reload()
})

watch(searchScope, (v) => {
  localStorage.setItem(SCOPE_KEY, v)
  // Only re-fetch when a search is active — toggling the scope while
  // the box is empty doesn't change the result set.
  if (activeSearch.value) reload()
})

function parseLocalIso(iso) {
  if (!iso) return null
  const m = iso.match(/^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?/)
  if (!m) return new Date(iso)
  const ms = m[7] ? parseInt(m[7].slice(0, 3).padEnd(3, '0'), 10) : 0
  return new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6], ms)
}

function fmtDate(iso) {
  const d = parseLocalIso(iso)
  if (!d) return '-'
  return d.toLocaleString()
}

function shortTestName(nodeid) {
  if (!nodeid) return ''
  const idx = nodeid.indexOf('::')
  return idx >= 0 ? nodeid.slice(idx + 2) : nodeid
}

function fmtDuration(ms) {
  if (!ms) return '-'
  if (ms < 1000) return `${ms}ms`

  const seconds = Math.floor(ms / 1000) % 60
  const minutes = Math.floor(ms / 60000) % 60
  const hours = Math.floor(ms / 3600000) % 24
  const days = Math.floor(ms / 86400000)

  const units = [
    { value: days, label: 'd' },
    { value: hours, label: 'h' },
    { value: minutes, label: 'm' },
    { value: seconds, label: 's' },
  ]

  const start = units.findIndex(u => u.value > 0)
  if (start === -1) return '-'

  let end = units.length - 1
  while (end > start && units[end].value === 0) {
    end--
  }

  return units.slice(start, end + 1).map(u => `${u.value}${u.label}`).join('')
}

function contextBadgeClass(pct) {
  if (pct == null) return 'bg-gray-100 text-gray-500 border-gray-200'
  if (pct >= 80) return 'bg-red-50 text-red-700 border-red-200'
  if (pct >= 50) return 'bg-amber-50 text-amber-700 border-amber-200'
  return 'bg-green-50 text-green-700 border-green-200'
}

function agentTypeLabel(s) {
  if (s.agent_kind === 'claude') return 'Claude Code session'
  if (s.agent_kind === 'codex') return 'OpenAI Codex session'
  return s.agent_type ? `Agent session: ${s.agent_type}` : 'Agent session'
}

function agentTypeClass(s) {
  if (s.agent_kind === 'claude') return 'agent-icon--claude'
  if (s.agent_kind === 'codex') return 'agent-icon--codex'
  return 'agent-icon--generic'
}

function totalMs(s) {
  const a = parseLocalIso(s.started_at)
  const b = parseLocalIso(s.last_seen)
  if (!a || !b) return 0
  return b.getTime() - a.getTime()
}

// Relative "time ago" for the Last seen column. Absolute started/last-seen
// stay available via the cell's title tooltip (timeTitle).
function fmtRelative(iso) {
  const d = parseLocalIso(iso)
  if (!d) return '-'
  const diff = Date.now() - d.getTime()
  if (diff < 0) return 'just now'
  const sec = Math.floor(diff / 1000)
  if (sec < 60) return `${sec}s ago`
  const min = Math.floor(sec / 60)
  if (min < 60) return `${min}m ago`
  const hr = Math.floor(min / 60)
  if (hr < 24) return `${hr}h ago`
  const day = Math.floor(hr / 24)
  if (day < 30) return `${day}d ago`
  const mo = Math.floor(day / 30)
  if (mo < 12) return `${mo}mo ago`
  return `${Math.floor(mo / 12)}y ago`
}

function timeTitle(s) {
  return `Started ${fmtDate(s.started_at)}\nLast seen ${fmtDate(s.last_seen)}`
}

// The 7 per-session counts collapse to one Activity cell: Spans + Edits stay
// visible; the rest fold behind a "+N more" hint whose tooltip enumerates the
// non-zero ones.
const FOLDED_METRICS = [
  { key: 'tool_calls', label: 'tools' },
  { key: 'skill_reads', label: 'reads' },
  { key: 'rule_checks', label: 'rules' },
  { key: 'plans', label: 'plans' },
  { key: 'prompts', label: 'prompts' },
]

function foldedNonzero(s) {
  return FOLDED_METRICS.filter(m => (s[m.key] || 0) > 0)
}

function activityMoreLabel(s) {
  const n = foldedNonzero(s).length
  return n ? `+${n} more` : ''
}

function activityMoreTitle(s) {
  const parts = foldedNonzero(s).map(m => `${s[m.key]} ${m.label}`)
  return parts.length ? parts.join(' · ') : 'no other activity'
}
</script>

<template>
  <div v-if="loading" class="empty-state">Loading sessions…</div>
  <div
    v-else
    class="sticky-page-root"
    :style="{ '--regin-trace-header-h': stickyHeaderHeight ? stickyHeaderHeight + 'px' : '0px' }"
  >
    <!-- Sticky page header: subtitle + search/toolbar pin to the top of
         `.content-scroll` so search/filter state stays visible while
         scrolling the long session table below. The table's <thead>
         can't be made page-sticky here because the wide table needs its
         own horizontal-scroll container — that nested overflow traps the
         vertical sticky. Keeping the toolbar sticky still solves the
         primary nav problem. -->
    <div
      ref="stickyHeaderEl"
      class="sticky -top-4 lg:-top-6 z-20 bg-white -mx-4 -mt-4 px-4 pt-4 lg:-mx-8 lg:-mt-6 lg:px-8 lg:pt-6 pb-3 mb-4 border-b border-slate-200 shadow-[0_2px_4px_-2px_rgba(15,23,42,0.06)]"
    >
    <p class="page-subtitle mb-4">Unified telemetry of skill reads, file edits, rule checks, and plan mode entries per Claude Code session.</p>

    <form class="session-filters" @submit.prevent="runSearch">
      <!-- Row 1: query input + scope + action buttons. Pressing Enter
           anywhere in this form submits both the search term and the
           trace-id (whichever was last edited). -->
      <div class="session-filters__row">
        <div class="search-group">
          <input
            v-model="searchInput"
            type="search"
            placeholder="Search sessions…"
            class="input search-input focus-visible:outline-2 focus-visible:outline-blue-500"
            aria-label="Search sessions"
          >
          <select
            v-model="searchScope"
            class="input search-scope focus-visible:outline-2 focus-visible:outline-blue-500"
            aria-label="What to search"
            title="What `Search` matches against"
          >
            <option v-for="opt in SCOPE_OPTIONS" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
          </select>
        </div>
        <button
          type="submit"
          class="btn btn-primary focus-visible:outline-2 focus-visible:outline-blue-500"
        >Search</button>
        <button
          v-if="activeSearch || activeTraceId"
          type="button"
          class="btn btn-secondary focus-visible:outline-2 focus-visible:outline-blue-500"
          @click="clearSearch"
        >Clear</button>
        <span v-if="activeSearch" class="text-xs text-slate-500">
          {{ searchScope }}: <code class="cell-code">{{ activeSearch }}</code>
        </span>

        <button
          v-if="selectionCount"
          type="button"
          class="btn btn-danger ml-auto focus-visible:outline-2 focus-visible:outline-blue-500"
          :disabled="batchDeleting"
          @click="batchDelete"
        >{{ batchDeleting ? 'Deleting…' : `Delete selected (${selectionCount})` }}</button>
      </div>

      <!-- Row 2: faceted filters as labeled pill dropdowns. The trace-id
           pill is also here — it carries an input rather than a select
           and shows a clear (×) when a value is committed. -->
      <div class="session-filters__row session-filters__row--facets">
        <span class="facets-label">Filters</span>

        <label class="facet-pill" :class="{ 'facet-pill--active': range !== 'today' }">
          <span class="facet-pill__label">Range</span>
          <select
            v-model="range"
            class="facet-pill__select focus-visible:outline-2 focus-visible:outline-blue-500"
            aria-label="Filter by last activity time range"
          >
            <option v-for="opt in RANGE_OPTIONS" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
          </select>
        </label>

        <label class="facet-pill" :class="{ 'facet-pill--active': kind !== 'real' }">
          <span class="facet-pill__label">Kind</span>
          <select
            v-model="kind"
            class="facet-pill__select focus-visible:outline-2 focus-visible:outline-blue-500"
            aria-label="Filter by session kind (real vs test)"
          >
            <option v-for="opt in KIND_OPTIONS" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
          </select>
        </label>

        <label class="facet-pill" :class="{ 'facet-pill--active': activeFilter !== 'all' }">
          <span class="facet-pill__label">Status</span>
          <select
            v-model="activeFilter"
            class="facet-pill__select focus-visible:outline-2 focus-visible:outline-blue-500"
            aria-label="Filter by active status"
          >
            <option v-for="opt in ACTIVE_OPTIONS" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
          </select>
        </label>

        <label class="facet-pill" :class="{ 'facet-pill--active': repoFilter !== 'all' }">
          <span class="facet-pill__label">Repo</span>
          <select
            v-model="repoFilter"
            class="facet-pill__select focus-visible:outline-2 focus-visible:outline-blue-500"
            aria-label="Filter by repo"
          >
            <option value="all">All repos</option>
            <option v-for="name in repoOptions" :key="name" :value="name">{{ name }}</option>
          </select>
        </label>

        <div class="facet-pill facet-pill--input" :class="{ 'facet-pill--active': activeTraceId }">
          <span class="facet-pill__label">Trace ID</span>
          <input
            v-model="traceIdInput"
            type="search"
            placeholder="prefix…"
            class="facet-pill__input focus-visible:outline-2 focus-visible:outline-blue-500"
            aria-label="Filter by trace id prefix"
            title="Case-insensitive prefix match on trace_id (press Enter to apply)"
          >
          <button
            v-if="activeTraceId"
            type="button"
            class="facet-pill__clear focus-visible:outline-2 focus-visible:outline-blue-500"
            aria-label="Clear trace id filter"
            title="Clear trace id filter"
            @click="clearTraceId"
          >×</button>
        </div>
      </div>
    </form>
    </div>
    <!-- /sticky page header -->

    <div class="split-card">
      <div v-if="sessions.length" class="hidden sm:block overflow-x-auto">
        <table class="tbl sessions-tbl">
          <thead>
            <tr>
              <th class="w-6">
                <input
                  type="checkbox"
                  class="h-4 w-4 align-middle focus-visible:outline-2 focus-visible:outline-blue-500"
                  :checked="allSelected"
                  :indeterminate.prop="selectionCount > 0 && !allSelected"
                  @change="toggleSelectAll"
                  title="Select all"
                  aria-label="Select all sessions"
                />
              </th>
              <th>Session</th>
              <th class="col-title">Title</th>
              <th>Repo</th>
              <th title="Spans and file edits this session; hover the +N hint for reads, rules, plans, prompts, and tools">Activity</th>
              <th>Context</th>
              <th title="Total wall-clock time / active agent work time (user-idle gaps excluded)">Elapsed / Active</th>
              <th>Last seen</th>
              <th class="text-right"><span class="sr-only">Actions</span></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="s in sessions" :key="s.trace_id" :class="{ 'tbl-row-active': isSelected(s.trace_id) }">
              <td class="w-6" @click.stop>
                <input
                  type="checkbox"
                  class="h-4 w-4 align-middle focus-visible:outline-2 focus-visible:outline-blue-500"
                  :checked="isSelected(s.trace_id)"
                  @change="toggleOne(s.trace_id, $event.target.checked)"
                  :aria-label="`Select session ${s.trace_id.slice(0, 8)}`"
                />
              </td>
              <td class="whitespace-nowrap">
                <div class="inline-flex items-center gap-2 align-middle">
                  <span
                    class="agent-icon"
                    :class="agentTypeClass(s)"
                    :title="agentTypeLabel(s)"
                    :aria-label="agentTypeLabel(s)"
                    role="img"
                  >
                    <svg v-if="s.agent_kind === 'claude'" viewBox="0 0 16 16" aria-hidden="true">
                      <path d="M8 2.2 9.5 6.5 13.8 8 9.5 9.5 8 13.8 6.5 9.5 2.2 8 6.5 6.5 8 2.2Z" />
                    </svg>
                    <svg v-else-if="s.agent_kind === 'codex'" viewBox="0 0 16 16" aria-hidden="true">
                      <path d="M3 3.5h10a1 1 0 0 1 1 1v7a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1v-7a1 1 0 0 1 1-1Zm2.2 3L7 8 5.2 9.5M8.2 10h3" />
                    </svg>
                    <svg v-else viewBox="0 0 16 16" aria-hidden="true">
                      <path d="M8 2.5a5.5 5.5 0 1 1 0 11 5.5 5.5 0 0 1 0-11Zm0 2.2v3.1l2.2 2.1" />
                    </svg>
                  </span>
                  <router-link :to="`/trace/sessions/${s.trace_id}`" class="table-link">
                    <code class="cell-code">{{ s.trace_id.slice(0, 12) }}…</code>
                  </router-link>
                </div>
                <span
                  v-if="isActive(s)"
                  class="ml-2 inline-flex items-center gap-1 rounded bg-green-100 text-green-800 text-[10px] font-semibold px-1.5 py-0.5 uppercase tracking-wide"
                  :title="s.status === 'active' ? 'SessionStart fired without a matching SessionEnd' : 'No explicit lifecycle — last span within 10 minutes'"
                >
                  <span class="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse"></span>
                  active
                </span>
                <span
                  v-else-if="s.status === 'ended'"
                  class="ml-2 inline-block rounded bg-gray-100 text-gray-600 text-[10px] font-semibold px-1.5 py-0.5 uppercase tracking-wide"
                  :title="s.ended_reason ? `reason: ${s.ended_reason}` : 'SessionEnd fired'"
                >
                  ended
                </span>
                <template v-if="s.is_test">
                  <span
                    class="ml-2 inline-block rounded bg-amber-100 text-amber-800 text-[10px] font-semibold px-1.5 py-0.5 uppercase tracking-wide"
                    title="Span attributes carry is_test=true"
                  >test</span>
                  <span
                    v-if="s.test_name"
                    class="ml-1 text-xs text-gray-600 font-mono"
                    :title="s.test_name"
                  >{{ shortTestName(s.test_name) }}</span>
                </template>
              </td>
              <td class="col-title">
                <span v-if="s.title" class="block truncate text-sm text-gray-800" :title="s.title">{{ titlePreview(s.title) }}</span>
                <span v-else class="text-gray-400 italic text-xs">no prompt</span>
              </td>
              <td class="whitespace-nowrap">
                <template v-if="s.repos && s.repos.length">
                  <span
                    class="inline-flex items-center rounded-md bg-slate-100 text-slate-600 text-[11px] px-2 py-0.5"
                    :title="s.cwd || (s.primary_repo || s.repos[0].name)"
                  >{{ s.primary_repo || s.repos[0].name }}</span>
                  <span
                    v-if="s.is_multi_repo"
                    class="ml-1 text-[11px] text-slate-400"
                    :title="`Also touched: ${s.repos.filter(r => !r.is_primary).map(r => r.name).join(', ')}`"
                  >+{{ s.repos.length - 1 }}</span>
                </template>
                <span v-else class="text-gray-300 text-xs" title="No registered repo matched">-</span>
              </td>
              <td class="whitespace-nowrap">
                <div class="text-[13px] text-slate-800">
                  {{ s.span_count }} <span class="text-slate-400">spans</span>
                  <span class="text-slate-300 px-0.5">·</span>
                  {{ s.file_edits }} <span class="text-slate-400">edits</span>
                </div>
                <div
                  v-if="activityMoreLabel(s)"
                  class="mt-0.5 text-[11px] text-slate-400 cursor-help"
                  :title="activityMoreTitle(s)"
                >{{ activityMoreLabel(s) }}</div>
              </td>
              <td class="whitespace-nowrap">
                <span
                  v-if="s.context_pct != null"
                  class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded border text-[11px] font-medium"
                  :class="contextBadgeClass(s.context_pct)"
                  :title="`peak ${fmtTokens(s.peak_context_tokens)} / ${fmtTokens(s.context_window_tokens)} tokens`"
                >{{ s.context_pct }}%
                  <span class="text-[10px] opacity-70">({{ fmtTokens(s.peak_context_tokens) }})</span>
                </span>
                <span v-else class="text-gray-300 text-xs">-</span>
              </td>
              <td class="text-gray-500 text-xs whitespace-nowrap">
                {{ fmtDuration(totalMs(s)) }}
                <template v-if="s.active_work_ms != null">
                  <span class="text-gray-400">/</span>
                  <span :title="`agent work time${s.active_pct != null ? ` (${s.active_pct}%)` : ''}, idle ${fmtDuration(s.idle_ms)} excluded`">{{ fmtDuration(s.active_work_ms) }}</span>
                </template>
              </td>
              <td class="text-gray-400 text-xs whitespace-nowrap" :title="timeTitle(s)">{{ fmtRelative(s.last_seen) }}</td>
              <td class="text-right">
                <button
                  type="button"
                  class="row-delete text-xs text-red-600 hover:text-red-800 hover:underline disabled:opacity-50 disabled:cursor-wait focus-visible:outline-2 focus-visible:outline-blue-500"
                  :disabled="deleting === s.trace_id"
                  @click="deleteSession(s)"
                  :title="`Delete session ${s.trace_id.slice(0, 12)}… and all its trace data`"
                >{{ deleting === s.trace_id ? 'Deleting…' : 'Delete' }}</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <ul v-if="sessions.length" class="sm:hidden flex flex-col divide-y divide-gray-200">
        <li v-for="s in sessions" :key="s.trace_id" class="p-3 text-sm" :class="{ 'bg-blue-50': isSelected(s.trace_id) }">
          <div class="flex items-start gap-2">
            <input
              type="checkbox"
              class="h-4 w-4 mt-1 shrink-0"
              :checked="isSelected(s.trace_id)"
              @change="toggleOne(s.trace_id, $event.target.checked)"
              :aria-label="`Select session ${s.trace_id.slice(0, 8)}`"
            />
            <div class="flex-1 min-w-0">
              <div class="flex flex-wrap items-center gap-2">
                <span
                  class="agent-icon"
                  :class="agentTypeClass(s)"
                  :title="agentTypeLabel(s)"
                  :aria-label="agentTypeLabel(s)"
                  role="img"
                >
                  <svg v-if="s.agent_kind === 'claude'" viewBox="0 0 16 16" aria-hidden="true">
                    <path d="M8 2.2 9.5 6.5 13.8 8 9.5 9.5 8 13.8 6.5 9.5 2.2 8 6.5 6.5 8 2.2Z" />
                  </svg>
                  <svg v-else-if="s.agent_kind === 'codex'" viewBox="0 0 16 16" aria-hidden="true">
                    <path d="M3 3.5h10a1 1 0 0 1 1 1v7a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1v-7a1 1 0 0 1 1-1Zm2.2 3L7 8 5.2 9.5M8.2 10h3" />
                  </svg>
                  <svg v-else viewBox="0 0 16 16" aria-hidden="true">
                    <path d="M8 2.5a5.5 5.5 0 1 1 0 11 5.5 5.5 0 0 1 0-11Zm0 2.2v3.1l2.2 2.1" />
                  </svg>
                </span>
                <router-link :to="`/trace/sessions/${s.trace_id}`" class="text-blue-600 hover:underline font-medium">
                  <code class="text-xs">{{ s.trace_id.slice(0, 12) }}…</code>
                </router-link>
                <span
                  v-if="isActive(s)"
                  class="inline-flex items-center gap-1 rounded bg-green-100 text-green-800 text-[10px] font-semibold px-1.5 py-0.5 uppercase tracking-wide"
                >
                  <span class="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse"></span>active
                </span>
                <span
                  v-else-if="s.status === 'ended'"
                  class="inline-block rounded bg-gray-100 text-gray-600 text-[10px] font-semibold px-1.5 py-0.5 uppercase tracking-wide"
                >ended</span>
                <span
                  v-if="s.is_test"
                  class="inline-block rounded bg-amber-100 text-amber-800 text-[10px] font-semibold px-1.5 py-0.5 uppercase tracking-wide"
                >test</span>
                <span
                  v-if="s.context_pct != null"
                  class="ml-auto inline-flex items-center gap-1 px-1.5 py-0.5 rounded border text-[11px] font-medium"
                  :class="contextBadgeClass(s.context_pct)"
                  :title="`main-conversation peak ${s.peak_main_context_tokens || s.peak_context_tokens} / ${s.context_window_tokens} tokens`"
                >ctx {{ s.context_pct }}%</span>
                <!-- All-inclusive peak (with advisor/sub-call tokens
                     rolled in). Only shown when it diverges from main
                     by more than 1% so the normal case stays uncluttered. -->
                <span
                  v-if="s.context_pct != null && s.context_pct_all != null
                        && (s.context_pct_all - s.context_pct) > 1"
                  class="inline-flex items-center px-1.5 py-0.5 rounded border text-[10px] font-medium border-slate-200 bg-slate-50 text-slate-500"
                  :title="`includes advisor/sub-call tokens — peak ${s.peak_context_tokens} / ${s.context_window_tokens}`"
                >+sub {{ s.context_pct_all }}%</span>
              </div>
              <div class="mt-1 text-gray-700 break-words">
                <span v-if="s.title">{{ titlePreview(s.title) }}</span>
                <span v-else class="text-gray-400 italic text-xs">no prompt</span>
              </div>
              <dl class="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-xs text-gray-600">
                <div class="flex justify-between"><dt class="text-gray-400">Spans</dt><dd>{{ s.span_count }}</dd></div>
                <div class="flex justify-between"><dt class="text-gray-400">Edits</dt><dd>{{ s.file_edits }}</dd></div>
                <div class="flex justify-between"><dt class="text-gray-400">Tools</dt><dd>{{ s.tool_calls }}</dd></div>
                <div class="flex justify-between"><dt class="text-gray-400">Reads</dt><dd>{{ s.skill_reads }}</dd></div>
                <div class="flex justify-between"><dt class="text-gray-400">Duration</dt><dd>{{ fmtDuration(totalMs(s)) }}</dd></div>
                <div class="flex justify-between"><dt class="text-gray-400">Last seen</dt><dd :title="timeTitle(s)">{{ fmtRelative(s.last_seen) }}</dd></div>
                <div class="flex justify-between col-span-2">
                  <dt class="text-gray-400">Repo</dt>
                  <dd v-if="s.repos && s.repos.length" :title="s.repos.map(r => r.name).join(', ')">
                    {{ s.primary_repo || s.repos[0].name }}<span v-if="s.is_multi_repo" class="text-gray-400"> +{{ s.repos.length - 1 }}</span>
                  </dd>
                  <dd v-else class="text-gray-300">-</dd>
                </div>
              </dl>
              <div class="mt-2 flex justify-end">
                <button
                  type="button"
                  class="text-xs text-red-600 hover:text-red-800 hover:underline disabled:opacity-50 disabled:cursor-wait focus-visible:outline-2 focus-visible:outline-blue-500"
                  :disabled="deleting === s.trace_id"
                  @click="deleteSession(s)"
                >{{ deleting === s.trace_id ? 'Deleting…' : 'Delete' }}</button>
              </div>
            </div>
          </div>
        </li>
      </ul>
      <p v-if="!sessions.length && activeSearch" class="p-4 text-sm text-gray-400">
        No sessions match {{ searchScope }} <code>{{ activeSearch }}</code>.
      </p>
      <p v-else-if="!sessions.length" class="p-4 text-sm text-gray-400">No session traces yet. Install the File Edit Trace hook in Settings and start a Claude Code session.</p>

      <CursorControls
        v-if="sessions.length"
        :count="sessions.length"
        :has-next="hasNext"
        :loading-more="loadingMore"
        label="sessions"
        @load-more="loadMore"
      />
    </div>
  </div>
</template>

<style scoped>
.split-card {
  background: #fff;
  border-radius: 0.5rem;
  border: 1px solid #e5e7eb;
  margin-bottom: 1rem;
  overflow: hidden;
}

/* Single-table session list. Title is the lead column and truncates to a
 * fixed cap so the metric columns to its right stay aligned. Slightly tighter
 * horizontal padding than the global .tbl keeps all 9 columns on screen
 * without a horizontal scrollbar at common laptop widths. */
.sessions-tbl th,
.sessions-tbl td {
  padding-left: 0.625rem;
  padding-right: 0.625rem;
}
.sessions-tbl .col-title {
  max-width: 16rem;
  min-width: 11rem;
}

/* Delete stays out of the way until the row is hovered or keyboard focus
 * lands inside it, so it never competes with the row content. */
.sessions-tbl .row-delete {
  opacity: 0;
  transition: opacity 0.12s ease;
}
.sessions-tbl tbody tr:hover .row-delete,
.sessions-tbl tbody tr:focus-within .row-delete {
  opacity: 1;
}
@media (hover: none) {
  /* Touch / non-hover pointers can't reveal on hover — keep it visible. */
  .sessions-tbl .row-delete { opacity: 1; }
}
.agent-icon {
  align-items: center;
  border: 1px solid currentColor;
  border-radius: 9999px;
  display: inline-flex;
  flex: 0 0 auto;
  height: 1.25rem;
  justify-content: center;
  width: 1.25rem;
}
.agent-icon svg {
  fill: none;
  height: 0.875rem;
  stroke: currentColor;
  stroke-linecap: round;
  stroke-linejoin: round;
  stroke-width: 1.6;
  width: 0.875rem;
}
.agent-icon--claude {
  background: #fff7ed;
  color: #c2410c;
}
.agent-icon--codex {
  background: #eef2ff;
  color: #4338ca;
}
.agent-icon--generic {
  background: #f3f4f6;
  color: #4b5563;
}

/* Visually-joined input + scope selector. Pulling the select tight
 * against the input reads as one control, which lets us drop the second
 * search box without losing the ability to choose what's being matched. */
.search-group {
  display: inline-flex;
  align-items: stretch;
}
.search-group .search-input {
  width: 22rem;
  border-top-right-radius: 0;
  border-bottom-right-radius: 0;
  border-right: 0;
}
.search-group .search-scope {
  border-top-left-radius: 0;
  border-bottom-left-radius: 0;
  border-left: 1px solid #cbd5e1;
  background: #f8fafc;
  color: #475569;
  font-size: 0.8125rem;
  padding-left: 0.5rem;
  padding-right: 1.75rem;
}
/* Two-tier filter bar: a search row on top and a faceted filter row
 * below. Each row wraps independently so narrow viewports never push
 * facets out of view. */
.session-filters {
  display: flex;
  flex-direction: column;
  gap: 0.625rem;
}
.session-filters__row {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-wrap: wrap;
}
.session-filters__row--facets {
  /* Tighter gap on facet row so labels sit close to their dropdowns. */
  gap: 0.375rem;
}

.facets-label {
  font-size: 0.6875rem;
  font-weight: 600;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: #64748b;
  margin-right: 0.125rem;
}

/* Pill control: a labeled prefix + a control (select or input). The
 * label/control share the same rounded rectangle so they read as one
 * unit rather than two adjacent widgets. */
.facet-pill {
  display: inline-flex;
  align-items: stretch;
  border: 1px solid #e2e8f0;
  border-radius: 0.5rem;
  background: #f8fafc;
  overflow: hidden;
  transition: border-color 0.15s ease, background 0.15s ease;
  cursor: pointer;
}
.facet-pill:hover { border-color: #cbd5e1; }
.facet-pill:focus-within {
  border-color: #3b82f6;
  background: #ffffff;
}
.facet-pill--active {
  border-color: #2563eb;
  background: #eff6ff;
}
.facet-pill--active .facet-pill__label {
  color: #1d4ed8;
  background: rgba(37, 99, 235, 0.08);
  border-right-color: rgba(37, 99, 235, 0.2);
}

.facet-pill__label {
  display: inline-flex;
  align-items: center;
  padding: 0.25rem 0.625rem;
  font-size: 0.6875rem;
  font-weight: 600;
  letter-spacing: 0.02em;
  text-transform: uppercase;
  color: #64748b;
  border-right: 1px solid #e2e8f0;
  user-select: none;
}

.facet-pill__select {
  background: transparent;
  border: 0;
  outline: 0;
  padding: 0.25rem 1.5rem 0.25rem 0.625rem;
  font-size: 0.8125rem;
  font-weight: 500;
  color: #1e293b;
  cursor: pointer;
  /* Restore native chevron — clipped by border-radius otherwise. */
  appearance: auto;
  -webkit-appearance: auto;
}

.facet-pill--input { padding-right: 0.125rem; }
.facet-pill__input {
  background: transparent;
  border: 0;
  outline: 0;
  padding: 0.25rem 0.625rem;
  font-size: 0.8125rem;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  color: #1e293b;
  width: 11rem;
}
.facet-pill__input::placeholder { color: #94a3b8; font-family: inherit; }
.facet-pill__clear {
  background: transparent;
  border: 0;
  color: #64748b;
  cursor: pointer;
  font-size: 1.125rem;
  line-height: 1;
  padding: 0 0.5rem;
  display: inline-flex;
  align-items: center;
  border-radius: 0.375rem;
}
.facet-pill__clear:hover { color: #1e293b; background: rgba(15, 23, 42, 0.06); }
</style>
