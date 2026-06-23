<script setup>
// Right pane: the selected taxonomy node, re-prioritized. Sticky header with a
// breadcrumb (ancestors → current, each crumb re-selects) + meta chips; then
// dividered sections ordered by importance — Memories (primary), Related
// topics (from edges[]), Source refs, and a collapsible Wiki narrative.
// Fetch/loading/error state is owned by the parent and passed in.
import { computed, ref, watch } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import Select from '../ui/Select.vue'
import Card from '../Card.vue'
import MarkdownContent from '../MarkdownContent.vue'
import MemoryHeadline from './MemoryHeadline.vue'

const props = defineProps({
  detail: { type: Object, default: null },
  ancestors: { type: Array, default: () => [] }, // [{id,label}] root→parent
  nodes: { type: Object, default: () => ({}) },  // for resolving edge labels
  topics: { type: Array, default: () => [] },    // [{id,label}] file-a-memory picker
  selectedId: { type: String, default: null },
  orphanId: { type: String, default: '__orphaned__' },
  loading: { type: Boolean, default: false },
  error: { type: String, default: '' },
})
const emit = defineEmits(['select-memory', 'select-node', 'topics-changed'])

const edges = computed(() => props.detail?.edges || [])
const refs = computed(() => props.detail?.refs || [])
const memories = computed(() => props.detail?.memories || [])
const labelOf = (id) => props.nodes[id]?.label || id

// Per-row picker selection ({memoryId: pendingNodeId}) and a busy guard so a
// double-click can't fire two links. Cleared whenever the node changes.
const pick = ref({})
const busy = ref('')
const canUnlink = computed(() =>
  props.selectedId && props.selectedId !== props.orphanId)

async function assign(memoryId) {
  const nodeId = pick.value[memoryId]
  if (!nodeId || busy.value) return
  busy.value = memoryId
  try {
    await api.post(`/memory/${memoryId}/topics`, { node_id: nodeId })
    pick.value = { ...pick.value, [memoryId]: '' }
    emit('topics-changed', { from: props.selectedId, to: nodeId })
  } finally {
    busy.value = ''
  }
}

async function unlink(memoryId) {
  if (busy.value || !canUnlink.value) return
  busy.value = memoryId
  try {
    await api.del(`/memory/${memoryId}/topics/${props.selectedId}`)
    emit('topics-changed', { from: props.selectedId, to: null })
  } finally {
    busy.value = ''
  }
}

// Collapse the wiki by default when there are memories to read first.
const wikiOpen = ref(false)
watch(() => props.detail?.id, () => {
  wikiOpen.value = !memories.value.length
  pick.value = {}
})
</script>

