<script setup>
import { computed, onMounted, onBeforeUnmount, ref } from 'vue'
import api from '../api'
import { useConfirm } from '../composables/useConfirm'
import { useResizablePanel } from '../composables/useResizablePanel'
import { usePage } from '../composables/usePage'
import { useTabRoute } from '../composables/useTabRoute'
import Card from '../components/Card.vue'
import PageControls from '../components/PageControls.vue'
import Button from '../components/ui/Button.vue'
import Tabs from '../components/ui/Tabs.vue'
import MemoryCategoryBar from '../components/memory/MemoryCategoryBar.vue'
import MemoryList from '../components/memory/MemoryList.vue'
import MemoryDetail from '../components/memory/MemoryDetail.vue'
import MemoryTopics from '../components/memory/MemoryTopics.vue'
import MemoryTopicFeedback from '../components/memory/MemoryTopicFeedback.vue'
import TopicRoutePlayground from '../components/memory/TopicRoutePlayground.vue'
import MemoryExemplars from '../components/memory/MemoryExemplars.vue'

const { confirm } = useConfirm()
const { width: listWidth, onResizeStart, onResizeKey } =
  useResizablePanel('regin_memory_list_width', { min: 200, max: 640, def: 288 })

const activeCategory = ref('all')
const query = ref('')
const searchDraft = ref('')
const scope = ref('')
const includeTests = ref(false)
const selectedId = ref(null)

// Three domain-coherent tabs replace the single long scroll: browse/manage,
// topic clustering + routing, and recall-ranking tuning.
const activeTab = useTabRoute({ default: 'memories', valid: ['memories', 'topics', 'recall'] })
const TABS = [
  { value: 'memories', label: 'Memories' },
  { value: 'topics', label: 'Topics' },
  { value: 'recall', label: 'Recall' },
]

// Selecting a memory from any tab (topic member, recall hit) jumps to the
// Memories tab so its detail pane is visible.
function selectMemory(id) {
  selectedId.value = id
  activeTab.value = 'memories'
}

// The page header pins to the top of the scroll area; the list rail pins just
// below it. Header height is dynamic (filters wrap, reflect summary toggles),
// so measure it and feed the offset to the rail's sticky `top`.
const headerEl = ref(null)
const headerH = ref(0)
let headerObserver = null

const reflectSummary = ref('')
const reflecting = ref(false)
const topicsRef = ref(null)
const topicFeedbackRef = ref(null)
const playgroundRef = ref(null)

// Recent-injections 🔍 → load that prompt into the sibling route playground and
// run the preview (the playground scrolls itself into view).
function inspectInPlayground(query) {
  playgroundRef.value?.probe(query)
}
const exemplarsRef = ref(null)
const recallQuery = ref('')
const recallHits = ref(null)

function categoryParams(key) {
  if (key === 'inbox') return { status: 'proposed' }
  if (key === 'retired') return { status: 'retired' }
  if (key.startsWith('kind:')) return { status: 'active', kind: key.slice(5) }
  if (key.startsWith('tier:')) return { status: 'active', tier: key.slice(5) }
  return { status: 'active' }
}

// Offset-limit pagination over /api/memory. `stats` rides back as an extra
// on the same envelope, so the category bar stays in sync per page.
const {
  items: memories, extras, loading: busy,
  page, pageSize, total, pageCount, hasNext, hasPrev,
  load, next, prev, goto, setSize, refresh,
} = usePage({
  path: '/memory',
  size: 50,
  buildQuery: () => ({
    include_tests: String(includeTests.value),
    ...categoryParams(activeCategory.value),
    q: query.value.trim() || undefined,
    scope: scope.value || undefined,
  }),
})

const stats = computed(() => extras.value.stats || {})

// A filter change resets to page 0 and re-fetches; the selected memory may
// no longer be in scope, so drop the detail pane.
async function applyFilters() {
  selectedId.value = null
  await refresh()
}

