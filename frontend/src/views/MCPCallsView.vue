<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import Card from '../components/Card.vue'
import Badge from '../components/Badge.vue'
import CursorControls from '../components/CursorControls.vue'
import Checkbox from '../components/ui/Checkbox.vue'
import { useCursor } from '../composables/useCursor'
import { useStickyHeader } from '../composables/useStickyHeader'

const TEST_TOGGLE_KEY = 'regin_mcpcalls_show_tests'
const route = useRoute()
const router = useRouter()
const showTests = ref(localStorage.getItem(TEST_TOGGLE_KEY) === '1')

const {
  items, extras, loading, loadingMore, hasNext,
  load, loadMore,
} = useCursor({
  path: '/mcp-calls',
  size: 100,
  buildQuery: () => ({
    tool: route.query.tool,
    session: route.query.session,
    include_tests: showTests.value ? 'true' : undefined,
  }),
})

const { stickyHeaderEl, stickyHeaderHeight } = useStickyHeader(loading)

const toolFilter = computed(() => extras.value.tool_filter || null)
const sessionFilter = computed(() => extras.value.session_filter || null)
const stats = computed(() => extras.value.stats || [])
const sessions = computed(() => extras.value.sessions || [])

onMounted(load)
watch(() => route.query, load)
watch(showTests, (v) => {
  localStorage.setItem(TEST_TOGGLE_KEY, v ? '1' : '0')
  load()
})

function filterBy(key, value) {
  const q = { ...route.query }
  if (value) { q[key] = value } else { delete q[key] }
  router.push({ query: q })
}

function clearFilters() {
  router.push({ query: {} })
}

function fmtDate(iso) {
  if (!iso) return '-'
  const d = new Date(iso)
  return d.toLocaleString()
}

