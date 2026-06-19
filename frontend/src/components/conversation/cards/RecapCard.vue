<script setup>
import { fmtClock, dotColor } from '../../../utils/traceFormatters.js'

// `harness.recap` (Claude Code `system: away_summary`): the prose recap the
// harness writes when the session goes idle. Short enough to show inline — a
// muted note block under a "recap" header, no folding. Click selects the span
// so the detail panel can show the full (untruncated) attributes.
defineProps({
  span: { type: Object, required: true },
  selectedSpan: { type: Object, default: null },
})
defineEmits(['activate'])
</script>

<template>
  <div
    tabindex="0"
    class="pl-3 cursor-pointer rounded px-2 py-1 -mx-2 hover:bg-slate-50 focus-visible:outline-2 focus-visible:outline-blue-500"
    :class="selectedSpan && selectedSpan.span_id === span.span_id ? 'bg-blue-50' : ''"
    @click="$emit('activate', span)"
  >
    <div class="flex items-center gap-2 text-xs">
      <span class="inline-block w-1.5 h-1.5 rounded-full shrink-0" :class="dotColor(span.name)"></span>
      <span class="font-mono text-[11px] text-slate-400 shrink-0">{{ fmtClock(span.start_time) }}</span>
      <span class="font-semibold uppercase tracking-wider text-[10px] text-indigo-500 shrink-0">recap</span>
      <span
        v-if="span.attributes?.content_truncated"
        class="text-[10px] text-amber-700 bg-amber-50 border border-amber-200 px-1 rounded shrink-0"
      >truncated</span>
    </div>
    <p
      v-if="span.attributes?.content"
      class="ml-5 mt-1 text-[12px] text-slate-600 italic whitespace-pre-wrap break-words border-l-2 border-indigo-200 pl-2 leading-snug"
    >{{ span.attributes.content }}</p>
  </div>
</template>
