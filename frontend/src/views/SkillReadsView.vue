<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import api from '../api'
import Card from '../components/Card.vue'
import Badge from '../components/Badge.vue'
import CursorControls from '../components/CursorControls.vue'
import Button from '../components/ui/Button.vue'
import Checkbox from '../components/ui/Checkbox.vue'
import { useFlash } from '../composables/useFlash'
import { useConfirm } from '../composables/useConfirm'
import { useCursor } from '../composables/useCursor'
import { useStickyHeader } from '../composables/useStickyHeader'

const TEST_TOGGLE_KEY = 'regin_skillreads_show_tests'
const route = useRoute()
const router = useRouter()
const { flash } = useFlash()
const { confirm } = useConfirm()
const showTests = ref(localStorage.getItem(TEST_TOGGLE_KEY) === '1')

const {
  items, extras, loading, loadingMore, hasNext,
  load, loadMore,
} = useCursor({
  path: '/skill-reads',
  size: 100,
  buildQuery: () => ({
    skill: route.query.skill,
    session: route.query.session,
    include_tests: showTests.value ? 'true' : undefined,
  }),
})

const { stickyHeaderEl, stickyHeaderHeight } = useStickyHeader(loading)

const skillFilter = computed(() => extras.value.skill_filter || null)
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

async function resetReads() {
  const scope = skillFilter.value ? `skill=${skillFilter.value}`
    : sessionFilter.value ? `session=${sessionFilter.value.slice(0, 8)}`
    : 'all'
  const ok = await confirm('Reset skill reads', `Delete the currently-filtered trace rows (${scope})? This cannot be undone.`, true)
  if (!ok) return
  const body = {}
  if (skillFilter.value) body.skill = skillFilter.value
  if (sessionFilter.value) body.session = sessionFilter.value
  const result = await api.post('/skill-reads/reset', body)
  if (!result.ok) { flash(result.msg || 'Failed to reset', 'error'); return }
  flash(result.msg)
  await load()
}

function fmtDate(iso) {
  if (!iso) return '-'
  const d = new Date(iso)
  return d.toLocaleString()
}

function shortTestName(nodeid) {
  if (!nodeid) return ''
  const idx = nodeid.indexOf('::')
  return idx >= 0 ? nodeid.slice(idx + 2) : nodeid
}
</script>

