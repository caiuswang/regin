<script setup>
// The defect ledger, readable and writable.
//
// The whole list state lives in the URL (`useListQuery`): a shared
// `?scope=`/`?status=`/`?q=`/`?sort=`/`?page=` is a link to exactly the rows
// on screen. The scope is the view switcher — All, Unattributed, Triage — and
// `all` is the default, so a defect reported against a feature is never in a
// scope this page cannot open.
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { usePipelineHeaderPublisher } from '../composables/usePipelineHeader'
import { usePage } from '../composables/usePage'
import { listStatusState, useListQuery } from '../composables/useListQuery'
import { scopeParam } from '../composables/usePipelineScope'
import { useFlash } from '../composables/useFlash'
import { invalidatePipelineCounts } from '../composables/usePipelineCounts'
import { useTaskShortcuts } from '../composables/useTaskShortcuts'
import api from '../api'
import ListStatus from '../components/ListStatus.vue'
import PageControls from '../components/PageControls.vue'
import PipelineListToolbar from '../components/PipelineListToolbar.vue'
import DefectRow from '../components/defects/DefectRow.vue'
import DefectTriageQueue from '../components/defects/DefectTriageQueue.vue'
import DefectsScopeBanner from '../components/defects/DefectsScopeBanner.vue'

// `lib/defects/query.py` — the server's vocabulary, spelled as the API spells
// it, because a second spelling here is a second rulebook to keep in step.
const SCOPES = [
  { value: 'all', label: 'All' },
  { value: 'unattributed', label: 'Unattributed' },
  // Narrower than `unattributed`, not a filter on top of it: the queue is what
  // is still WAITING to be routed.
  { value: 'triage', label: 'Triage' },
]
const STATUSES = [
  { value: 'all', label: 'Any status' },
  { value: 'open', label: 'Open' },
  { value: 'closed', label: 'Closed' },
]
const SORT_KEYS = [
  { value: 'opened', label: 'Opened' },
  { value: 'updated', label: 'Updated' },
  { value: 'round', label: 'Round' },
  { value: 'status', label: 'Status' },
  { value: 'feature', label: 'Feature' },
]

const route = useRoute()
const publish = usePipelineHeaderPublisher()
const { flash } = useFlash()
const list = useListQuery({
  defaults: { scope: 'all', status: 'all', q: '', sort: 'opened:desc' },
  facets: ['status'],
  viewKey: 'scope',
  kept: ['feature_id', 'fix_item'],
})
const { state } = list
const featureId = computed(() => String(route.query.feature_id ?? '').trim())
const fixItem = computed(() => String(route.query.fix_item ?? '').trim())
// A followed key is a scope you cannot leave with a pill, so the pills hide.
const followed = computed(() => Boolean(featureId.value || fixItem.value))
// The queue is a different reading of the ledger, not a filter over it: it
// owns its own read, its own count and its own keyboard flow.
const triaging = computed(() => state.value.scope === 'triage' && !followed.value)

const {
  items, loading, error, loaded, population, applied,
  page, pageSize, total, pageCount, hasNext, hasPrev, load,
} = usePage({
  path: '/defects',
  buildQuery: () => ({
    feature_id: featureId.value,
    fix_item: fixItem.value,
    project: scopeParam(route.query.project),
    scope: state.value.scope === 'all' ? '' : state.value.scope,
    status: state.value.status === 'all' ? '' : state.value.status,
    q: state.value.q,
    sort: state.value.sort,
  }),
})

const appliedSort = computed(() => applied.value?.sort || state.value.sort)
const sortKey = computed(() => appliedSort.value.split(':')[0])
const sortDir = computed(() => (appliedSort.value.split(':')[1] === 'asc' ? 'asc' : 'desc'))
const servedScope = computed(() => applied.value?.filters?.scope || 'all')

const canWrite = computed(() => api.getStoredUser()?.role !== 'viewer')

