<script setup>
import { ref, computed, onBeforeUnmount, onMounted, watch } from 'vue'
import api from '../api'
import CursorControls from '../components/CursorControls.vue'
import SessionActiveFilters from '../components/sessions/SessionActiveFilters.vue'
import SessionCard from '../components/sessions/SessionCard.vue'
import SessionListRow from '../components/sessions/SessionListRow.vue'
import SessionToolbar from '../components/sessions/SessionToolbar.vue'
import Checkbox from '../components/ui/Checkbox.vue'
import { useConfirm } from '../composables/useConfirm'
import { useFlash } from '../composables/useFlash'
import { useSessionGrouping } from '../composables/useSessionGrouping'
import { useSessionTags } from '../composables/useSessionTags'
import {
  useSessionListQuery,
  ACTIVE_OPTIONS, KIND_OPTIONS, RANGE_OPTIONS, SCOPE_OPTIONS,
} from '../composables/useSessionListQuery'
import { useStickyHeader } from '../composables/useStickyHeader'
import { useTraceHeaderPublisher } from '../composables/useTraceHeader'
import { isActiveWithClock } from '../utils/sessionActivity.js'
import { titlePreview } from '../utils/sessionRowFormat.js'
import { shortTraceId } from '../utils/traceFormatters.js'

const { confirm } = useConfirm()
const { flash } = useFlash()

const query = useSessionListQuery()
const {
  items: sessions, loading, loadingMore, hasNext, load, loadMore,
  serverClock, tagCounts, repoCounts, totalCount, activeCount, builtinTags,
  filterCount, searchInput, activeSearch, traceIdInput, searchScope,
  kind, activeFilter, range, tagFilter, repoFilter,
  customStart, customEnd, rangeLabel, activeFilters,
} = query

const { customTags, loadCustomTags, patchRowTags, addTag, removeTag } = useSessionTags()
const repoOptions = ref([])

const isActive = (s) => isActiveWithClock(s, serverClock.value)
const { mode: groupMode, groups } = useSessionGrouping(sessions, isActive)

// Grouping partitions the rows ALREADY LOADED, while the header pill and the
// footer total come from the server over the whole filter set. Once the list
// is truncated those disagree — "92 active now" above "ACTIVE NOW · 14" reads
// as a bug unless the group says which of the two it is counting. The one
// group whose server-side total we actually know is the active one, so name
// it; the rest are disclosed by the footer note below.
function groupCount(group) {
  if (group.key === 'active' && activeCount.value > group.rows.length) {
    return `${group.rows.length} of ${activeCount.value}`
  }
  return String(group.rows.length)
}

const footerNote = computed(() => (hasNext.value && groupMode.value !== 'flat'
  ? 'keyset-paginated, 50 per page · groups cover the loaded rows'
  : 'keyset-paginated, 50 per page'))

// Facet options: each builtin category, then each custom tag — every entry
// carrying its count for the current filter set.
const tagOptions = computed(() => {
  const opts = builtinTags.value.map(t => ({
    value: t.slug, label: t.label, count: tagCounts.value[t.slug] || 0,
  }))
  // `t.count` from /session-tags is a GLOBAL total; mixing it in as a fallback
  // would let a tag with no sessions in the current window advertise its
  // all-time count and outrank facets that genuinely match. Absent from
  // `tag_counts` means zero for this filter set.
  for (const t of customTags.value) {
    opts.push({ value: t.slug, label: `#${t.slug}`, count: tagCounts.value[t.slug] ?? 0 })
  }
  return opts
})

// The chip row needs display labels the composable doesn't have; the facet
// options are assembled here, so publish the slug→label map back to it.
watch(tagOptions, (opts) => {
  query.tagLabels.value = Object.fromEntries(opts.map(o => [o.value, o.label]))
}, { immediate: true })

// Derived from the chip set rather than compared to a literal, so the trigger's
// "narrowed" styling can't drift from what `filterCount` counts.
const rangeNarrowed = computed(() => activeFilters.value.some(f => f.key === 'range'))

const deleting = ref(null)   // trace_id currently being deleted
const closing = ref(null)    // trace_id currently being manually closed
const selectedIds = ref(new Set())
const batchDeleting = ref(false)
const refreshing = ref(false)

