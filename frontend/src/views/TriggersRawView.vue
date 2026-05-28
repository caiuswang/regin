<script setup>
import { computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import api from '../api'
import Card from '../components/Card.vue'
import Badge from '../components/Badge.vue'
import CursorControls from '../components/CursorControls.vue'
import { useFlash } from '../composables/useFlash'
import { useConfirm } from '../composables/useConfirm'
import { useCursor } from '../composables/useCursor'
import { useStickyHeader } from '../composables/useStickyHeader'

const route = useRoute()
const router = useRouter()
const { flash } = useFlash()
const { confirm } = useConfirm()

const {
  items, extras, loading, loadingMore, hasNext,
  load, loadMore,
} = useCursor({
  path: '/triggers',
  size: 100,
  buildQuery: () => ({
    rule: route.query.rule,
    session: route.query.session,
    triggered: route.query.triggered,
  }),
})

const { stickyHeaderEl, stickyHeaderHeight } = useStickyHeader(loading)

const ruleFilter = computed(() => extras.value.rule_filter || null)
const sessionFilter = computed(() => extras.value.session_filter || null)
const onlyTriggered = computed(() => !!extras.value.only_triggered)
const stats = computed(() => extras.value.stats || [])
const sessions = computed(() => extras.value.sessions || [])

onMounted(load)
watch(() => route.query, load)

function filterBy(key, value) {
  const q = { ...route.query }
  if (value) { q[key] = value } else { delete q[key] }
  router.push({ query: q })
}

function clearFilters() { router.push({ query: {} }) }

async function resetTriggers() {
  const scope = ruleFilter.value ? `rule=${ruleFilter.value}`
    : sessionFilter.value ? `session=${sessionFilter.value.slice(0, 8)}`
    : 'all'
  const ok = await confirm('Reset triggers', `Delete the currently-filtered trace rows (${scope})? This cannot be undone.`, true)
  if (!ok) return
  const body = {}
  if (ruleFilter.value) body.rule = ruleFilter.value
  if (sessionFilter.value) body.session = sessionFilter.value
  const result = await api.post('/triggers/reset', body)
  if (!result.ok) { flash(result.msg || 'Failed to reset', 'error'); return }
  flash(result.msg)
  await load()
}
</script>

<template>
  <div v-if="loading" class="empty-state">Loading triggers…</div>
  <div
    v-else
    class="sticky-page-root"
    :style="{ '--regin-trace-header-h': stickyHeaderHeight ? stickyHeaderHeight + 'px' : '0px' }"
  >
    <!-- Sticky page header: subtitle + filter toolbar pin to the top of
         `.content-scroll` so filter context stays visible while scrolling
         the long Recent events table below. Negative margins extend the
         opaque background to the content card edges; `top: -1rem` (mobile)
         / `-1.5rem` (desktop) covers `.content-scroll`'s padding-top. -->
    <div
      ref="stickyHeaderEl"
      class="sticky -top-4 lg:-top-6 z-20 bg-white -mx-4 -mt-4 px-4 pt-4 lg:-mx-8 lg:-mt-6 lg:px-8 lg:pt-6 pb-3 mb-4 border-b border-slate-200 shadow-[0_2px_4px_-2px_rgba(15,23,42,0.06)]"
    >
    <p class="page-subtitle mb-4">Rows with <strong>matches &gt; 0</strong> mean the rule fired during a PostToolUse hook.</p>

    <div class="toolbar">
      <span class="toolbar-label">Filter</span>
      <button type="button"
        class="filter-chip focus-visible:outline-2 focus-visible:outline-blue-500"
        :class="{ active: !ruleFilter && !onlyTriggered && !sessionFilter }"
        @click="clearFilters">All</button>
      <button type="button"
        class="filter-chip focus-visible:outline-2 focus-visible:outline-blue-500"
        :class="{ active: onlyTriggered }"
        @click="filterBy('triggered', onlyTriggered ? null : '1')">Triggered only</button>
      <Badge v-if="ruleFilter" color="yellow">
        rule = {{ ruleFilter }}
        <button type="button" class="badge-x focus-visible:outline-2 focus-visible:outline-blue-500"
          aria-label="Clear rule filter" @click="filterBy('rule', null)">&times;</button>
      </Badge>
      <Badge v-if="sessionFilter" color="blue">
        session = {{ sessionFilter.slice(0, 8) }}…
        <button type="button" class="badge-x focus-visible:outline-2 focus-visible:outline-blue-500"
          aria-label="Clear session filter" @click="filterBy('session', null)">&times;</button>
      </Badge>
      <button type="button" class="btn btn-danger text-xs ml-auto focus-visible:outline-2 focus-visible:outline-blue-500"
        @click="resetTriggers">
        {{ ruleFilter || sessionFilter ? 'Reset filtered' : 'Reset all' }}
      </button>
    </div>
    </div>
    <!-- /sticky page header -->

    <div class="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
      <Card :no-padding="true">
        <div class="card-group-header">
          <h2 class="card-group-title">Sessions <span class="text-slate-400 font-normal font-mono text-xs ml-1">recent 50</span></h2>
        </div>
        <table v-if="sessions.length" class="tbl">
          <thead>
            <tr><th>Session</th><th class="text-right">Checks</th><th class="text-right">Fired</th><th class="text-right">Rules</th><th class="text-right">Files</th><th>Plan</th><th>Last</th></tr>
          </thead>
          <tbody>
            <tr v-for="s in sessions" :key="s.session_id" :class="{ 'tbl-row-active': sessionFilter === s.session_id }">
              <td>
                <button v-if="s.session_id" type="button"
                  class="table-link bg-transparent border-0 p-0 cursor-pointer focus-visible:outline-2 focus-visible:outline-blue-500"
                  @click="filterBy('session', s.session_id)">
                  <code class="cell-code">{{ s.session_id.slice(0, 8) }}…</code>
                </button>
                <span v-else class="text-slate-400 text-xs">unknown</span>
              </td>
              <td class="text-right font-mono text-xs">{{ s.total }}</td>
              <td class="text-right">
                <Badge v-if="s.fired > 0" color="red" :label="String(s.fired)" />
                <span v-else class="text-slate-400 font-mono text-xs">0</span>
              </td>
              <td class="text-right font-mono text-xs">{{ s.rules }}</td>
              <td class="text-right font-mono text-xs">{{ s.files }}</td>
              <td>
                <router-link v-if="s.plan_filename" :to="`/plans/${s.plan_filename}`"
                  class="table-link focus-visible:outline-2 focus-visible:outline-blue-500"
                  :title="s.plan_filename">
                  <code class="cell-code">{{ s.plan_filename }}</code>
                </router-link>
                <span v-else class="text-slate-300 text-xs">-</span>
              </td>
              <td class="text-slate-400 text-xs">{{ s.last_seen }}</td>
            </tr>
          </tbody>
        </table>
        <p v-else class="empty-state">No sessions yet.</p>
      </Card>

      <Card :no-padding="true">
        <div class="card-group-header">
          <h2 class="card-group-title">Per-rule summary</h2>
        </div>
        <table v-if="stats.length" class="tbl">
          <thead>
            <tr><th>Rule</th><th class="text-right">Checks</th><th class="text-right">Fired</th><th>Last seen</th></tr>
          </thead>
          <tbody>
            <tr v-for="s in stats" :key="s.rule_id">
              <td>
                <button type="button"
                  class="table-link bg-transparent border-0 p-0 cursor-pointer focus-visible:outline-2 focus-visible:outline-blue-500"
                  @click="filterBy('rule', s.rule_id)">
                  <code class="cell-code">{{ s.rule_id }}</code>
                </button>
              </td>
              <td class="text-right font-mono text-xs">{{ s.total }}</td>
              <td class="text-right">
                <Badge v-if="s.fired > 0" color="red" :label="String(s.fired)" />
                <span v-else class="text-slate-400 font-mono text-xs">0</span>
              </td>
              <td class="text-slate-400 text-xs">{{ s.last_seen }}</td>
            </tr>
          </tbody>
        </table>
        <p v-else class="empty-state">No trace logs yet.</p>
      </Card>
    </div>

    <Card :no-padding="true">
      <div class="card-group-header">
        <h2 class="card-group-title">Recent events</h2>
      </div>
      <table v-if="items.length" class="tbl hidden sm:table">
        <thead>
          <tr><th>When</th><th>Rule</th><th class="text-right">Matches</th><th>Severity</th><th>File</th><th>Repo</th><th>Session</th></tr>
        </thead>
        <tbody>
          <tr v-for="r in items" :key="r.id" :class="{ 'tbl-row-fired': r.triggered }">
            <td class="text-slate-400 text-xs whitespace-nowrap">{{ r.checked_at }}</td>
            <td>
              <button type="button"
                class="table-link bg-transparent border-0 p-0 cursor-pointer focus-visible:outline-2 focus-visible:outline-blue-500"
                @click="filterBy('rule', r.rule_id)">
                <code class="cell-code">{{ r.rule_id }}</code>
              </button>
            </td>
            <td class="text-right">
              <Badge v-if="r.triggered" color="red" :label="String(r.match_count)" />
              <span v-else class="text-slate-400 font-mono text-xs">0</span>
            </td>
            <td class="text-xs">{{ r.severity || '' }}</td>
            <td class="max-w-xs"><code class="cell-code block truncate" :title="r.file_path">{{ r.file_path }}</code></td>
            <td class="text-xs text-slate-500">{{ r.repo || '' }}</td>
            <td>
              <button v-if="r.session_id" type="button"
                class="table-link bg-transparent border-0 p-0 cursor-pointer focus-visible:outline-2 focus-visible:outline-blue-500"
                @click="filterBy('session', r.session_id)">
                <code class="cell-code">{{ r.session_id.slice(0, 8) }}</code>
              </button>
              <span v-else class="text-slate-300">-</span>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-if="!items.length" class="empty-state">No events match the current filter.</p>
      <CursorControls
        v-if="items.length"
        :count="items.length"
        :has-next="hasNext"
        :loading-more="loadingMore"
        label="events"
        @load-more="loadMore"
      />
    </Card>
  </div>
</template>

<style scoped>
.tbl-row-fired { background: rgba(254, 226, 226, 0.4); }

/* Make each table's column header pin under the sticky page header
   while scrolling. Card defaults to overflow-x-auto which would trap
   the sticky inside the card; override to visible so sticky resolves
   to `.content-scroll`. Subtract the .content-scroll padding-top
   (1rem / 1.5rem) so the thead sits flush with the page header's
   bottom edge. */
.sticky-page-root :deep(.card) {
  overflow: visible !important;
}
.sticky-page-root :deep(.tbl > thead > tr > th) {
  position: sticky;
  top: calc(var(--regin-trace-header-h, 0px) - 1rem);
  z-index: 5;
  background: #f8fafc;
}
@media (min-width: 1024px) {
  .sticky-page-root :deep(.tbl > thead > tr > th) {
    top: calc(var(--regin-trace-header-h, 0px) - 1.5rem);
  }
}
</style>
