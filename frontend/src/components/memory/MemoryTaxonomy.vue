<script setup>
// The authoritative topic taxonomy (.regin/topics/topic.json) as a navigable
// surface — the WebUI mirror of the index_root / index_expand / index_fetch
// MCP walk. Orchestrates two interchangeable left views (an accessible
// keyboard tree and a radial graph) against a shared, independently-scrolling
// detail pane. A node's memories/refs/wiki/related-topics hang off the right;
// clicking a memory emits `select` so the parent view surfaces it in the
// Memories tab (unchanged contract).
import { computed, ref } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import Input from '../ui/Input.vue'
import Tabs from '../ui/Tabs.vue'
import { useResizablePanel } from '../../composables/useResizablePanel'
import TaxonomyTree from './TaxonomyTree.vue'
import TaxonomyGraph from './TaxonomyGraph.vue'
import TaxonomyDetail from './TaxonomyDetail.vue'

const emit = defineEmits(['select'])

const VIEW_TABS = [{ label: 'Tree', value: 'tree' }, { label: 'Graph', value: 'graph' }]
const viewMode = ref('tree')
const filter = ref('')
const collapsed = ref(false)
const { width, onResizeStart, onResizeKey } =
  useResizablePanel('regin_memory_taxonomy_width', { min: 240, max: 600, def: 340 })

const ORPHAN_ID = '__orphaned__'
const roots = ref([])
const nodes = ref({})
const loading = ref(false)
const loadError = ref('')

// File-a-memory picker options: every real topic node (the synthetic
// Orphaned bucket is never a valid assign target).
const topicOptions = computed(() =>
  Object.values(nodes.value)
    .filter(n => n.id !== ORPHAN_ID)
    .map(n => ({ value: n.id, label: n.label }))
    .sort((a, b) => a.label.localeCompare(b.label)))

const selectedId = ref(null)
const detail = ref(null)
const detailLoading = ref(false)
const detailError = ref('')
const detailCache = {}

async function reload() {
  loading.value = true
  loadError.value = ''
  try {
    const data = await api.get('/memory/taxonomy')
    roots.value = data.roots || []
    nodes.value = data.nodes || {}
    for (const k in detailCache) delete detailCache[k]
  } catch (e) {
    loadError.value = e?.message || 'Failed to load the topic taxonomy.'
  } finally {
    loading.value = false
  }
}

// child → parent, for the detail-pane breadcrumb.
const parentOf = computed(() => {
  const p = {}
  for (const id in nodes.value) for (const c of nodes.value[id].children || []) p[c] = id
  return p
})
const ancestors = computed(() => {
  const chain = []
  let cur = parentOf.value[selectedId.value]
  while (cur) {
    chain.unshift({ id: cur, label: nodes.value[cur]?.label || cur })
    cur = parentOf.value[cur]
  }
  return chain
})

async function selectNode(id) {
  selectedId.value = id
  detailError.value = ''
  if (detailCache[id]) { detail.value = detailCache[id]; return }
  detail.value = null
  detailLoading.value = true
  try {
    const d = await api.get(`/memory/taxonomy/${id}`)
    detailCache[id] = d
    detail.value = d
  } catch (e) {
    detailError.value = e?.message || 'Failed to load this topic.'
  } finally {
    detailLoading.value = false
  }
}

// A manual file/unfile changed the graph: drop the affected nodes' cached
// detail (source + target both moved a memory), refresh the tree counts, and
// re-open the current node so it reflects the change immediately.
async function onTopicsChanged({ from, to }) {
  for (const id of [from, to]) if (id && detailCache[id]) delete detailCache[id]
  await reload()
  if (selectedId.value) await selectNode(selectedId.value)
}

reload()
defineExpose({ reload })
</script>

<template>
  <section class="flex flex-col gap-3">
    <!-- header strip: title · view toggle · filter · collapse -->
    <div class="flex items-center flex-wrap gap-3">
      <div class="flex items-baseline gap-2">
        <h2 class="text-sm font-semibold text-fg">Taxonomy</h2>
        <span class="text-[11px] text-fg-faint font-mono">{{ roots.length }} buckets</span>
      </div>
      <Tabs v-model="viewMode" :tabs="VIEW_TABS" variant="segmented" />
      <div class="relative flex-1 min-w-[11rem] max-w-xs">
        <Icon name="search" :size="14" class="absolute left-2.5 top-1/2 -translate-y-1/2 text-fg-faint pointer-events-none" />
        <Input v-model="filter" placeholder="Filter topics…" class="pl-8!" />
      </div>
      <Button
        variant="ghost" size="sm"
        class="gap-1.5 focus-visible:outline-2 focus-visible:outline-ring focus-visible:outline-offset-2"
        :aria-label="collapsed ? 'Show navigator' : 'Hide navigator'"
        @click="collapsed = !collapsed"
      >
        <Icon :name="collapsed ? 'chevron-right' : 'chevron-left'" :size="14" />
        {{ collapsed ? 'Show' : 'Hide' }}
      </Button>
    </div>

    <p v-if="loading" class="text-sm text-fg-faint">Loading…</p>
    <p v-else-if="loadError" class="text-sm text-danger">{{ loadError }}</p>
    <p v-else-if="!roots.length" class="text-sm text-fg-faint">
      No approved topic graph (<code class="text-[12px]">.regin/topics/topic.json</code>).
    </p>

    <!-- workspace: independently-scrolling panes, resizable divider -->
    <div v-else class="flex items-stretch h-[calc(100vh-15rem)] min-h-[26rem]">
      <div
        v-show="!collapsed"
        class="relative shrink-0 flex flex-col min-h-0"
        :style="{ width: width + 'px' }"
      >
        <div class="flex-1 min-h-0" :class="viewMode === 'tree' ? 'overflow-y-auto pr-1' : 'overflow-hidden'">
          <TaxonomyTree
            v-if="viewMode === 'tree'"
            :roots="roots" :nodes="nodes" :selected-id="selectedId" :filter="filter"
            @select="selectNode"
          />
          <TaxonomyGraph
            v-else
            :roots="roots" :nodes="nodes" :selected-id="selectedId" :filter="filter"
            @select="selectNode"
          />
        </div>
      </div>

      <!-- drag handle (←/→ when focused) -->
      <Button
        v-show="!collapsed"
        variant="ghost"
        class="group relative shrink-0 w-3 h-auto self-stretch p-0 cursor-col-resize hover:bg-transparent focus-visible:outline-2 focus-visible:outline-ring rounded-none"
        aria-label="Resize navigator"
        @mousedown="onResizeStart"
        @keydown="onResizeKey"
      >
        <span class="block mx-auto w-px h-full bg-border group-hover:bg-primary transition-colors motion-reduce:transition-none" />
      </Button>

      <div class="flex-1 min-w-0 min-h-0">
        <TaxonomyDetail
          :detail="detail"
          :ancestors="ancestors"
          :nodes="nodes"
          :topics="topicOptions"
          :selected-id="selectedId"
          :orphan-id="ORPHAN_ID"
          :loading="detailLoading"
          :error="detailError"
          @select-memory="id => emit('select', id)"
          @select-node="selectNode"
          @topics-changed="onTopicsChanged"
        />
      </div>
    </div>
  </section>
</template>