const selectionCount = computed(() => selectedIds.value.size)
const allSelected = computed(() =>
  sessions.value.length > 0 && sessions.value.every(s => selectedIds.value.has(s.trace_id)))

const { stickyHeaderEl, stickyHeaderHeight } = useStickyHeader(loading)

async function reload() {
  await load()
  // Selection was computed against the previous page set; drop entries that
  // are no longer visible so a later batch-delete can't target rows the user
  // can't currently see.
  const visible = new Set(sessions.value.map(s => s.trace_id))
  selectedIds.value = new Set([...selectedIds.value].filter(id => visible.has(id)))
}

async function refresh() {
  refreshing.value = true
  try { await reload() } finally { refreshing.value = false }
}

function runSearch() {
  query.commitSearch()
  reload()
}

function toggleOne(traceId, checked) {
  const next = new Set(selectedIds.value)
  if (checked) next.add(traceId)
  else next.delete(traceId)
  selectedIds.value = next
}

function toggleSelectAll(checked) {
  selectedIds.value = checked ? new Set(sessions.value.map(s => s.trace_id)) : new Set()
}

function clearSelection() {
  selectedIds.value = new Set()
}

// Escape is the universal "never mind". An open popover owns it first — Reka
// closes on Escape and a portalled panel is in the DOM only while open, so
// defer to it rather than dropping the selection out from under the user.
function onEscape(e) {
  if (e.key !== 'Escape' || !selectedIds.value.size) return
  if (document.querySelector('.ds-popover')) return
  clearSelection()
}

function rowLabel(s) {
  return titlePreview(s.title) || `${shortTraceId(s.trace_id, 12)}...`
}

async function mutate(traceId, spinner, request, done) {
  spinner.value = traceId
  try {
    const res = await request()
    if (res.ok === false) {
      flash(`${done} failed: ${res.msg || 'unknown error'}`, 'error')
      return
    }
    flash(`${done} session ${shortTraceId(traceId, 12)}...`)
    await reload()
  } finally {
    spinner.value = null
  }
}

async function deleteSession(s) {
  const header = isActive(s)
    ? '⚠️  This session appears to still be ACTIVE. Deleting now will remove its '
      + 'trace data mid-session; subsequent spans will reappear as a new, partial trace.\n\n'
    : ''
  const ok = await confirm('Delete session', `${header}Delete "${rowLabel(s)}"? This removes all `
    + `spans, skill reads, plan sessions, and rule triggers for trace ${shortTraceId(s.trace_id, 12)}...`, true)
  if (!ok) return
  await mutate(s.trace_id, deleting, () => api.del(`/sessions/${s.trace_id}`), 'Deleted')
}

async function closeSession(s) {
  const ok = await confirm('Close session', `Mark "${rowLabel(s)}" as closed? This settles a `
    + 'corrupt or interrupted session that never emitted a SessionEnd. Its trace data is kept; '
    + 'only the status changes to ended.')
  if (!ok) return
  await mutate(s.trace_id, closing, () => api.post(`/sessions/${s.trace_id}/close`), 'Closed')
}

async function batchDelete() {
  const ids = [...selectedIds.value]
  if (!ids.length) return
  const idSet = new Set(ids)
  const activeSel = sessions.value.filter(s => idSet.has(s.trace_id) && isActive(s)).length
  const header = activeSel > 0
    ? `⚠️  ${activeSel} of the selected session(s) still appear ACTIVE. Deleting now removes their `
      + 'trace data mid-session; subsequent spans will reappear as new partial traces.\n\n'
    : ''
  const noun = `session${ids.length === 1 ? '' : 's'}`
  const ok = await confirm(`Delete ${ids.length} ${noun}`, `${header}Delete ${ids.length} ${noun}? `
    + 'This removes all spans, skill reads, plan sessions, and rule triggers for every selected trace.', true)
  if (!ok) return
  batchDeleting.value = true
  try {
    const res = await api.post('/sessions/batch-delete', { trace_ids: ids })
    if (res.ok === false) {
      flash(`Batch delete failed: ${res.msg || 'unknown error'}`, 'error')
      return
    }
    selectedIds.value = new Set()
    flash(`Deleted ${res.processed || ids.length} ${noun}`)
    await reload()
  } finally {
    batchDeleting.value = false
  }
}

