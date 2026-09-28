<script setup>
// Every defect, findable after it has left the triage queue (D2).
//
// A child of the Pipeline shell, not a tab: defects are routed from the
// Features rail, and this page is where a closed, declined or snoozed one is
// found again. Every narrowing is in the URL and the server applies all of
// it — `GET /api/defects` is an offset page with its own counts, so nothing
// here filters or counts a loaded array.
import { computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import api from '../api'
import { usePipelineHeaderPublisher } from '../composables/usePipelineHeader'
import { scopeParam } from '../composables/usePipelineScope'
import { usePage } from '../composables/usePage'
import { listStatusState, useListQuery } from '../composables/useListQuery'
import { useFlash } from '../composables/useFlash'
import { invalidatePipelineCounts } from '../composables/usePipelineCounts'
import Button from '../components/ui/Button.vue'
import Icon from '../components/ui/Icon.vue'
import ListStatus from '../components/ListStatus.vue'
import PageControls from '../components/PageControls.vue'
import PipelineListToolbar from '../components/PipelineListToolbar.vue'
import DefectRow from '../components/defects/DefectRow.vue'

const route = useRoute()
const router = useRouter()
const publish = usePipelineHeaderPublisher()
const { flash } = useFlash()

// `lib/defects/query.py::SORTS`, restated so the select is never empty.
const SORT_OPTIONS = [
  { value: 'opened', label: 'Opened' },
  { value: 'updated', label: 'Updated' },
  { value: 'round', label: 'Round' },
  { value: 'status', label: 'Status' },
  { value: 'feature', label: 'Feature' },
]

const list = useListQuery({
  defaults: { q: '', status: '', triage: '', sort: 'opened:desc' },
  facets: ['status', 'triage'],
  kept: ['feature_id'],
})
const { state } = list

const {
  items, loading, error, loaded, population, applied,
  page, pageSize, total, pageCount, hasNext, hasPrev, load,
} = usePage({
  path: '/defects',
  buildQuery: () => ({
    q: state.value.q,
    status: state.value.status,
    triage: state.value.triage,
    sort: state.value.sort,
    feature_id: route.query.feature_id,
    project: scopeParam(route.query.project),
  }),
})

const facets = computed(() => [
  { key: 'status', label: 'Status', value: state.value.status, options: [
    { value: '', label: 'Any status' },
    { value: 'open', label: 'Open' },
    { value: 'closed', label: 'Closed' },
  ] },
  { key: 'triage', label: 'Triage', value: state.value.triage, options: [
    { value: '', label: 'Any triage' },
    { value: 'queue', label: 'In the queue' },
    { value: 'snoozed', label: 'Snoozed' },
    { value: 'declined', label: 'Declined' },
  ] },
])

const appliedSort = computed(() => applied.value?.sort || state.value.sort)
const sortKey = computed(() => appliedSort.value.split(':')[0])
const sortDir = computed(() => (appliedSort.value.split(':')[1] === 'asc' ? 'asc' : 'desc'))
const canWrite = computed(() => api.getStoredUser()?.role !== 'viewer')
const featureId = computed(() => route.query.feature_id || '')
const status = computed(() => listStatusState({
  loaded: loaded.value, error: error.value, items: items.value,
  total: total.value, population: population.value,
}))

publish({ subtitle: 'Every defect, open or closed, triaged or not.' })

function gotoPage(index) {
  list.patch({ page: String(Math.max(0, Math.min(pageCount.value - 1, index))) })
}

function clearFeature() {
  const query = { ...route.query }
  delete query.feature_id
  delete query.page
  router.replace({ path: route.path, query })
}

async function reload() {
  page.value = state.value.page
  pageSize.value = state.value.size
  await load()
}
watch(() => (route.path === '/defects' ? JSON.stringify(route.query) : null),
  (key) => { if (key != null) reload() }, { immediate: true })

// A merge deletes the row it was made on; the keeper is the one to show.
function onChanged(message, info) {
  if (message) {
    flash(message, 'success', info?.keeper
      ? { link: { to: `/defects/${info.keeper}`, label: `Open #${info.keeper}` } } : {})
  }
  invalidatePipelineCounts()
  if (info?.keeper) {
    router.push(`/defects/${info.keeper}`)
    return
  }
  load()
}
</script>

<template>
  <div class="dv" data-testid="defects-list-page">
    <PipelineListToolbar
      :q="state.q"
      search-label="Search defects by symptom"
      search-placeholder="Search symptoms"
      :facets="facets"
      :sort-options="SORT_OPTIONS"
      :sort-key="sortKey"
      :sort-dir="sortDir"
      :total="loaded ? total : null"
      :population="population"
      noun="defect"
      :dirty-count="list.dirtyCount.value"
      @patch="list.patch"
      @reset="list.reset"
    >
      <template #controls>
        <span v-if="featureId" class="dv-chip" data-testid="defects-feature-chip">
          <router-link :to="`/features/${featureId}`">feature #{{ featureId }}</router-link>
          <Button variant="ghost" size="icon" class="dv-chip__x"
                  :aria-label="`Show defects of every feature, not only #${featureId}`"
                  data-testid="defects-feature-clear" @click="clearFeature"
          ><Icon name="x" :size="12" /></Button>
        </span>
      </template>
    </PipelineListToolbar>

    <ListStatus
      :state="status"
      noun="defect"
      :total="total"
      :page="page"
      :dirty-count="list.dirtyCount.value"
      :error="error || ''"
      empty-text="No defects yet. One is declared from a task, a feature, or `regin defects open`."
      @retry="load"
      @clear="list.reset"
      @first-page="list.patch({ page: '0' })"
    />

    <ul v-if="status === 'rows'" class="dv-list" :class="{ 'is-busy': loading }" data-testid="defects-list">
      <DefectRow
        v-for="d in items"
        :key="d.id"
        :defect="d"
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
  </div>
</template>

<style scoped>
.dv { display: flex; flex-direction: column; gap: 0.75rem; min-width: 0; }
.dv-list { display: flex; flex-direction: column; gap: 0.5rem; }
.dv-list.is-busy { opacity: 0.6; }
.dv-chip {
    display: inline-flex; align-items: center; gap: 0.2rem;
    border: 1px solid var(--color-border); border-radius: 999px;
    padding: 0 0.15rem 0 0.6rem; font-size: 12px;
}
.dv-chip a { color: var(--color-primary); text-decoration: none; }
.dv-chip .dv-chip__x { width: 22px; height: 22px; }
</style>
