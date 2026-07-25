<script setup>
import { computed } from 'vue'
import { fmtDuration, isEmptyThinkingSpan } from '../../../utils/traceFormatters.js'

const props = defineProps({
  span: { type: Object, required: true },
  selectedSpan: { type: Object, default: null },
})
defineEmits(['activate'])

const empty = computed(() => isEmptyThinkingSpan(props.span))
const selected = computed(() =>
  !!(props.selectedSpan && props.selectedSpan.span_id === props.span.span_id))
</script>

<template>
  <!-- Two shapes, because a thinking span usually has NO captured text. An
       empty one is a duration and nothing else, so it collapses to a single
       muted line: a full bordered card with a coloured label spent a block of
       vertical rhythm advertising a payload that isn't there, and thinking
       spans are frequent enough that the feed became mostly filler. The card
       is reserved for the case where there is something to read. -->
  <div
    v-if="empty"
    class="flex items-baseline gap-2 rounded border border-transparent px-2.5 py-0.5 cursor-pointer hover:bg-slate-50 focus-visible:outline-2 focus-visible:outline-blue-500"
    :class="selected ? 'event-selected' : ''"
    tabindex="0"
    @click="$emit('activate', span)"
    @keydown.enter="$emit('activate', span)"
    @keydown.space.prevent="$emit('activate', span)"
  >
    <span class="text-[12px] text-slate-400">thought</span>
    <span
      v-if="span.duration_ms"
      class="ml-auto shrink-0 font-mono text-[10.5px] text-slate-500"
    >{{ fmtDuration(span.duration_ms) }}</span>
  </div>
  <div
    v-else
    class="group rounded-lg border border-slate-200 bg-slate-50/40 px-3 py-2.5 cursor-pointer hover:border-slate-300 transition-colors"
    :class="selected ? 'event-selected' : ''"
    @click="$emit('activate', span)"
  >
    <div class="flex items-center gap-2 mb-1.5">
      <span class="font-semibold uppercase tracking-wider text-[10px] text-amber-700">Thinking</span>
      <span
        v-if="span.duration_ms"
        class="ml-auto font-mono text-[10.5px] text-slate-400"
      >{{ fmtDuration(span.duration_ms) }}</span>
    </div>
    <div
      class="text-[12.5px] text-slate-500 italic whitespace-pre-wrap break-words leading-relaxed max-h-72 overflow-y-auto"
    >{{ span.attributes.thinking_text }}</div>
  </div>
</template>