// Each scope says what its own emptiness means.
const EMPTY = {
  all: 'The ledger is empty. Nothing has been reported yet.',
  unattributed: 'Nothing is unblamed. Every defect regin holds names the feature that caused it.',
  feature: 'This feature has no defects recorded.',
  'fix-item': 'Nothing points at this item.',
}
const LEAD = {
  all: 'Every defect regin holds, newest first — attributed or not.',
  unattributed: 'Defects blamed on no feature. Attribution is a human naming the feature that CAUSED the bug — regin never guesses one from a path, a session or a cost.',
  triage: 'The routing queue: unattributed reports nobody has decided on yet. Accept, mark duplicate, decline or snooze — one key each, and no feature is ever preselected.',
  feature: 'Every defect blamed on this feature.',
  'fix-item': 'Every defect this one tracker item is meant to close.',
}

// `population` counts the ledger under the project scope; a narrower scope
// (unattributed, a feature) has its own emptiness, which is not "no match".
const scopeEmpty = computed(() => servedScope.value !== 'all'
  && !list.dirtyCount.value && total.value === 0)
const status = computed(() => (scopeEmpty.value && loaded.value && !error.value
  ? 'empty'
  : listStatusState({
    loaded: loaded.value, error: error.value, items: items.value,
    total: total.value, population: population.value,
  })))

async function reload() {
  if (triaging.value) {
    publish({ subtitle: LEAD.triage })
    return
  }
  page.value = state.value.page
  pageSize.value = state.value.size
  await load()
  if (!error.value) publish({ subtitle: LEAD[servedScope.value] || LEAD.all })
}
watch(() => (route.path === '/defects' ? JSON.stringify(route.query) : null),
  (key) => { if (key != null) reload() }, { immediate: true })

// A write reloads the page and scope it was on: rows stay (dimmed while the
// read is in flight), nothing resets to page 0, and the shell's counts are
// told, because a merge really did move the number in the tab strip.
function onChanged(message) {
  if (message) flash(message)
  invalidatePipelineCounts()
  load()
}

function gotoPage(index) {
  list.patch({ page: String(Math.max(0, Math.min(pageCount.value - 1, index))) })
}

const toolbar = ref(null)
useTaskShortcuts({ '/': () => toolbar.value?.focusSearch() })
</script>

<template>
  <div class="space-y-4">
    <PipelineListToolbar
      ref="toolbar"
      noun="defect"
      :views="followed ? [] : SCOPES"
      :view="state.scope"
      :bare="triaging"
      :q="state.q"
      search-label="Search defects by symptom"
      search-placeholder="Search symptoms…"
      :facets="[{ key: 'status', label: 'Status', value: state.status, options: STATUSES }]"
      :sort-options="SORT_KEYS"
      :sort-key="sortKey"
      :sort-dir="sortDir"
      :total="loaded && !error ? total : null"
      :population="population"
      :dirty-count="list.dirtyCount.value"
      @view="v => list.patch({ scope: v })"
      @patch="list.patch"
      @reset="list.reset"
    />
    <DefectsScopeBanner
      :feature-id="featureId"
      :fix-item="fixItem"
      :feature-title="items[0]?.feature_title || ''"
    />

    <DefectTriageQueue v-if="triaging" @changed="onChanged" />

    <template v-else>
      <ListStatus
        :state="status"
        noun="defect"
        :total="total"
        :page="page"
        :dirty-count="list.dirtyCount.value"
        :error="error || ''"
        :empty-text="EMPTY[servedScope] || EMPTY.all"
        @retry="load"
        @clear="list.reset"
        @first-page="list.patch({ page: '0' })"
      />

      <ul v-if="status === 'rows'" class="list" :class="{ 'is-busy': loading }" data-testid="defect-list">
        <DefectRow
          v-for="defect in items"
          :key="defect.id"
          :defect="defect"
          :can-write="canWrite"
          collapsible
          @changed="onChanged"
        />
      </ul>

      <PageControls
        v-if="loaded && total > pageSize"
        :page="page"
        :page-count="pageCount"
        :total="total"
        :size="pageSize"
        :has-next="hasNext"
        :has-prev="hasPrev"
        :loading="loading"
        @prev="gotoPage(page - 1)"
        @next="gotoPage(page + 1)"
        @goto="gotoPage"
        @set-size="n => list.patch({ size: String(n) })"
      />
    </template>
  </div>
</template>

<style scoped>
.list { display: flex; flex-direction: column; gap: 0.4rem; }
/* A reload in flight dims the rows it is about to replace. It does not remove
   them: the reader keeps their place, and an open form keeps its text. */
.list.is-busy { opacity: 0.6; }
</style>
