<script setup>
import MarkdownContent from '../../MarkdownContent.vue'
import { fmtDuration, fmtTokens, fullLabel } from '../../../utils/traceFormatters.js'
import CopyButton from './CopyButton.vue'

defineProps({
  span: { type: Object, required: true },
  selectedSpan: { type: Object, default: null },
})
defineEmits(['activate'])
</script>

<template>
  <div
    class="group rounded-[9px] border border-transparent bg-surface px-[13px] py-[10px] cursor-pointer hover:border-slate-200 transition-colors"
    :class="selectedSpan && selectedSpan.span_id === span.span_id ? 'event-selected' : ''"
    @click="$emit('activate', span)"
  >
    <div class="flex items-center gap-2 mb-[5px]">
      <span class="font-bold uppercase tracking-[0.07em] text-[10px] text-emerald-700">Assistant</span>
      <span v-if="span.attributes?.truncated" class="text-amber-700 bg-amber-50 border border-amber-200 px-1 rounded text-[10px]">truncated</span>
      <CopyButton
        v-if="span.attributes?.text"
        :text="span.attributes.text"
        tint="text-slate-500 hover:bg-slate-200/60"
      />
      <span
        v-if="span.output_tokens || span.duration_ms"
        class="ml-auto font-mono text-[10.5px] text-slate-400"
        :title="span.attributes?.turn_total_duration_ms
          ? `inference ${fmtDuration(span.duration_ms)}, whole turn ${fmtDuration(span.attributes.turn_total_duration_ms)}`
          : `inference ${fmtDuration(span.duration_ms)}`"
      ><template v-if="span.output_tokens">{{ fmtTokens(span.output_tokens) }} tok</template><template v-if="span.output_tokens && span.duration_ms"> · </template><template v-if="span.duration_ms">{{ fmtDuration(span.duration_ms) }}</template></span>
    </div>
    <div class="text-[13px] leading-[1.55] text-slate-700">
      <MarkdownContent v-if="span.attributes?.text" :markdown="span.attributes.text" />
      <span v-else class="text-slate-500">{{ fullLabel(span) }}</span>
    </div>
  </div>
</template>
