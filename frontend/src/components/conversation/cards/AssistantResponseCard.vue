<script setup>
import MarkdownContent from '../../MarkdownContent.vue'
import { fmtDuration, fullLabel } from '../../../utils/traceFormatters.js'
import CopyButton from './CopyButton.vue'

defineProps({
  span: { type: Object, required: true },
  selectedSpan: { type: Object, default: null },
})
defineEmits(['activate'])
</script>

<template>
  <div
    class="group rounded-lg border bg-slate-50 border-slate-200 px-3 py-2.5 cursor-pointer hover:border-slate-300 transition-colors"
    :class="selectedSpan && selectedSpan.span_id === span.span_id ? 'event-selected' : ''"
    @click="$emit('activate', span)"
  >
    <div class="flex items-center gap-2 mb-1.5">
      <span class="font-semibold uppercase tracking-wider text-[10px] text-emerald-700">Assistant</span>
      <span v-if="span.attributes?.truncated" class="text-amber-700 bg-amber-50 border border-amber-200 px-1 rounded text-[10px]">truncated</span>
      <CopyButton
        v-if="span.attributes?.text"
        :text="span.attributes.text"
        tint="text-slate-500 hover:bg-slate-200/60"
      />
      <span
        v-if="span.duration_ms"
        class="ml-auto font-mono text-[10.5px] text-slate-400"
        :title="span.attributes?.turn_total_duration_ms
          ? `inference ${fmtDuration(span.duration_ms)}, whole turn ${fmtDuration(span.attributes.turn_total_duration_ms)}`
          : `inference ${fmtDuration(span.duration_ms)}`"
      >{{ fmtDuration(span.duration_ms) }}</span>
    </div>
    <div class="text-[13.5px] text-slate-800">
      <MarkdownContent v-if="span.attributes?.text" :markdown="span.attributes.text" />
      <span v-else class="text-slate-500">{{ fullLabel(span) }}</span>
    </div>
  </div>
</template>