function selectCategory(key) {
  activeCategory.value = key
  applyFilters()
}

function onSearch(value) {
  query.value = value
  applyFilters()
}

function clearSearch() {
  searchDraft.value = ''
  onSearch('')
}

function setScope(value) {
  scope.value = value
  applyFilters()
}

function toggleTests() {
  includeTests.value = !includeTests.value
  applyFilters()
}

async function runReflect() {
  reflecting.value = true
  try {
    const r = await api.post('/memory/reflect', {})
    reflectSummary.value =
      `reflect: ${r.examined} examined, ${r.merged} merged, ${r.promoted} promoted, ${r.embedded} embedded, ${r.edges} edges, ${r.topics} topics, ${r.decayed} decayed`
  } finally {
    reflecting.value = false
  }
  await load()
  topicsRef.value?.reload()
  topicFeedbackRef.value?.reload()
  exemplarsRef.value?.reload()
}

async function runRecall() {
  const q = recallQuery.value.trim()
  if (!q) return
  const data = await api.post('/memory/recall', { query: q, include_tests: includeTests.value })
  recallHits.value = data.hits || []
}

function clearRecall() {
  recallHits.value = null
  recallQuery.value = ''
}

// score_kind is a property of the whole recall (which retrieval path won),
// not per hit — every hit in one recall shares it. Surface it once, explained.
const SCORE_KIND = {
  rerank: { cls: 'bg-emerald-100 text-emerald-700', label: 'cross-encoder rerank — highest-fidelity relevance' },
  rrf: { cls: 'bg-blue-100 text-blue-700', label: 'dense + lexical RRF fusion (no reranker available)' },
  fts: { cls: 'bg-amber-100 text-amber-800', label: 'lexical FTS fallback — no embedder; weakest signal' },
}
function scoreKindMeta(kind) {
  return SCORE_KIND[kind] || { cls: 'bg-slate-100 text-slate-500', label: kind }
}