// Add/remove a custom tag on one row: call the API, patch that row's chips
// from the returned slugs, and refresh the facet's custom-tag options so a
// brand-new tag appears (or a now-unused one drops off).
async function onTagChange(traceId, slug, op) {
  const { tags, error } = await (op === 'add' ? addTag : removeTag)(traceId, slug)
  if (error) { flash(error, 'error'); return }
  patchRowTags(sessions, traceId, tags)
  loadCustomTags()
}

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

query.persistAndReload(reload)

const publishHeader = useTraceHeaderPublisher()
watch([activeCount, totalCount, refreshing], () => {
  publishHeader({
    activeCount: activeCount.value,
    tabCount: totalCount.value,
    refreshing: refreshing.value,
    onRefresh: refresh,
  })
}, { immediate: true })

onMounted(() => {
  window.addEventListener('keydown', onEscape)
  loadRepoOptions()
  loadCustomTags()
  reload()
})

onBeforeUnmount(() => window.removeEventListener('keydown', onEscape))
</script>

<template>
  <div
    class="sticky-page-root"
    :style="{ '--regin-trace-header-h': stickyHeaderHeight ? stickyHeaderHeight + 'px' : '0px' }"
  >
    <!-- The toolbar pins to the top of `.content-scroll` so search / grouping
         state stays visible while scrolling a long session list. -->
    <div
      ref="stickyHeaderEl"
      class="sticky -top-4 lg:-top-6 z-20 bg-white -mx-4 -mt-4 px-4 pt-4 lg:-mx-8 lg:-mt-6 lg:px-8 lg:pt-6 pb-3 mb-4 border-b border-slate-200 shadow-[0_2px_4px_-2px_rgba(15,23,42,0.06)]"
    >
      <SessionToolbar
        v-model:search="searchInput"
        v-model:scope="searchScope"
        v-model:group="groupMode"
        v-model:range="range"
        v-model:kind="kind"
        v-model:status="activeFilter"
        v-model:tag="tagFilter"
        v-model:repo="repoFilter"
        v-model:trace-id="traceIdInput"
        :scope-options="SCOPE_OPTIONS"
        :range-options="RANGE_OPTIONS"
        :range-label="rangeLabel"
        :range-narrowed="rangeNarrowed"
        :range-start="customStart"
        :range-end="customEnd"
        :kind-options="KIND_OPTIONS"
        :status-options="ACTIVE_OPTIONS"
        :tag-options="tagOptions"
        :repo-options="repoOptions"
        :repo-counts="repoCounts"
        :filter-count="filterCount"
        :selection-count="selectionCount"
        :batch-deleting="batchDeleting"
        @search="runSearch"
        @reset="query.resetFilters(reload)"
        @batch-delete="batchDelete"
        @clear-selection="clearSelection"
        @range-preset="(v) => query.selectRangePreset(v)"
        @range-pick="(d) => query.pickRangeDay(d)"
        @range-clear="query.clearFilter('range')"
      />

      <SessionActiveFilters
        :filters="activeFilters"
        @clear="(key) => query.clearFilter(key)"
        @clear-all="query.resetFilters(reload)"
      />
    </div>

    <div class="slist">
      <div v-if="loading && !sessions.length" class="empty-state">Loading sessions…</div>

      <div v-if="sessions.length" class="slist__grid">
        <div class="slist__head">
          <div class="slist__head-check">
            <Checkbox
              :model-value="allSelected"
              :indeterminate="selectionCount > 0 && !allSelected"
              title="Select all"
              aria-label="Select all sessions"
              @update:model-value="toggleSelectAll"
            />
          </div>
          <div></div>
          <div>Session</div>
          <div>Repo</div>
          <div title="Spans and file edits this session; hover the +N hint for reads, rules, plans, prompts, and tools">Activity</div>
          <div>Context</div>
          <div title="Total wall-clock time / active agent work time (user-idle gaps excluded)">Elapsed / Active</div>
          <div>Last seen</div>
        </div>

        <template v-for="group in groups" :key="group.key">
          <div v-if="group.label" class="slist__group" :class="`slist__group--${group.tone}`">
            <span class="slist__group-label">
              <span v-if="group.tone === 'live'" class="slist__group-dot" aria-hidden="true"></span>
              {{ group.label }}
            </span>
            <span class="slist__group-count">{{ groupCount(group) }}</span>
          </div>
          <SessionListRow
            v-for="s in group.rows"
            :key="s.trace_id"
            :s="s"
            :clock="serverClock"
            :selected="selectedIds.has(s.trace_id)"
            :is-deleting="deleting === s.trace_id"
            :is-closing="closing === s.trace_id"
            @toggle="(checked) => toggleOne(s.trace_id, checked)"
            @delete="deleteSession"
            @close="closeSession"
            @add-tag="(slug) => onTagChange(s.trace_id, slug, 'add')"
            @remove-tag="(slug) => onTagChange(s.trace_id, slug, 'remove')"
          />
        </template>
      </div>

      <ul v-if="sessions.length" class="slist__cards">
        <template v-for="group in groups" :key="group.key">
          <li v-if="group.label" class="slist__group slist__group--card" :class="`slist__group--${group.tone}`">
            <span class="slist__group-label">{{ group.label }}</span>
            <span class="slist__group-count">{{ groupCount(group) }}</span>
          </li>
          <SessionCard
            v-for="s in group.rows"
            :key="s.trace_id"
            :s="s"
            :clock="serverClock"
            :selected="selectedIds.has(s.trace_id)"
            :is-deleting="deleting === s.trace_id"
            :is-closing="closing === s.trace_id"
            @toggle="(checked) => toggleOne(s.trace_id, checked)"
            @delete="deleteSession"
            @close="closeSession"
            @add-tag="(slug) => onTagChange(s.trace_id, slug, 'add')"
            @remove-tag="(slug) => onTagChange(s.trace_id, slug, 'remove')"
          />
        </template>
      </ul>

      <p v-if="!sessions.length && !loading && activeSearch" class="empty-state">
        No sessions match {{ searchScope }} <code class="cell-code">{{ activeSearch }}</code>.
      </p>
      <p v-else-if="!sessions.length && !loading" class="empty-state">
        No session traces yet. Install the File Edit Trace hook in Settings and start a Claude Code session.
      </p>

      <CursorControls
        v-if="sessions.length"
        :count="sessions.length"
        :total="totalCount"
        :has-next="hasNext"
        :loading-more="loadingMore"
        label="sessions"
        :note="footerNote"
        @load-more="loadMore"
      />
    </div>
  </div>