<template>
  <div class="flex h-full flex-col min-w-0">
    <p v-if="!selectedId" class="text-sm text-fg-faint pt-1">
      Pick a topic to read its wiki, source refs, and the memories filed under it.
    </p>
    <p v-else-if="loading" class="text-sm text-fg-faint pt-1">Loading…</p>
    <p v-else-if="error" class="text-sm text-danger pt-1">{{ error }}</p>

    <Card v-else-if="detail" class="flex h-full flex-col overflow-hidden p-0">
      <!-- Sticky header: breadcrumb + title + meta chips -->
      <header class="shrink-0 border-b border-border-subtle bg-surface px-4 pt-3 pb-2.5">
        <nav v-if="ancestors.length" class="flex items-center flex-wrap gap-x-1 gap-y-0.5 mb-1.5 text-xs text-fg-subtle" aria-label="Breadcrumb">
          <template v-for="a in ancestors" :key="a.id">
            <Button
              variant="link"
              class="text-xs text-fg-subtle hover:text-primary font-normal focus-visible:outline-2 focus-visible:outline-ring focus-visible:outline-offset-2"
              @click="emit('select-node', a.id)"
            >{{ a.label }}</Button>
            <Icon name="chevron-right" :size="12" class="text-fg-faint" />
          </template>
          <span class="text-fg-muted font-medium">{{ detail.label }}</span>
        </nav>

        <div class="flex items-start gap-2">
          <h3 class="text-base font-semibold text-fg leading-snug min-w-0">{{ detail.label }}</h3>
          <code class="shrink-0 text-[11px] text-fg-faint mt-0.5">{{ detail.id }}</code>
        </div>
        <p v-if="detail.blurb" class="text-sm text-fg-muted leading-relaxed mt-1">{{ detail.blurb }}</p>

        <div class="flex items-center flex-wrap gap-1.5 mt-2 text-[11px]">
          <span class="inline-flex items-center gap-1 rounded bg-surface-2 text-fg-muted px-1.5 py-0.5 font-mono tabular-nums">
            {{ detail.memory_total }} memories
          </span>
          <span class="inline-flex items-center gap-1 rounded bg-surface-2 text-fg-muted px-1.5 py-0.5 font-mono tabular-nums">
            {{ refs.length }} refs
          </span>
          <span
            class="inline-flex items-center gap-1 rounded px-1.5 py-0.5 font-mono"
            :class="detail.wiki?.exists ? 'bg-success-soft text-success-strong' : 'bg-surface-2 text-fg-faint'"
          >
            <Icon :name="detail.wiki?.exists ? 'check' : 'x'" :size="11" /> wiki
          </span>
        </div>
      </header>

      <!-- Scrollable body -->
      <div class="flex-1 overflow-y-auto px-4 py-3 space-y-5">
        <!-- Memories — the primary payload, anchored first -->
        <section>
          <h4 class="flex items-baseline gap-2 text-xs font-semibold uppercase tracking-wider text-fg-subtle mb-2">
            Memories <span class="font-mono text-fg-faint normal-case tracking-normal">{{ detail.memory_total }}</span>
          </h4>
          <ul v-if="memories.length" class="space-y-1.5">
            <li v-for="m in memories" :key="m.id">
              <MemoryHeadline :memory="m" @select="id => emit('select-memory', id)" />
              <!-- file-a-memory controls — wrap, never modify, the headline -->
              <div class="flex items-center flex-wrap gap-1.5 mt-1 pl-2.5">
                <Select
                  :model-value="pick[m.id] || ''"
                  :options="topics"
                  placeholder="File under topic…"
                  :disabled="busy === m.id"
                  class="text-xs h-7 py-0!"
                  :aria-label="`File ${m.title || m.kind} under a topic`"
                  @update:model-value="v => pick = { ...pick, [m.id]: v }"
                />
                <Button
                  variant="secondary" size="sm"
                  class="h-7 gap-1 focus-visible:outline-2 focus-visible:outline-ring focus-visible:outline-offset-2"
                  :disabled="!pick[m.id] || busy === m.id"
                  @click="assign(m.id)"
                >
                  <Icon name="plus" :size="12" /> File
                </Button>
                <Button
                  v-if="canUnlink"
                  variant="ghost" size="sm"
                  class="h-7 gap-1 text-fg-faint hover:text-danger focus-visible:outline-2 focus-visible:outline-ring focus-visible:outline-offset-2"
                  :disabled="busy === m.id"
                  :title="`Unfile from ${detail.label}`"
                  @click="unlink(m.id)"
                >
                  <Icon name="x" :size="12" /> Unfile here
                </Button>
              </div>
            </li>
          </ul>
          <p v-else class="text-sm text-fg-faint">No memories filed under this topic yet.</p>
        </section>

        <!-- Related topics — surfaced from edges[], previously unused -->
        <section v-if="edges.length" class="border-t border-border-subtle pt-4">
          <h4 class="text-xs font-semibold uppercase tracking-wider text-fg-subtle mb-2">Related topics</h4>
          <div class="flex flex-wrap gap-1.5">
            <Button
              v-for="e in edges"
              :key="e.target"
              variant="secondary"
              size="sm"
              class="h-auto py-1 gap-1.5 focus-visible:outline-2 focus-visible:outline-ring focus-visible:outline-offset-2"
              :title="e.type"
              @click="emit('select-node', e.target)"
            >
              <Icon name="arrow-up-right" :size="12" class="text-fg-faint" />
              {{ labelOf(e.target) }}
            </Button>
          </div>
        </section>

        <!-- Source refs, role-tagged -->
        <section v-if="refs.length" class="border-t border-border-subtle pt-4">
          <h4 class="text-xs font-semibold uppercase tracking-wider text-fg-subtle mb-2">Source refs</h4>
          <ul class="space-y-1">
            <li v-for="(r, i) in refs" :key="i" class="flex items-baseline gap-2 text-xs">
              <code class="text-fg-muted truncate">{{ r.path }}</code>
              <span v-if="r.role" class="shrink-0 text-[10px] uppercase tracking-wide rounded bg-surface-2 text-fg-subtle px-1.5 py-0.5">{{ r.role }}</span>
            </li>
          </ul>
        </section>

        <!-- Curated wiki narrative — collapsible -->
        <section class="border-t border-border-subtle pt-4">
          <Button
            variant="ghost"
            class="flex w-full items-center justify-between gap-2 h-auto px-0 py-0 font-normal hover:bg-transparent focus-visible:outline-2 focus-visible:outline-ring focus-visible:outline-offset-2"
            :aria-expanded="wikiOpen"
            @click="wikiOpen = !wikiOpen"
          >
            <span class="text-xs font-semibold uppercase tracking-wider text-fg-subtle">Wiki</span>
            <Icon :name="wikiOpen ? 'chevron-down' : 'chevron-right'" :size="14" class="text-fg-faint" />
          </Button>
          <div v-if="wikiOpen" class="mt-2">
            <MarkdownContent v-if="detail.wiki?.body" :markdown="detail.wiki.body" class="text-sm" />
            <p v-else class="text-sm text-fg-faint">
              No curated wiki page — <code class="text-[11px]">{{ detail.wiki?.path }}</code> doesn't exist
              (buckets and un-accepted topics have none).
            </p>
          </div>
        </section>
      </div>
    </Card>
  </div>
</template>