function fmtDuration(ms) {
  if (ms == null) return '-'
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(2)}s`
}

function shortTestName(nodeid) {
  if (!nodeid) return ''
  const idx = nodeid.indexOf('::')
  return idx >= 0 ? nodeid.slice(idx + 2) : nodeid
}
</script>

<template>
  <div v-if="loading" class="empty-state">Loading MCP calls…</div>
  <div
    v-else
    class="sticky-page-root"
    :style="{ '--regin-trace-header-h': stickyHeaderHeight ? stickyHeaderHeight + 'px' : '0px' }"
  >
    <!-- Sticky page header: subtitle + filter toolbar pin to the top of
         `.content-scroll` so filter context stays visible while scrolling
         the long Recent calls table below. -->
    <div
      ref="stickyHeaderEl"
      class="sticky -top-4 lg:-top-6 z-20 bg-white -mx-4 -mt-4 px-4 pt-4 lg:-mx-8 lg:-mt-6 lg:px-8 lg:pt-6 pb-3 mb-4 border-b border-slate-200 shadow-[0_2px_4px_-2px_rgba(15,23,42,0.06)]"
    >
    <p class="page-subtitle mb-4">Traces from the PostToolUse hook (matcher <code class="cell-code">mcp__.*</code>). Every MCP tool Claude actually invoked.</p>

    <div class="toolbar">
      <Checkbox v-model="showTests" label="Show test sessions" aria-label="Show test sessions" />
      <span class="toolbar-divider"></span>
      <button type="button"
        class="filter-chip focus-visible:outline-2 focus-visible:outline-blue-500"
        :class="{ active: !toolFilter && !sessionFilter }"
        @click="clearFilters">All</button>
      <Badge v-if="toolFilter" color="yellow">
        tool = {{ toolFilter }}
        <button type="button" class="badge-x focus-visible:outline-2 focus-visible:outline-blue-500"
          aria-label="Clear tool filter" @click="filterBy('tool', null)">&times;</button>
      </Badge>
      <Badge v-if="sessionFilter" color="blue">
        session = {{ sessionFilter.slice(0, 8) }}…
        <button type="button" class="badge-x focus-visible:outline-2 focus-visible:outline-blue-500"
          aria-label="Clear session filter" @click="filterBy('session', null)">&times;</button>
      </Badge>
    </div>
    </div>
    <!-- /sticky page header -->

    <div class="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
      <Card :no-padding="true">
        <div class="px-4 py-2.5 bg-gray-50 border-b border-gray-200 font-semibold text-sm">Sessions <span class="text-gray-400 font-normal">(recent 50)</span></div>
        <table v-if="sessions.length" class="tbl">
          <thead><tr><th>Session</th><th class="text-right">Calls</th><th class="text-right">Tools</th><th>Last</th></tr></thead>
          <tbody>
            <tr v-for="s in sessions" :key="s.session_id" :class="{ 'bg-blue-50': sessionFilter === s.session_id }">
              <td>
                <span v-if="s.session_id" class="text-blue-600 hover:underline cursor-pointer" @click="filterBy('session', s.session_id)"><code class="text-xs">{{ s.session_id.slice(0, 8) }}...</code></span>
                <span v-else class="text-gray-400 text-xs">unknown</span>
                <template v-if="s.is_test">
                  <span
                    class="ml-2 inline-block rounded bg-amber-100 text-amber-800 text-[10px] font-semibold px-1.5 py-0.5 uppercase tracking-wide"
                    title="Test session (span attributes carry is_test=true)"
                  >test</span>
                  <span
                    v-if="s.test_name"
                    class="ml-1 text-xs text-gray-600 font-mono"
                    :title="s.test_name"
                  >{{ shortTestName(s.test_name) }}</span>
                </template>
              </td>
              <td class="text-right">{{ s.total }}</td>
              <td class="text-right">{{ s.tools }}</td>
              <td class="text-gray-400 text-xs">{{ fmtDate(s.last_seen) }}</td>
            </tr>
          </tbody>
        </table>
        <p v-else class="p-4 text-sm text-gray-400">No sessions yet.</p>
      </Card>

      <Card :no-padding="true">
        <div class="px-4 py-2.5 bg-gray-50 border-b border-gray-200 font-semibold text-sm">Per-tool summary</div>
        <table v-if="stats.length" class="tbl">
          <thead><tr><th>Tool</th><th class="text-right">Calls</th><th>Last seen</th></tr></thead>
          <tbody>
            <tr v-for="s in stats" :key="s.tool_name" :class="{ 'bg-blue-50': toolFilter === s.tool_name }">
              <td>
                <span class="text-blue-600 hover:underline cursor-pointer" @click="filterBy('tool', s.tool_name)">
                  <code class="text-xs break-all">{{ s.tool_name }}</code>
                </span>
              </td>
              <td class="text-right">{{ s.total }}</td>
              <td class="text-gray-400 text-xs">{{ fmtDate(s.last_seen) }}</td>
            </tr>
          </tbody>
        </table>
        <p v-else class="p-4 text-sm text-gray-400">No MCP calls yet.</p>
      </Card>
    </div>

    <Card :no-padding="true">
      <div class="px-4 py-2.5 bg-gray-50 border-b border-gray-200 font-semibold text-sm">Recent calls</div>
      <table v-if="items.length" class="tbl hidden sm:table">
        <thead><tr><th>When</th><th>Tool</th><th>Session</th><th class="text-right">Duration</th><th>Input keys</th></tr></thead>
        <tbody>
          <tr v-for="r in items" :key="r.id">
            <td class="text-gray-400 text-xs whitespace-nowrap">{{ fmtDate(r.called_at) }}</td>
            <td>
              <span class="text-blue-600 hover:underline cursor-pointer" @click="filterBy('tool', r.tool_name)">
                <code class="text-xs break-all">{{ r.tool_name }}</code>
              </span>
            </td>
            <td>
              <span v-if="r.session_id" class="text-blue-600 hover:underline cursor-pointer" @click="filterBy('session', r.session_id)"><code class="text-xs">{{ r.session_id.slice(0, 8) }}</code></span>
              <span v-else class="text-gray-300">-</span>
            </td>
            <td class="text-right text-gray-500 text-xs">{{ fmtDuration(r.duration_ms) }}</td>
            <td class="max-w-xs"><code class="text-xs text-gray-500 block truncate" :title="r.tool_input_keys">{{ r.tool_input_keys }}</code></td>
          </tr>
        </tbody>
      </table>
      <ul v-if="items.length" class="sm:hidden divide-y divide-gray-200">
        <li v-for="r in items" :key="r.id" class="p-3 text-sm">
          <div class="flex flex-wrap items-center gap-2 mb-1">
            <span class="text-blue-600 hover:underline cursor-pointer" @click="filterBy('tool', r.tool_name)">
              <code class="text-xs break-all">{{ r.tool_name }}</code>
            </span>
            <span class="ml-auto text-gray-400 text-xs">{{ fmtDate(r.called_at) }}</span>
          </div>
          <div class="text-xs text-gray-500 flex flex-wrap gap-x-3">
            <span>{{ fmtDuration(r.duration_ms) }}</span>
            <span v-if="r.session_id" class="text-blue-600 hover:underline cursor-pointer" @click="filterBy('session', r.session_id)">session <code>{{ r.session_id.slice(0, 8) }}</code></span>
          </div>
          <code v-if="r.tool_input_keys" class="block mt-1 text-xs text-gray-500 break-all">{{ r.tool_input_keys }}</code>
        </li>
      </ul>
      <p v-if="!items.length" class="p-4 text-sm text-gray-400">No events match the current filter.</p>
      <CursorControls
        v-if="items.length"
        :count="items.length"
        :has-next="hasNext"
        :loading-more="loadingMore"
        label="calls"
        @load-more="loadMore"
      />
    </Card>
  </div>
</template>

<style scoped>
/* Make each table's column header pin under the sticky page header
   while scrolling. See SessionTraceView for the same pattern; the
   thead's top offset subtracts `.content-scroll`'s padding-top
   (1rem mobile / 1.5rem desktop) so it sits flush with the page
   header's bottom edge. */
.sticky-page-root :deep(.card) {
  overflow: visible !important;
}
.sticky-page-root :deep(.tbl > thead > tr > th) {
  position: sticky;
  top: calc(var(--regin-trace-header-h, 0px) - 1rem);
  z-index: 5;
  background: var(--color-slate-50);
}
@media (min-width: 1024px) {
  .sticky-page-root :deep(.tbl > thead > tr > th) {
    top: calc(var(--regin-trace-header-h, 0px) - 1.5rem);
  }
}
</style>
