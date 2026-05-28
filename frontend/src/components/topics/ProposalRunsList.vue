<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import api from '../../api'
import { useConfirm } from '../../composables/useConfirm'
import Badge from '../Badge.vue'
import Card from '../Card.vue'
import { fmtAgo } from '../../utils/traceFormatters'

const props = defineProps({
  repo: { type: String, required: true },
  data: { type: Object, default: null },
  busy: { type: Boolean, default: false },
})

const emit = defineEmits(['refresh', 'error'])

const route = useRoute()
const router = useRouter()
const { confirm } = useConfirm()

const SEARCH_KEY = 'regin_proposal_runs_search'
const STATE_KEY = 'regin_proposal_runs_state'
const REVIEW_KEY = 'regin_proposal_runs_review'
const SORT_KEY = 'regin_proposal_runs_sort'

const STATE_OPTIONS = [
  { value: 'all', label: 'All states' },
  { value: 'queued', label: 'Queued' },
  { value: 'running', label: 'Running' },
  { value: 'waiting_for_permission', label: 'Waiting for permission' },
  { value: 'completed', label: 'Completed' },
  { value: 'failed', label: 'Failed' },
  { value: 'timed_out', label: 'Timed out' },
  { value: 'cancelled', label: 'Cancelled' },
]
const REVIEW_OPTIONS = [
  { value: 'all', label: 'All reviews' },
  { value: 'pending_review', label: 'Pending review' },
  { value: 'changes_requested', label: 'Changes requested' },
  { value: 'ready_to_apply', label: 'Ready to apply' },
  { value: 'partially_applied', label: 'Partially applied' },
  { value: 'applied', label: 'Applied' },
]
const SORT_OPTIONS = [
  { value: 'newest', label: 'Recently updated' },
  { value: 'oldest', label: 'Least recently updated' },
  { value: 'revisions', label: 'Most revisions' },
  { value: 'drafts', label: 'Most drafts' },
]

const PAGE_SIZE = 25

const search = ref(localStorage.getItem(SEARCH_KEY) || '')
const stateFilter = ref(localStorage.getItem(STATE_KEY) || 'all')
const reviewFilter = ref(localStorage.getItem(REVIEW_KEY) || 'all')
const sort = ref(localStorage.getItem(SORT_KEY) || 'newest')
const page = ref(1)

watch(search, (v) => { localStorage.setItem(SEARCH_KEY, v); page.value = 1 })
watch(stateFilter, (v) => { localStorage.setItem(STATE_KEY, v); page.value = 1 })
watch(reviewFilter, (v) => { localStorage.setItem(REVIEW_KEY, v); page.value = 1 })
watch(sort, (v) => { localStorage.setItem(SORT_KEY, v) })

const allRuns = computed(() => props.data?.runs || [])

const filtered = computed(() => {
  const q = search.value.trim().toLowerCase()
  const rows = allRuns.value.filter((run) => {
    const state = run.state || 'completed'
    if (stateFilter.value !== 'all' && state !== stateFilter.value) return false
    if (reviewFilter.value !== 'all') {
      const rs = run.review_state || 'pending_review'
      if (rs !== reviewFilter.value) return false
    }
    if (q) {
      const blob = `${run.id} ${run.title || ''} ${run.topic_request || ''} ${run.provider || ''} ${run.review_state || ''} ${state} ${run.agent || ''}`.toLowerCase()
      if (!blob.includes(q)) return false
    }
    return true
  })
  const cmp = ({
    newest: (a, b) => activityTs(b) - activityTs(a),
    oldest: (a, b) => activityTs(a) - activityTs(b),
    revisions: (a, b) => (b.revision_count || 0) - (a.revision_count || 0),
    drafts: (a, b) => (b.draft_topic_count || 0) - (a.draft_topic_count || 0),
  })[sort.value] || (() => 0)
  return rows.slice().sort(cmp)
})

const totalPages = computed(() => Math.max(1, Math.ceil(filtered.value.length / PAGE_SIZE)))
const pagedRuns = computed(() => {
  const start = (page.value - 1) * PAGE_SIZE
  return filtered.value.slice(start, start + PAGE_SIZE)
})

watch(totalPages, (n) => {
  if (page.value > n) page.value = n
})

const hasActiveFilter = computed(() =>
  search.value || stateFilter.value !== 'all' || reviewFilter.value !== 'all' || sort.value !== 'newest'
)

function clearFilters() {
  search.value = ''
  stateFilter.value = 'all'
  reviewFilter.value = 'all'
  sort.value = 'newest'
}

function withQuery(next) {
  return { ...route.query, ...next }
}

function chooseRun(id) {
  router.replace({
    query: withQuery({ tab: 'proposals', proposal: id, revision: undefined, draft: undefined }),
  })
}

async function onDelete(run) {
  const ok = await confirm('Delete proposal run', `Delete proposal run ${run.id}?`, true)
  if (!ok) return
  try {
    const result = await api.post(`/repos/${props.repo}/topics/proposals/${run.id}/delete`, {})
    if (!result.ok) {
      emit('error', result.msg || result.error || 'Delete proposal failed')
      return
    }
    emit('refresh')
  } catch (err) {
    emit('error', err.message || String(err))
  }
}

