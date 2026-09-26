<script setup>
// Every project, the way Linear lists them: a name, a derived status and a
// progress bar per row. A project is a registered repo; nothing here is
// stored, every figure is a rollup the tab strip would report under the same
// `?project=`.
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import Badge from '../components/Badge.vue'
import Card from '../components/Card.vue'
import ListStatus from '../components/ListStatus.vue'
import PageControls from '../components/PageControls.vue'
import PipelineListToolbar from '../components/PipelineListToolbar.vue'
import ProjectProgress from '../components/projects/ProjectProgress.vue'
import { usePage } from '../composables/usePage'
import { listStatusState, useListQuery } from '../composables/useListQuery'
import { useTaskShortcuts } from '../composables/useTaskShortcuts'
import { OPEN_TASKS_HINT, STATUS_COLOR, STATUS_LABEL, openTasks, projectName }
  from '../utils/projectFormat'

// `lib/pipeline/projects.py` — STATUSES and SORT_KEYS.
const STATUS_OPTIONS = [
  { value: '', label: 'Any status' },
  ...['in_progress', 'planned', 'completed', 'empty']
    .map(v => ({ value: v, label: STATUS_LABEL[v] || v })),
]
const SORT_OPTIONS = [
  { value: 'activity', label: 'Last activity' },
  { value: 'name', label: 'Name' },
  { value: 'progress', label: 'Progress' },
  { value: 'open_defects', label: 'Open defects' },
]

const route = useRoute()
const list = useListQuery({
  defaults: { q: '', status: '', sort: 'activity:desc' },
  facets: ['status'],
})
const { state } = list

// Every narrowing is the server's (I3): the tab sends q/status/sort/page and
// renders exactly the rows that come back.
const {
  items, loading, error, loaded, population, applied,
  page, pageSize, total, pageCount, hasNext, hasPrev, load,
} = usePage({
  path: '/pipeline/projects',
  buildQuery: () => ({ q: state.value.q, status: state.value.status,
                       sort: state.value.sort }),
})
const appliedSort = computed(() => applied.value?.sort || state.value.sort)
const sortKey = computed(() => appliedSort.value.split(':')[0])
const sortDir = computed(() => (appliedSort.value.split(':')[1] === 'asc' ? 'asc' : 'desc'))
const status = computed(() => listStatusState({
  loaded: loaded.value, error: error.value, items: items.value,
  total: total.value, population: population.value,
}))

async function reload() {
  page.value = state.value.page
  pageSize.value = state.value.size
  await load()
}
// Kept alive by the shell: a write made on another tab only shows here if the
// list is re-read when the tab comes back, which the path key does.
watch(() => (route.path === '/projects' ? JSON.stringify(route.query) : null),
  (key) => { if (key != null) reload() }, { immediate: true })

function gotoPage(index) {
  list.patch({ page: String(Math.max(0, Math.min(pageCount.value - 1, index))) })
}

const toolbar = ref(null)
useTaskShortcuts({ '/': () => toolbar.value?.focusSearch() })

function isQuiet(p) {
  return p.features.total === 0 && p.tasks.total === 0 && p.defects.total === 0
}
</script>

<template>
  <div class="space-y-4" data-testid="projects-view">
    <PipelineListToolbar
      ref="toolbar"
      noun="project"
      :q="state.q"
      search-label="Search projects by name or description"
      search-placeholder="Search projects…"
      :facets="[{ key: 'status', label: 'Filter by status', value: state.status, options: STATUS_OPTIONS }]"
      :sort-options="SORT_OPTIONS"
      :sort-key="sortKey"
      :sort-dir="sortDir"
      :total="loaded && !error ? total : null"
      :population="population"
      :dirty-count="list.dirtyCount.value"
      @patch="list.patch"
      @reset="list.reset"
    />
    <ListStatus
      :state="status"
      noun="project"
      :total="total"
      :page="page"
      :dirty-count="list.dirtyCount.value"
      :error="error || ''"
      empty-text="No projects yet. Register a repo with regin add-repo."
      @retry="load"
      @clear="list.reset"
      @first-page="list.patch({ page: '0' })"
    />
    <Card v-if="status === 'rows'" :no-padding="true" :class="{ 'pv-busy': loading }">
      <div class="overflow-x-auto">
        <table class="tbl" data-testid="projects-table">
          <thead>
            <tr>
              <th>Project</th>
              <th style="width: 8rem">Status</th>
              <th style="width: 18rem">Progress</th>
              <th class="num" style="width: 6rem">Features</th>
              <th class="num" style="width: 6rem" :title="OPEN_TASKS_HINT">Open tasks</th>
              <th class="num" style="width: 7rem">Open defects</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="p in items"
              :key="p.scope"
              :class="{ 'pv-quiet': isQuiet(p) }"
              :data-testid="`project-row-${p.scope}`"
            >
              <td>
                <router-link
                  :to="`/projects/${p.scope}`"
                  class="table-link font-medium focus-visible:outline-2 focus-visible:outline-blue-500"
                >{{ projectName(p) }}</router-link>
                <div v-if="p.description" class="pv-desc">{{ p.description }}</div>
                <div v-else-if="!p.registered && p.scope !== 'none'" class="pv-desc">
                  not in the registry — rows still name this id
                </div>
              </td>
              <td><Badge :color="STATUS_COLOR[p.status] || 'gray'" :label="STATUS_LABEL[p.status] || p.status" /></td>
              <td><ProjectProgress :progress="p.progress" :by-stage="p.features.by_stage" compact /></td>
              <td class="num">{{ p.features.total }}</td>
              <td class="num">{{ openTasks(p.tasks) }}</td>
              <td class="num" :class="{ 'pv-hot': p.defects.open > 0 }">{{ p.defects.open }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </Card>
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
.num { text-align: right; font-variant-numeric: tabular-nums; }
.pv-desc {
    font-size: 0.75rem;
    color: var(--color-text-muted, #6b7280);
    margin-top: 0.125rem;
    max-width: 32rem;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}
.pv-quiet { opacity: 0.6; }
.pv-hot { color: var(--color-amber-700, #b45309); font-weight: 600; }
.pv-busy { opacity: 0.6; }
</style>
