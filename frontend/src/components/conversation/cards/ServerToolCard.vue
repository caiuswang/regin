<script setup>
import MarkdownContent from '../../MarkdownContent.vue'
import { fmtClock } from '../../../utils/traceFormatters.js'
import { useCopy } from '../../../composables/useCopy.js'

defineProps({
  span: { type: Object, required: true },
  selectedSpan: { type: Object, default: null },
})
defineEmits(['activate'])
const { copyText } = useCopy()
</script>

<template>
  <!-- Server-side tool result card (e.g. advisor) — renders the full
       response_text as markdown, since the call's value is the textual
       reply, not a side effect. -->
  <div
    class="group rounded-md border bg-violet-50 border-violet-200 px-3 py-2 cursor-pointer hover:border-violet-300 transition-colors"
    :class="selectedSpan && selectedSpan.span_id === span.span_id ? 'ring-2 ring-violet-300' : ''"
    @click="$emit('activate', span)"
  >
    <div class="flex items-center gap-2 text-[11px] font-mono text-violet-600 mb-1">
      <span class="font-semibold uppercase tracking-wider text-[10px]">{{ (span.attributes?.tool_name || 'tool').toUpperCase() }}</span>
      <span class="text-violet-300">·</span>
      <span>{{ fmtClock(span.start_time) }}</span>
      <span
        v-if="span.attributes?.advisor_model"
        class="text-[10px] text-violet-500 font-sans"
      >{{ span.attributes.advisor_model }}</span>
      <span v-if="span.attributes?.response_truncated" class="text-amber-700 bg-amber-50 border border-amber-200 px-1 rounded text-[10px]">truncated</span>
      <button
        type="button"
        class="ml-auto opacity-0 group-hover:opacity-100 transition-opacity px-1.5 py-0.5 rounded text-[10px] text-violet-600 hover:bg-violet-200/60 focus-visible:outline-2 focus-visible:outline-violet-400"
        title="Copy"
        @click.stop="copyText(span.attributes.response_text)"
      >Copy</button>
    </div>
    <div class="text-[13.5px] text-slate-800">
      <MarkdownContent :markdown="span.attributes.response_text" />
    </div>
  </div>
</template>