<template>
  <div v-if="loading" class="empty-state">Loading skill reads…</div>
  <div
    v-else
    class="sticky-page-root"
    :style="{ '--regin-trace-header-h': stickyHeaderHeight ? stickyHeaderHeight + 'px' : '0px' }"
  >
    <!-- Sticky page header: subtitle + filter toolbar pin to the top of
         `.content-scroll` so filter context stays visible while scrolling
         the long Recent events table below. -->
    <div
      ref="stickyHeaderEl"
      class="sticky -top-4 lg:-top-6 z-20 bg-white -mx-4 -mt-4 px-4 pt-4 lg:-mx-8 lg:-mt-6 lg:px-8 lg:pt-6 pb-3 mb-4 border-b border-slate-200 shadow-[0_2px_4px_-2px_rgba(15,23,42,0.06)]"
    >
    <p class="page-subtitle mb-4">Skills Claude read (<code class="cell-code">source=read</code>), explicitly invoked via slash command (<code class="cell-code">source=invoke</code>), or launched via the Skill tool (<code class="cell-code">source=launch</code>).</p>

    <div class="toolbar">
      <Checkbox v-model="showTests" label="Show test sessions" aria-label="Show test sessions" />
      <span class="toolbar-divider"></span>
      <button type="button"
        class="filter-chip focus-visible:outline-2 focus-visible:outline-blue-500"
        :class="{ active: !skillFilter && !sessionFilter }"
        @click="clearFilters">All</button>
      <Badge v-if="skillFilter" color="yellow">
        skill = {{ skillFilter }}
        <button type="button" class="badge-x focus-visible:outline-2 focus-visible:outline-blue-500"
          aria-label="Clear skill filter" @click="filterBy('skill', null)">&times;</button>
      </Badge>
      <Badge v-if="sessionFilter" color="blue">
        session = {{ sessionFilter.slice(0, 8) }}…
        <button type="button" class="badge-x focus-visible:outline-2 focus-visible:outline-blue-500"
          aria-label="Clear session filter" @click="filterBy('session', null)">&times;</button>
      </Badge>
      <Button variant="danger" size="sm" class="ml-auto" @click="resetReads">
        {{ skillFilter || sessionFilter ? 'Reset filtered' : 'Reset all' }}
      </Button>
    </div>
    </div>
    <!-- /sticky page header -->

    <div class="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
      <Card :no-padding="true">
        <div class="px-4 py-2.5 bg-gray-50 border-b border-gray-200 font-semibold text-sm">Sessions <span class="text-gray-400 font-normal">(recent 50)</span></div>
        <table v-if="sessions.length" class="tbl">
          <thead><tr><th>Session</th><th class="text-right">Reads</th><th class="text-right">Skills</th><th>Plan</th><th>Last</th></tr></thead>
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
              <td class="text-right">{{ s.skills }}</td>
              <td>
                <router-link v-if="s.plan_filename" :to="`/plans/${s.plan_filename}`" class="text-blue-600 hover:underline text-xs"
                  :title="s.plan_filename"
                >
                  <code class="text-xs">{{ s.plan_filename }}</code>
                </router-link>
                <span v-else class="text-gray-300 text-xs">-</span>
              </td>
              <td class="text-gray-400 text-xs">{{ fmtDate(s.last_seen) }}</td>
            </tr>
          </tbody>
        </table>
        <p v-else class="p-4 text-sm text-gray-400">No sessions yet.</p>
      </Card>

      <Card :no-padding="true">
        <div class="px-4 py-2.5 bg-gray-50 border-b border-gray-200 font-semibold text-sm">Per-skill summary</div>
        <table v-if="stats.length" class="tbl">
          <thead><tr><th>Skill</th><th class="text-right">Reads</th><th>Last seen</th></tr></thead>
          <tbody>
            <tr v-for="s in stats" :key="s.skill_id">
              <td><router-link :to="`/skills/${s.skill_id}`" class="text-blue-600 hover:underline"><code class="text-xs">{{ s.skill_id }}</code></router-link></td>
              <td class="text-right">{{ s.total }}</td>
              <td class="text-gray-400 text-xs">{{ fmtDate(s.last_seen) }}</td>
            </tr>
          </tbody>
        </table>
        <p v-else class="p-4 text-sm text-gray-400">No trace logs yet.</p>
      </Card>
    </div>

    <Card :no-padding="true">
      <div class="px-4 py-2.5 bg-gray-50 border-b border-gray-200 font-semibold text-sm">Recent events</div>
      <table v-if="items.length" class="tbl hidden sm:table">
        <thead><tr><th>When</th><th>Skill</th><th>Source</th><th>Session</th><th>File / Args</th></tr></thead>
        <tbody>
          <tr v-for="r in items" :key="r.id">
            <td class="text-gray-400 text-xs whitespace-nowrap">{{ fmtDate(r.read_at) }}</td>
            <td><router-link :to="`/skills/${r.skill_id}`" class="text-blue-600 hover:underline"><code class="text-xs">{{ r.skill_id }}</code></router-link></td>
            <td>
              <span v-if="r.source === 'invoke'" class="inline-block rounded bg-green-100 text-green-800 text-[10px] font-semibold px-1.5 py-0.5 uppercase tracking-wide">invoke</span>
              <span v-else-if="r.source === 'launch'" class="inline-block rounded bg-purple-100 text-purple-800 text-[10px] font-semibold px-1.5 py-0.5 uppercase tracking-wide">launch</span>
              <span v-else class="inline-block rounded bg-blue-100 text-blue-800 text-[10px] font-semibold px-1.5 py-0.5 uppercase tracking-wide">read</span>
            </td>
            <td>
              <span v-if="r.session_id" class="text-blue-600 hover:underline cursor-pointer" @click="filterBy('session', r.session_id)"><code class="text-xs">{{ r.session_id.slice(0, 8) }}</code></span>
              <span v-else class="text-gray-300">-</span>
            </td>
            <td class="max-w-xs">
              <code v-if="r.source === 'invoke' && r.command_args" class="text-xs text-gray-500 block truncate" :title="r.command_args">{{ r.command_args }}</code>
              <code v-else class="text-xs text-gray-500 block truncate" :title="r.file_path">{{ r.file_path }}</code>
            </td>
          </tr>
        </tbody>
      </table>
      <ul v-if="items.length" class="sm:hidden divide-y divide-gray-200">
        <li v-for="r in items" :key="r.id" class="p-3 text-sm">
          <div class="flex flex-wrap items-center gap-2 mb-1">
            <router-link :to="`/skills/${r.skill_id}`" class="text-blue-600 hover:underline"><code class="text-xs">{{ r.skill_id }}</code></router-link>
            <span v-if="r.source === 'invoke'" class="rounded bg-green-100 text-green-800 text-[10px] font-semibold px-1.5 py-0.5 uppercase tracking-wide">invoke</span>
            <span v-else-if="r.source === 'launch'" class="rounded bg-purple-100 text-purple-800 text-[10px] font-semibold px-1.5 py-0.5 uppercase tracking-wide">launch</span>
            <span v-else class="rounded bg-blue-100 text-blue-800 text-[10px] font-semibold px-1.5 py-0.5 uppercase tracking-wide">read</span>
            <span class="ml-auto text-gray-400 text-xs">{{ fmtDate(r.read_at) }}</span>
          </div>
          <code v-if="r.source === 'invoke' && r.command_args" class="block text-xs text-gray-600 break-all">{{ r.command_args }}</code>
          <code v-else-if="r.file_path" class="block text-xs text-gray-600 break-all">{{ r.file_path }}</code>
          <div v-if="r.session_id" class="mt-1 text-xs">
            <span class="text-blue-600 hover:underline cursor-pointer" @click="filterBy('session', r.session_id)">session <code>{{ r.session_id.slice(0, 8) }}</code></span>
          </div>
        </li>
      </ul>
      <p v-if="!items.length" class="p-4 text-sm text-gray-400">No events match the current filter.</p>
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
/* Make each table's column header pin under the sticky page header
   while scrolling. See SessionTraceView for the same pattern. */
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
