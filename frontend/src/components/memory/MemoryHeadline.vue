<script setup>
// One importance-ranked memory address in the taxonomy detail pane. The row
// click emits `select` (parent routes to the Memories tab — unchanged
// contract). A separate preview glyph opens a Popover that LAZY-FETCHES the
// full memory body (`GET /api/memory/<id>`, cached) — headlines from the
// taxonomy endpoint carry no body. Hovering the glyph prefetches so the
// popover opens instantly.
import { ref } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import Popover from '../ui/Popover.vue'
import MarkdownContent from '../MarkdownContent.vue'

const props = defineProps({
  memory: { type: Object, required: true },
})
const emit = defineEmits(['select'])

// A readable handle for a memory: its title, else a body-derived snippet
// (the taxonomy payload sends `snippet`; full memory objects carry `body`),
// else the bare kind — so a titleless memory never shows just "lesson".
function headline(m) {
  if (m.title) return m.title
  const text = (m.snippet || m.body || '').replace(/\s+/g, ' ').trim()
  return text ? text.slice(0, 80) : m.kind
}

const KIND_CLS = {
  lesson: 'bg-violet-100 text-violet-700',
  gotcha: 'bg-amber-100 text-amber-800',
  preference: 'bg-blue-100 text-blue-700',
  fact: 'bg-slate-100 text-slate-600',
  procedure: 'bg-cyan-100 text-cyan-700',
}
const kindCls = (k) => KIND_CLS[k] || KIND_CLS.fact

// Veracity → a small glyph + token color (true=check, false=×, else muted dot).
const VERACITY = {
  true: { icon: 'check', cls: 'text-success', label: 'verified true' },
  false: { icon: 'x', cls: 'text-danger', label: 'verified false' },
}
const veracity = (v) => VERACITY[v] || null

const open = ref(false)
const body = ref(null)
const loading = ref(false)
const error = ref('')
let loaded = false

async function loadBody() {
  if (loaded || loading.value) return
  loading.value = true
  error.value = ''
  try {
    const data = await api.get(`/memory/${props.memory.id}`)
    body.value = data?.memory || null
    loaded = true
  } catch (e) {
    error.value = e?.message || 'Failed to load this memory.'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div
    class="group flex items-center gap-2 rounded-md border border-border bg-surface px-2.5 py-1.5
           hover:border-border-strong hover:shadow-sm transition-[border-color,box-shadow]
           motion-reduce:transition-none"
  >
    <Button
      variant="ghost"
      class="flex flex-1 min-w-0 items-center justify-start gap-2 text-left h-auto px-0 py-0 font-normal rounded hover:bg-transparent hover:text-fg focus-visible:outline-2 focus-visible:outline-ring focus-visible:outline-offset-2"
      @click="emit('select', memory.id)"
    >
      <span
        class="shrink-0 text-[9px] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded"
        :class="kindCls(memory.kind)"
      >{{ memory.kind }}</span>
      <span class="flex-1 min-w-0 truncate text-sm" :class="memory.title ? 'text-fg' : 'text-fg-faint'">{{ headline(memory) }}</span>
    </Button>

    <!-- veracity glyph -->
    <Icon
      v-if="veracity(memory.veracity)"
      :name="veracity(memory.veracity).icon"
      :size="13"
      :class="['shrink-0', veracity(memory.veracity).cls]"
      :aria-label="veracity(memory.veracity).label"
    />

    <!-- importance: a small meter + number, so the bare score has a visual scale -->
    <span class="shrink-0 flex items-center gap-1.5" :title="`importance ${(memory.importance || 0).toFixed(1)} / 10`">
      <span class="hidden sm:block h-1 w-10 rounded-full bg-surface-3 overflow-hidden" aria-hidden="true">
        <span class="block h-full rounded-full bg-primary" :style="{ width: Math.max(0, Math.min(100, (memory.importance || 0) * 10)) + '%' }" />
      </span>
      <span class="text-[10px] font-mono text-fg-faint tabular-nums w-6 text-right">{{ (memory.importance || 0).toFixed(1) }}</span>
    </span>

    <!-- preview popover: lazy-fetches the full body -->
    <Popover v-model:open="open" side="left" align="start">
      <template #trigger>
        <Button
          variant="ghost"
          class="shrink-0 grid place-items-center h-6 w-6 p-0 rounded text-fg-faint hover:text-fg hover:bg-surface-2 focus-visible:outline-2 focus-visible:outline-ring focus-visible:outline-offset-2 motion-reduce:transition-none"
          :aria-label="`Preview memory: ${headline(memory)}`"
          @mouseenter="loadBody"
          @focus="loadBody"
          @click="open = true"
        >
          <Icon name="search" :size="13" />
        </Button>
      </template>

      <div class="w-72 max-w-[20rem]">
        <p v-if="loading" class="text-sm text-fg-faint">Loading…</p>
        <p v-else-if="error" class="text-sm text-danger">{{ error }}</p>
        <template v-else-if="body">
          <div class="flex items-center gap-2 mb-1.5">
            <span class="text-[9px] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded" :class="kindCls(body.kind)">{{ body.kind }}</span>
            <span v-if="body.scope" class="text-[10px] font-mono text-fg-subtle truncate">{{ body.scope }}</span>
          </div>
          <h5 v-if="body.title" class="text-sm font-semibold text-fg mb-1">{{ body.title }}</h5>
          <MarkdownContent v-if="body.body" :markdown="body.body" class="text-xs max-h-72 overflow-y-auto" />
          <p v-else class="text-xs text-fg-faint">No body text.</p>
        </template>
      </div>
    </Popover>
  </div>
</template>