function proposalStateColor(state) {
  if (state === 'completed') return 'green'
  if (state === 'failed' || state === 'timed_out') return 'red'
  if (state === 'waiting_for_permission') return 'yellow'
  if (state === 'running' || state === 'queued') return 'blue'
  if (state === 'cancelled') return 'gray'
  return 'gray'
}

function proposalReviewColor(status) {
  if (status === 'ready_to_apply') return 'green'
  if (status === 'applied') return 'green'
  if (status === 'changes_requested') return 'yellow'
  if (status === 'partially_applied') return 'blue'
  return 'gray'
}

// Sort key: last content change (generate / regenerate / downgrade /
// restore), not the creation-time run id — a regenerated run should sort
// as fresh even though its id is old.
function activityTs(run) {
  const t = Date.parse(run.last_activity_at || '')
  return Number.isNaN(t) ? 0 : t
}
</script>

<template>
  <Card :no-padding="true">
    <div class="topics-panel-header">
      <div>
        <h2>Proposal Runs</h2>
        <p class="topics-panel-caption">Operational history, provider status, and review queue depth.</p>
      </div>
      <Badge color="purple" :label="`${filtered.length} / ${allRuns.length}`" />
    </div>
    <div class="topics-runs-filterbar">
      <input
        v-model="search"
        type="search"
        class="topics-input topics-input-grow focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"
        placeholder="Search by run id, provider, agent…"
        aria-label="Search proposal runs"
      >
      <select v-model="stateFilter" class="topics-input focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2" aria-label="Filter by state">
        <option v-for="opt in STATE_OPTIONS" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
      </select>
      <select v-model="reviewFilter" class="topics-input focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2" aria-label="Filter by review state">
        <option v-for="opt in REVIEW_OPTIONS" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
      </select>
      <select v-model="sort" class="topics-input focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2" aria-label="Sort runs">
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
          <th>Run</th>
          <th>State</th>
          <th>Provider</th>
          <th>Review</th>
          <th class="text-right">Revisions</th>
          <th class="text-right">Drafts</th>
          <th class="text-right">Reviewed</th>
          <th class="text-right"></th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="run in pagedRuns"
          :key="run.id"
          class="topics-row-selectable cursor-pointer"
          @click="chooseRun(run.id)"
        >
          <td>
            <button
              type="button"
              class="topics-row-button focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"
              @click.stop="chooseRun(run.id)"
            >
              <div class="topics-row-title line-clamp-2" :title="run.topic_request || run.title || ''">{{ run.title || 'Untitled run' }}</div>
              <div class="topics-row-meta text-slate-500">
                <code class="text-[11px]">{{ run.id }}</code>
                <span v-if="fmtAgo(run.last_activity_at)" class="ml-1 text-[11px]">· updated {{ fmtAgo(run.last_activity_at) }}</span>
              </div>
              <div v-if="run.error" class="topics-row-meta text-red-600 line-clamp-2">{{ run.error }}</div>
            </button>
          </td>
          <td><Badge :color="proposalStateColor(run.state)" :label="run.state || 'completed'" /></td>
          <td>{{ run.provider }}<span v-if="run.agent" class="text-xs text-slate-500"> · {{ run.agent }}</span></td>
          <td><Badge :color="proposalReviewColor(run.review_state)" :label="(run.review_state || 'pending_review').replaceAll('_', ' ')" /></td>
          <td class="text-right">{{ run.revision_count || (run.latest_revision_number ? 1 : 0) }}</td>
          <td class="text-right">{{ run.draft_topic_count }}</td>
          <td class="text-right">{{ run.reviewed_count }}</td>
          <td class="text-right">
            <button
              type="button"
              class="btn btn-danger text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"
              :disabled="busy"
              @click.stop="onDelete(run)"
            >Delete</button>
          </td>
        </tr>
        <tr v-if="!allRuns.length">
          <td colspan="8" class="text-gray-500">No proposal runs yet. Generate one above.</td>
        </tr>
        <tr v-else-if="!pagedRuns.length">
          <td colspan="8" class="text-gray-500">
            No runs match the current filters.
            <button type="button" class="ml-2 text-blue-600 underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500" @click="clearFilters">Clear filters</button>
          </td>
        </tr>
      </tbody>
    </table>
    <div v-if="totalPages > 1" class="topics-runs-pagination">
      <button type="button" class="btn btn-secondary text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2" :disabled="page <= 1" @click="page--">← Prev</button>
      <span class="text-xs text-slate-600">Page {{ page }} of {{ totalPages }} · {{ filtered.length }} run{{ filtered.length === 1 ? '' : 's' }}</span>
      <button type="button" class="btn btn-secondary text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2" :disabled="page >= totalPages" @click="page++">Next →</button>
    </div>
  </Card>
</template>
