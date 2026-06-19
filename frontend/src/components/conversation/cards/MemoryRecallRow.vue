<script setup>
import { computed, ref } from 'vue'
import { fmtClock, dotColor } from '../../../utils/traceFormatters.js'
import Button from '../../ui/Button.vue'

// memory.recall row: the <recalled_experience> block regin injected into
// this prompt. The compact row shows how many memories were recalled and
// their titles as chips; a `block` toggle reveals the exact injected text
// inline (it's what the model actually received). Selecting the row also
// opens the full per-hit list in the Span details side panel.
//
// Layout caveat mirrors RuleCheckRow: the text content sits in ONE flex
// child so drag-selection copy doesn't inject stray newlines between
// flex items.
const props = defineProps({
  span: { type: Object, required: true },
  selectedSpan: { type: Object, default: null },
})
const emit = defineEmits(['activate'])

const expanded = ref(false)
const hits = computed(() => props.span.attributes?.hits || [])
const hitCount = computed(() =>
  props.span.attributes?.hit_count ?? hits.value.length)
const block = computed(() => props.span.attributes?.block || '')
// A routed authoritative topic, when the prompt matched one. May be the
// only thing injected (topic-only route → hit_count 0).
const topic = computed(() => props.span.attributes?.topic || null)

function onRowClick() {
  if (typeof window !== 'undefined') {
    const sel = window.getSelection?.()
    if (sel && sel.toString().length > 0) return
  }
  emit('activate', props.span)
}

// Reveal the injected text inline. Also fire `activate` so the lazy
// content fetch hydrates `block` if this span hasn't been selected yet.
function toggleBlock(e) {
  e.stopPropagation()
  expanded.value = !expanded.value
  if (expanded.value && !block.value) emit('activate', props.span)
}
</script>

<template>
  <div class="pl-3">
    <div
      tabindex="0"
      class="flex items-center gap-2 text-xs cursor-pointer rounded px-2 py-1 -mx-2 hover:bg-slate-50 focus-visible:outline-2 focus-visible:outline-blue-500"
      :class="selectedSpan && selectedSpan.span_id === span.span_id ? 'bg-blue-50' : ''"
      @click="onRowClick"
    >
      <span class="inline-block w-1.5 h-1.5 rounded-full shrink-0" :class="dotColor(span.name)"></span>
      <span class="font-mono text-[11px] text-slate-400 shrink-0 cursor-text select-text">{{ fmtClock(span.start_time) }}</span>
      <div class="flex-1 min-w-0 truncate cursor-text select-text whitespace-nowrap">
        <span class="text-fuchsia-700">recalled experience</span>
        {{ ' ' }}<span class="text-slate-500">{{ hitCount }} {{ hitCount === 1 ? 'memory' : 'memories' }}</span>
        {{ ' ' }}<span
          v-if="topic"
          class="font-mono text-[10px] text-indigo-700 bg-indigo-50 border border-indigo-200 px-1 rounded"
          :title="topic.label || ''"
        >topic: {{ topic.id }}</span>
        {{ ' ' }}<template v-for="(h, i) in hits" :key="i"
          ><span
            class="font-mono text-[10px] text-fuchsia-700 bg-fuchsia-50 border border-fuchsia-200 px-1 rounded"
            :title="`${h.kind || 'memory'}${h.score != null ? ` · score ${h.score}` : ''}`"
          >{{ h.title || h.kind || 'memory' }}</span>{{ ' ' }}</template>
      </div>
      <Button
        variant="ghost"
        size="sm"
        class="font-mono text-[11px] text-slate-400 shrink-0 hover:bg-transparent hover:text-slate-600"
        :title="expanded ? 'hide injected block' : 'show injected block'"
        @click="toggleBlock"
      >{{ expanded ? '▾ block' : '▸ block' }}</Button>
    </div>
    <pre
      v-if="expanded"
      class="mt-1 ml-5 mb-1 p-2 text-[11px] leading-snug bg-slate-50 border border-slate-200 rounded whitespace-pre-wrap break-words text-slate-700 max-h-80 overflow-auto select-text"
    >{{ block || '(injected text not loaded — select the row)' }}</pre>
  </div>
</template>
