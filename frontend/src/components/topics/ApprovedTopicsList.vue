<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import Badge from '../Badge.vue'
import Card from '../Card.vue'

const props = defineProps({
  data: { type: Object, default: null },
})

const route = useRoute()
const router = useRouter()

const SEARCH_KEY = 'regin_approved_topics_search'
const STATUS_KEY = 'regin_approved_topics_status'
const BROKEN_KEY = 'regin_approved_topics_broken'
const SORT_KEY = 'regin_approved_topics_sort'

const BROKEN_OPTIONS = [
  { value: 'all', label: 'All topics' },
  { value: 'broken', label: 'Has broken refs' },
  { value: 'clean', label: 'No broken refs' },
]
const SORT_OPTIONS = [
  { value: 'label', label: 'Label A→Z' },
  { value: 'label_desc', label: 'Label Z→A' },
  { value: 'refs', label: 'Most refs' },
  { value: 'edges', label: 'Most edges' },
  { value: 'broken', label: 'Most broken refs' },
]

const PAGE_SIZE = 25

const search = ref(localStorage.getItem(SEARCH_KEY) || '')
const statusFilter = ref(localStorage.getItem(STATUS_KEY) || 'all')
const brokenFilter = ref(localStorage.getItem(BROKEN_KEY) || 'all')
const sort = ref(localStorage.getItem(SORT_KEY) || 'label')
const page = ref(1)

watch(search, (v) => { localStorage.setItem(SEARCH_KEY, v); page.value = 1 })
watch(statusFilter, (v) => { localStorage.setItem(STATUS_KEY, v); page.value = 1 })
watch(brokenFilter, (v) => { localStorage.setItem(BROKEN_KEY, v); page.value = 1 })
watch(sort, (v) => { localStorage.setItem(SORT_KEY, v) })

const allTopics = computed(() => props.data?.table || [])

const statusOptions = computed(() => {
  const seen = new Set()
  for (const t of allTopics.value) {
    if (t.status) seen.add(t.status)
  }
  return [{ value: 'all', label: 'All statuses' }, ...Array.from(seen).sort().map((s) => ({ value: s, label: s }))]
})

const filtered = computed(() => {
  const q = search.value.trim().toLowerCase()
  const rows = allTopics.value.filter((t) => {
    if (statusFilter.value !== 'all' && t.status !== statusFilter.value) return false
    if (brokenFilter.value === 'broken' && !t.broken_ref_count) return false
    if (brokenFilter.value === 'clean' && t.broken_ref_count) return false
    if (q) {
      const blob = `${t.label || ''} ${t.intent || ''} ${(t.aliases || []).join(' ')} ${t.id || ''}`.toLowerCase()
      if (!blob.includes(q)) return false
    }
    return true
  })
  const cmp = ({
    label: (a, b) => (a.label || '').localeCompare(b.label || ''),
    label_desc: (a, b) => (b.label || '').localeCompare(a.label || ''),
    refs: (a, b) => (b.ref_count || 0) - (a.ref_count || 0),
    edges: (a, b) => (b.edge_count || 0) - (a.edge_count || 0),
    broken: (a, b) => (b.broken_ref_count || 0) - (a.broken_ref_count || 0),
  })[sort.value] || (() => 0)
  return rows.slice().sort(cmp)
})

const totalPages = computed(() => Math.max(1, Math.ceil(filtered.value.length / PAGE_SIZE)))
const pagedTopics = computed(() => {
  const start = (page.value - 1) * PAGE_SIZE
  return filtered.value.slice(start, start + PAGE_SIZE)
})

watch(totalPages, (n) => {
  if (page.value > n) page.value = n
})

const hasActiveFilter = computed(() =>
  search.value || statusFilter.value !== 'all' || brokenFilter.value !== 'all' || sort.value !== 'label'
)

function clearFilters() {
  search.value = ''
  statusFilter.value = 'all'
  brokenFilter.value = 'all'
  sort.value = 'label'
}

function withQuery(next) {
  return { ...route.query, ...next }
}

function chooseTopic(id) {
  router.replace({ query: withQuery({ tab: 'wiki', topic: id || undefined }) })
}
</script>

<template>
  <Card :no-padding="true">
    <div class="topics-panel-header">
      <div>
        <h2>Approved Topics</h2>
        <p class="topics-panel-caption">Pick a topic to inspect references, edges, and the previewed wiki content.</p>
      </div>
      <Badge color="blue" :label="`${filtered.length} / ${allTopics.length}`" />
    </div>
    <div class="topics-runs-filterbar">
      <input
        v-model="search"
        type="search"
        class="topics-input topics-input-grow focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"
        placeholder="Search by label, intent, alias…"
        aria-label="Search approved topics"
      >
      <select v-model="statusFilter" class="topics-input focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2" aria-label="Filter by status">
        <option v-for="opt in statusOptions" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
      </select>
      <select v-model="brokenFilter" class="topics-input focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2" aria-label="Filter by broken refs">
        <option v-for="opt in BROKEN_OPTIONS" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
      </select>
      <select v-model="sort" class="topics-input focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2" aria-label="Sort topics">
        <option v-for="opt in SORT_OPTIONS" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
      </select>
      <button
        v-if="hasActiveFilter"
        type="button"
        class="btn btn-secondary text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"
        @click="clearFilters"
      >Clear</button>
    </div>
    <table class="tbl tbl-workbench">
      <thead>
        <tr>
          <th>Label</th>
          <th>Status</th>
          <th class="text-right">Refs</th>
          <th class="text-right">Edges</th>
          <th class="text-right">Broken</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="topic in pagedTopics"
          :key="topic.id"
          class="topics-row-selectable cursor-pointer"
          @click="chooseTopic(topic.id)"
        >
          <td>
            <button
              type="button"
              class="topics-row-button focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"
              @click.stop="chooseTopic(topic.id)"
            >
              <div class="topics-row-title">{{ topic.label }}</div>
            </button>
          </td>
          <td><Badge :color="topic.broken_ref_count ? 'red' : 'green'" :label="topic.status" /></td>
          <td class="text-right">{{ topic.ref_count }}</td>
          <td class="text-right">{{ topic.edge_count }}</td>
          <td class="text-right">{{ topic.broken_ref_count }}</td>
        </tr>
        <tr v-if="!allTopics.length">
          <td colspan="5" class="text-gray-500">No approved topics yet. Use Scan Topics or Generate Wiki above.</td>
        </tr>
        <tr v-else-if="!pagedTopics.length">
          <td colspan="5" class="text-gray-500">
            No topics match the current filters.
            <button type="button" class="ml-2 text-blue-600 underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500" @click="clearFilters">Clear filters</button>
          </td>
        </tr>
      </tbody>
    </table>
    <div v-if="totalPages > 1" class="topics-runs-pagination">
      <button type="button" class="btn btn-secondary text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2" :disabled="page <= 1" @click="page--">← Prev</button>
      <span class="text-xs text-slate-600">Page {{ page }} of {{ totalPages }} · {{ filtered.length }} topic{{ filtered.length === 1 ? '' : 's' }}</span>
      <button type="button" class="btn btn-secondary text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2" :disabled="page >= totalPages" @click="page++">Next →</button>
    </div>
  </Card>
</template>