const KIND_STYLES = {
  lesson: 'bg-violet-100 text-violet-700',
  gotcha: 'bg-amber-100 text-amber-800',
  preference: 'bg-blue-100 text-blue-700',
  fact: 'bg-slate-100 text-slate-600',
  procedure: 'bg-cyan-100 text-cyan-700',
}
const KIND_BAR = {
  lesson: 'bg-violet-400',
  gotcha: 'bg-amber-400',
  preference: 'bg-blue-400',
  fact: 'bg-slate-300',
  procedure: 'bg-cyan-400',
}
function kindCls(kind) {
  return KIND_STYLES[kind] || KIND_STYLES.fact
}
function kindBar(kind) {
  return KIND_BAR[kind] || KIND_BAR.fact
}
function snippet(body) {
  const text = (body || '').replace(/[#*`>_[\]]/g, '').replace(/\s+/g, ' ').trim()
  return text.length > 160 ? `${text.slice(0, 160)}…` : text
}

async function onBulk({ action, ids }) {
  if (action === 'forget') {
    const ok = await confirm(
      'Forget', `Permanently delete ${ids.length} memories? This cannot be undone.`, true)
    if (!ok) return
  }
  await api.post('/memory/bulk', { action, ids })
  if (ids.includes(selectedId.value)) selectedId.value = null
  await load()
}

onMounted(() => {
  load()
  headerObserver = new ResizeObserver(() => {
    headerH.value = headerEl.value?.offsetHeight || 0
  })
  if (headerEl.value) headerObserver.observe(headerEl.value)
})

onBeforeUnmount(() => headerObserver?.disconnect())
</script>

<template>
  <div>
    <!-- Pinned page header. The scroll container has 1.5rem top padding (1rem
         on mobile); the sticky anchor is pulled up by that amount (-top-6 /
         -top-4) with matching pt so the white background covers the gutter and
         no scrolled content peeks above it. -->
    <div ref="headerEl" class="sticky -top-6 z-20 -mx-8 px-8 pt-6 bg-white border-b border-slate-200 max-md:-top-4 max-md:-mx-4 max-md:px-4 max-md:pt-4">
    <div class="flex items-center gap-3 mb-1">
      <h1 class="text-xl font-semibold text-slate-900">Memory</h1>
      <span class="text-xs text-slate-500 font-mono">{{ stats.total || 0 }} memories</span>
      <div class="ml-auto flex items-center gap-2">
        <Button
          :variant="includeTests ? 'primary' : 'secondary'"
          size="sm"
          @click="toggleTests"
        >Include test data</Button>
        <Button variant="secondary" size="sm" :disabled="busy || reflecting" @click="runReflect">Run reflect</Button>
      </div>
    </div>
    <p class="text-sm text-slate-500 mb-3">
      Cross-session experience captured from <code class="text-[12px]">send_to_user(type=lesson)</code>
      and session distills. Consolidated by reflect, recalled into future prompts.
    </p>
    <p v-if="reflectSummary" class="text-xs text-slate-500 font-mono mb-3">{{ reflectSummary }}</p>

    <Tabs v-model="activeTab" :tabs="TABS" variant="underline" class="-mb-px" />
    </div>

    <!-- Memories: browse, search, and manage the store. -->
    <div v-show="activeTab === 'memories'">
    <div class="mt-4 mb-3 space-y-2.5">
      <MemoryCategoryBar
        :stats="stats"
        :active="activeCategory"
        :scope="scope"
        @select="selectCategory"
        @update:scope="setScope"
      />
      <div class="flex items-center gap-2">
        <input
          v-model="searchDraft"
          type="search"
          placeholder="Search title, body, tags…"
          class="flex-1 max-w-sm min-w-40 text-sm border border-slate-200 rounded-md px-3 py-1.5 focus-visible:outline-2 focus-visible:outline-blue-500"
          @keyup.enter="onSearch(searchDraft)"
          @search="onSearch(searchDraft)"
        />
        <Button variant="secondary" size="sm" @click="onSearch(searchDraft)">Search</Button>
        <Button v-if="query" variant="secondary" size="sm" @click="clearSearch">Clear</Button>
      </div>
    </div>

    <div class="flex gap-6 items-start">
      <!-- When a memory is selected the list becomes a fixed, drag-resizable
           rail; otherwise it expands to fill the row. -->
      <div
        :class="selectedId ? 'relative shrink-0 self-start sticky' : 'flex-1 min-w-0'"
        :style="selectedId ? { width: listWidth + 'px', top: (headerH - 16) + 'px' } : null"
      >
        <!-- When pinned, the rail scrolls its own overflow so a long list stays
             reachable without growing past the viewport. The resize handle is a
             sibling of this scroll box, so it is never clipped by the overflow. -->
        <div
          :class="selectedId ? 'overflow-y-auto pr-1' : ''"
          :style="selectedId ? { maxHeight: `calc(100vh - ${headerH + 40}px)` } : null"
        >
          <MemoryList
            :expanded="!selectedId"
            :memories="memories"
            :selected-id="selectedId"
            :busy="busy || reflecting"
            @select="selectMemory"
            @bulk="onBulk"
          />
          <PageControls
            v-if="total > 0"
            class="mt-2 rounded-md border border-slate-200 overflow-hidden"
            :page="page"
            :page-count="pageCount"
            :total="total"
            :size="pageSize"
            :has-next="hasNext"
            :has-prev="hasPrev"
            :loading="busy"
            @prev="prev"
            @next="next"
            @goto="goto"
            @set-size="setSize"
          />
        </div>
        <!-- Drag handle sitting in the gutter on the rail's right edge. A
             <button> so it's keyboard focusable (←/→ resize). -->
        <button
          v-if="selectedId"
          type="button"
          class="absolute top-0 -right-3 w-3 h-full p-0 bg-transparent border-0 cursor-col-resize group z-10 select-none focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-400 rounded"
          title="Drag (or ←/→) to resize list"
          aria-label="Resize memory list"
          @mousedown="onResizeStart"
          @keydown="onResizeKey"
        >
          <span class="block mx-auto w-px h-full bg-slate-200 group-hover:bg-blue-400 transition-colors"></span>
        </button>
      </div>
      <MemoryDetail
        v-if="selectedId"
        :memory-id="selectedId"
        @changed="load"
        @navigate="id => selectedId = id"
        @close="selectedId = null"
      />
    </div>
    </div>

    <!-- Topics: reflect-synthesised clusters, their routing feedback, and a
         query → route preview. -->
    <div v-show="activeTab === 'topics'" class="pt-4 space-y-8">
      <MemoryTopics ref="topicsRef" @select="selectMemory" />
      <MemoryTopicFeedback ref="topicFeedbackRef" @inspect="inspectInPlayground" />
      <TopicRoutePlayground ref="playgroundRef" />
    </div>

    <!-- Recall: probe what a query surfaces, plus the per-memory exemplars
         that re-rank recall. -->
    <div v-show="activeTab === 'recall'" class="pt-4">
      <div class="flex flex-wrap items-center gap-2 mb-3">
        <input
          v-model="recallQuery"
          type="text"
          placeholder="Recall probe — what would a query surface?"
          class="flex-1 min-w-48 text-sm border border-slate-200 rounded-md px-3 py-1.5 focus-visible:outline-2 focus-visible:outline-blue-500"
          @keyup.enter="runRecall"
        />
        <Button variant="secondary" size="sm" @click="runRecall">Recall</Button>
        <Button v-if="recallHits != null" variant="secondary" size="sm" @click="clearRecall">Clear</Button>
      </div>

      <Card v-if="recallHits != null" class="mb-4">
        <div class="p-4">
          <div class="flex flex-wrap items-center gap-2 mb-2">
            <h2 class="text-sm font-semibold text-slate-800">Recall results</h2>
            <template v-if="recallHits.length">
              <span
                class="text-[10px] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded"
                :class="scoreKindMeta(recallHits[0].score_kind).cls"
              >{{ recallHits[0].score_kind }}</span>
              <span class="text-[11px] text-slate-400">{{ scoreKindMeta(recallHits[0].score_kind).label }}</span>
            </template>
          </div>
          <p v-if="!recallHits.length" class="text-sm text-slate-500">Nothing matched.</p>
          <ul v-else class="space-y-1.5">
            <li v-for="h in recallHits" :key="h.id">
              <button
                type="button"
                class="relative w-full text-left rounded-md border pl-3.5 pr-2.5 py-2 overflow-hidden transition-all focus-visible:outline-2 focus-visible:outline-blue-500"
                :class="selectedId === h.id ? 'border-blue-300 bg-blue-50/60' : 'border-slate-200 bg-white hover:border-slate-300 hover:shadow-sm'"
                @click="selectMemory(h.id)"
              >
                <span class="absolute inset-y-0 left-0 w-1" :class="kindBar(h.kind)" aria-hidden="true"></span>
                <div class="flex items-center gap-2 mb-0.5">
                  <span
                    class="text-[9px] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded"
                    :class="kindCls(h.kind)"
                  >{{ h.kind }}</span>
                  <span class="font-mono tabular-nums text-[10px] text-slate-400">{{ h.score.toFixed(2) }}</span>
                  <span class="text-sm font-medium text-slate-800 truncate">{{ h.title || h.kind }}</span>
                </div>
                <p class="text-xs text-slate-500 leading-snug">{{ snippet(h.body) }}</p>
              </button>
            </li>
          </ul>
        </div>
      </Card>

      <MemoryExemplars ref="exemplarsRef" />
    </div>
  </div>
</template>
