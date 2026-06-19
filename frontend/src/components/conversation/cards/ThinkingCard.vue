<script setup>
import { fmtClock } from '../../../utils/traceFormatters.js'

defineProps({
  span: { type: Object, required: true },
  selectedSpan: { type: Object, default: null },
})
defineEmits(['activate'])
</script>

<template>
  <!-- Extended-thinking card (thinking-only turns) — intentionally muted so it
       recedes behind the important content rather than competing with it. -->
  <div
    class="group rounded-md border border-slate-200 bg-slate-50/40 px-3 py-2 cursor-pointer hover:border-slate-300 transition-colors"
    :class="selectedSpan && selectedSpan.span_id === span.span_id ? 'ring-2 ring-slate-300' : ''"
    @click="$emit('activate', span)"
  >
    <div class="flex items-center gap-2 text-[11px] font-mono text-slate-400 mb-1">
      <span class="font-semibold uppercase tracking-wider text-[10px]">THINKING</span>
      <span class="text-slate-300">·</span>
      <span>{{ fmtClock(span.start_time) }}</span>
    </div>
    <div
      v-if="span.attributes?.thinking_text"
      class="text-[12.5px] text-slate-500 italic whitespace-pre-wrap break-words leading-relaxed max-h-72 overflow-y-auto"
    >{{ span.attributes.thinking_text }}</div>
    <div v-else class="text-[12px] text-slate-400 italic">reasoned (text not captured)</div>
  </div>
</template>