</template>

<style scoped>
.slist {
  background: var(--color-surface);
  border: 1px solid var(--color-border-subtle);
  border-radius: 0.875rem;
  margin-bottom: 1rem;
  overflow: hidden;
}

/* One grid template, declared once and consumed by the column header and
 * every row — the row lives in its own SFC, so a shared custom property is
 * what keeps the two in lockstep. */
.slist__grid { --session-cols: 26px 36px minmax(220px, 1fr) 104px 138px 132px 152px 84px; }
.slist__grid { display: none; padding: 0.25rem 0 0.375rem; }

.slist__head {
  align-items: center;
  border-bottom: 1px solid var(--color-border-subtle);
  color: var(--color-fg-faint);
  column-gap: 0.75rem;
  display: grid;
  font-size: 0.625rem;
  font-weight: 700;
  grid-template-columns: var(--session-cols);
  letter-spacing: 0.1em;
  padding: 0.5rem 0.875rem;
  text-transform: uppercase;
}
.slist__head-check { display: flex; }

.slist__group {
  align-items: center;
  display: flex;
  gap: 0.625rem;
  padding: 0.875rem 0.875rem 0.375rem;
}
.slist__group::after {
  background: var(--color-border-subtle);
  content: '';
  flex: 1 1 auto;
  height: 1px;
}
.slist__group-label {
  align-items: center;
  display: inline-flex;
  flex: 0 0 auto;
  font-size: 0.59375rem;
  font-weight: 700;
  gap: 0.4375rem;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}
.slist__group--live .slist__group-label { color: var(--color-emerald-700); }
.slist__group--muted .slist__group-label { color: var(--color-fg-faint); }
.slist__group-dot {
  background: var(--color-emerald-500);
  border-radius: 9999px;
  height: 0.375rem;
  width: 0.375rem;
}
.slist__group-count {
  color: var(--color-fg-faint);
  flex: 0 0 auto;
  font-size: 0.6875rem;
  font-variant-numeric: tabular-nums;
  order: 3;
}

.slist__cards { display: flex; flex-direction: column; }

@media (min-width: 640px) {
  .slist__grid { display: block; }
  .slist__cards { display: none; }
}
</style>
