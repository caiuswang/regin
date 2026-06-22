<script setup>
import { computed, ref, onMounted } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import Card from '../Card.vue'
import MarkdownContent from '../MarkdownContent.vue'

// The authoritative topic taxonomy (.regin/topics/topic.json) as a navigable
// tree — the WebUI mirror of the index_root / index_expand / index_fetch MCP
// walk. Left rail: the parent_id tree, coarse-to-fine. Right pane: the
// selected node's blurb, wiki narrative, source refs, and the subtree
// memories that hang off it. Clicking a memory emits `select` so the parent
// view surfaces it in the Memories tab.
const emit = defineEmits(['select'])

const roots = ref([])
const nodes = ref({})
const expanded = ref(new Set())
const selectedId = ref(null)
const detail = ref(null)
const loading = ref(false)
const loadError = ref('')
const detailLoading = ref(false)
const detailError = ref('')

async function reload() {
  loading.value = true
  loadError.value = ''
  try {
    const data = await api.get('/memory/taxonomy')
    roots.value = data.roots || []
    nodes.value = data.nodes || {}
    expanded.value = new Set(roots.value) // open the top level by default
  } catch (e) {
    loadError.value = e?.message || 'Failed to load the topic taxonomy.'
  } finally {
    loading.value = false
  }
}

// Flatten the expanded tree into an indented row list — depth ≤4, so a plain
// walk beats a recursive component here.
const rows = computed(() => {
  const out = []
  const walk = (id, depth) => {
    const n = nodes.value[id]
    if (!n) return
    out.push({ ...n, depth })
    if (expanded.value.has(id)) (n.children || []).forEach((c) => walk(c, depth + 1))
  }
  roots.value.forEach((r) => walk(r, 0))
  return out
})

function toggle(id) {
  const next = new Set(expanded.value)
  next.has(id) ? next.delete(id) : next.add(id)
  expanded.value = next
}

async function select(id) {
  selectedId.value = id
  detailLoading.value = true
  detailError.value = ''
  detail.value = null
  try {
    detail.value = await api.get(`/memory/taxonomy/${id}`)
  } catch (e) {
    detailError.value = e?.message || 'Failed to load this topic.'
  } finally {
    detailLoading.value = false
  }
}

// A row click both selects (loads the detail) and, for a parent, toggles it.
function onRow(row) {
  if (row.child_count) toggle(row.id)
  select(row.id)
}

const KIND_CLS = {
  lesson: 'bg-violet-100 text-violet-700',
  gotcha: 'bg-amber-100 text-amber-800',
  preference: 'bg-blue-100 text-blue-700',
  fact: 'bg-slate-100 text-slate-600',
  procedure: 'bg-cyan-100 text-cyan-700',
}
function kindCls(kind) {
  return KIND_CLS[kind] || KIND_CLS.fact
}

onMounted(reload)
defineExpose({ reload })
</script>

