<script setup>
import MarkdownContent from '../../MarkdownContent.vue'
import { fmtClock, fmtDuration, fullLabel } from '../../../utils/traceFormatters.js'
import { useCopy } from '../../../composables/useCopy.js'

defineProps({
  span: { type: Object, required: true },
  selectedSpan: { type: Object, default: null },
})
defineEmits(['activate'])
const { copyText } = useCopy()
</script>

<template>
  <div
    class="group rounded-md border bg-slate-50 border-slate-200 px-3 py-2 cursor-pointer hover:border-slate-300 transition-colors"
    :class="selectedSpan && selectedSpan.span_id === span.span_id ? 'ring-2 ring-slate-300' : ''"
    @click="$emit('activate', span)"
  >
    <div class="flex items-center gap-2 text-[11px] font-mono text-slate-500 mb-1">
      <span class="font-semibold uppercase tracking-wider text-[10px]">ASSISTANT</span>
      <span class="text-slate-300">·</span>
      <span>{{ fmtClock(span.start_time) }}</span>
      <span
        v-if="span.duration_ms"
        class="text-slate-400"
        :title="span.attributes?.turn_total_duration_ms
          ? `inference ${fmtDuration(span.duration_ms)}, whole turn ${fmtDuration(span.attributes.turn_total_duration_ms)}`
          : `inference ${fmtDuration(span.duration_ms)}`"
      >· {{ fmtDuration(span.duration_ms) }}</span>
      <span v-if="span.attributes?.truncated" class="text-amber-700 bg-amber-50 border border-amber-200 px-1 rounded text-[10px]">truncated</span>
      <button
        v-if="span.attributes?.text"
        type="button"
        class="ml-auto opacity-0 group-hover:opacity-100 transition-opacity px-1.5 py-0.5 rounded text-[10px] text-slate-500 hover:bg-slate-200/60 focus-visible:outline-2 focus-visible:outline-slate-400"
        title="Copy"
        @click.stop="copyText(span.attributes.text)"
      >Copy</button>
    </div>
    <div class="text-[13.5px] text-slate-800">
      <MarkdownContent v-if="span.attributes?.text" :markdown="span.attributes.text" />
      <span v-else class="text-slate-500">{{ fullLabel(span) }}</span>
    </div>
  </div>
</template>
