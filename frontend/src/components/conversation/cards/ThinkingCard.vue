<script setup>
import { fmtClock } from '../../../utils/traceFormatters.js'

defineProps({
  span: { type: Object, required: true },
  selectedSpan: { type: Object, default: null },
})
defineEmits(['activate'])
</script>

<template>
  <!-- Extended-thinking card (thinking-only turns) -->
  <div
    class="group rounded-md border border-violet-200 bg-violet-50/40 px-3 py-2 cursor-pointer hover:border-violet-300 transition-colors"
    :class="selectedSpan && selectedSpan.span_id === span.span_id ? 'ring-2 ring-violet-300' : ''"
    @click="$emit('activate', span)"
  >
    <div class="flex items-center gap-2 text-[11px] font-mono text-violet-500 mb-1">
      <span class="font-semibold uppercase tracking-wider text-[10px]">THINKING</span>
      <span class="text-violet-300">·</span>
      <span>{{ fmtClock(span.start_time) }}</span>
    </div>
    <div
      v-if="span.attributes?.thinking_text"
      class="text-[12.5px] text-violet-900/80 italic whitespace-pre-wrap break-words leading-relaxed max-h-72 overflow-y-auto"
    >{{ span.attributes.thinking_text }}</div>
    <div v-else class="text-[12px] text-violet-500 italic">reasoned (text not captured)</div>
  </div>
</template>