<template>
  <section class="flex gap-6 items-start">
    <!-- Left rail: the taxonomy tree. -->
    <div class="shrink-0 w-72 max-md:w-56">
      <div class="flex items-baseline gap-2 mb-2">
        <h2 class="text-sm font-semibold text-slate-800">Taxonomy</h2>
        <span class="text-[11px] text-slate-400 font-mono">{{ roots.length }} buckets</span>
      </div>
      <p v-if="loading" class="text-sm text-slate-400">Loading…</p>
      <p v-else-if="loadError" class="text-sm text-red-600">{{ loadError }}</p>
      <p v-else-if="!roots.length" class="text-sm text-slate-400">
        No approved topic graph (<code class="text-[12px]">.regin/topics/topic.json</code>).
      </p>
      <ul v-else class="space-y-0.5">
        <li v-for="row in rows" :key="row.id">
          <Button
            variant="ghost"
            size="sm"
            :class="['w-full justify-start gap-1.5 h-auto px-2 py-1.5 rounded-md font-normal focus-visible:outline-2 focus-visible:outline-blue-500', selectedId === row.id ? 'bg-blue-50 text-blue-700 hover:bg-blue-50' : 'text-slate-700']"
            :style="{ paddingLeft: 0.5 + row.depth * 0.85 + 'rem' }"
            @click="onRow(row)"
          >
            <span class="w-3.5 shrink-0 flex items-center justify-center" aria-hidden="true">
              <Icon
                v-if="row.child_count"
                :name="expanded.has(row.id) ? 'chevron-down' : 'chevron-right'"
                :size="13"
                class="text-slate-400"
              />
            </span>
            <span class="flex-1 min-w-0 truncate text-sm" :title="row.label">{{ row.label }}</span>
            <span
              v-if="row.mem_count"
              class="shrink-0 text-[10px] font-mono tabular-nums px-1.5 py-0.5 rounded bg-slate-100 text-slate-500"
              title="memories in this subtree"
            >{{ row.mem_count }}</span>
          </Button>
        </li>
      </ul>
    </div>

    <!-- Right pane: the selected node. -->
    <div class="flex-1 min-w-0">
      <p v-if="!selectedId" class="text-sm text-slate-400 pt-1">
        Pick a topic on the left to read its wiki, source refs, and the memories filed under it.
      </p>
      <p v-else-if="detailLoading" class="text-sm text-slate-400 pt-1">Loading…</p>
      <p v-else-if="detailError" class="text-sm text-red-600 pt-1">{{ detailError }}</p>
      <Card v-else-if="detail" class="overflow-hidden">
        <div class="p-4">
          <div class="flex items-center gap-2 mb-1">
            <h3 class="text-base font-semibold text-slate-900">{{ detail.label }}</h3>
            <code class="text-[11px] text-slate-400">{{ detail.id }}</code>
          </div>
          <p v-if="detail.blurb" class="text-sm text-slate-600 leading-relaxed mb-3">{{ detail.blurb }}</p>

          <!-- Source refs -->
          <div v-if="detail.refs.length" class="mb-4">
            <h4 class="text-xs font-semibold uppercase tracking-wider text-slate-500 mb-1.5">
              Source refs <span class="font-mono text-slate-400">{{ detail.refs.length }}</span>
            </h4>
            <ul class="space-y-0.5">
              <li v-for="(r, i) in detail.refs" :key="i" class="text-xs flex items-baseline gap-2">
                <code class="text-slate-700">{{ r.path }}</code>
                <span v-if="r.role" class="text-[10px] text-slate-400">{{ r.role }}</span>
              </li>
            </ul>
          </div>

          <!-- Memories filed under this subtree -->
          <div class="mb-4">
            <h4 class="text-xs font-semibold uppercase tracking-wider text-slate-500 mb-1.5">
              Memories <span class="font-mono text-slate-400">{{ detail.memory_total }}</span>
            </h4>
            <ul v-if="detail.memories.length" class="space-y-1">
              <li v-for="m in detail.memories" :key="m.id">
                <Button
                  variant="ghost"
                  size="sm"
                  class="w-full justify-start gap-2 h-auto px-2.5 py-1.5 rounded-md border border-slate-200 bg-white hover:border-slate-300 hover:bg-white hover:shadow-sm font-normal focus-visible:outline-2 focus-visible:outline-blue-500"
                  @click="emit('select', m.id)"
                >
                  <span
                    class="shrink-0 text-[9px] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded"
                    :class="kindCls(m.kind)"
                  >{{ m.kind }}</span>
                  <span class="flex-1 min-w-0 truncate text-sm text-slate-800">{{ m.title || m.kind }}</span>
                  <span class="shrink-0 text-[10px] font-mono text-slate-400 tabular-nums">{{ (m.importance || 0).toFixed(1) }}</span>
                </Button>
              </li>
            </ul>
            <p v-else class="text-sm text-slate-400">No memories filed under this topic yet.</p>
          </div>

          <!-- Curated wiki narrative -->
          <div>
            <h4 class="text-xs font-semibold uppercase tracking-wider text-slate-500 mb-1.5">Wiki</h4>
            <MarkdownContent v-if="detail.wiki.body" :markdown="detail.wiki.body" class="text-sm" />
            <p v-else class="text-sm text-slate-400">
              No curated wiki page — <code class="text-[11px]">{{ detail.wiki.path }}</code> doesn't exist
              (buckets and un-accepted topics have none).
            </p>
          </div>
        </div>
      </Card>
    </div>
  </section>
</template>
