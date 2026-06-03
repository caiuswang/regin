<script setup>
import { fmtClock, toolDisplayName } from '../../../utils/traceFormatters.js'
import { useCopy } from '../../../composables/useCopy.js'

// Tool-failure card: surface tool_name + the input that failed (Bash command
// or file_path) + full error text inline (red tint). The error is capped at
// 16 KB by post_tool_failure.py so it fits without blowing up the row.
defineProps({
  span: { type: Object, required: true },
  selectedSpan: { type: Object, default: null },
})
defineEmits(['activate'])
const { copyText } = useCopy()
</script>

<template>
  <div
    class="group rounded-md border bg-red-50 border-red-200 px-3 py-2 cursor-pointer hover:border-red-300 transition-colors"
    :class="selectedSpan && selectedSpan.span_id === span.span_id ? 'ring-2 ring-red-300' : ''"
    @click="$emit('activate', span)"
  >
    <div class="flex items-center gap-2 text-[11px] font-mono text-red-700 mb-1">
      <span class="font-semibold uppercase tracking-wider text-[10px]">TOOL FAILURE</span>
      <span class="text-red-300">·</span>
      <span>{{ fmtClock(span.start_time) }}</span>
      <span class="font-sans text-red-700 font-medium">{{ toolDisplayName(span.attributes?.tool_name || 'tool') }}</span>
      <span
        v-if="span.attributes?.is_interrupt"
        class="text-[10px] bg-amber-100 border border-amber-200 text-amber-800 px-1 rounded font-sans"
      >user interrupt</span>
      <button
        v-if="span.attributes?.error"
        type="button"
        class="ml-auto opacity-0 group-hover:opacity-100 transition-opacity px-1.5 py-0.5 rounded text-[10px] text-red-600 hover:bg-red-200/60 focus-visible:outline-2 focus-visible:outline-red-400"
        title="Copy"
        @click.stop="copyText(span.attributes.error)"
      >Copy</button>
    </div>
    <!-- Bash failure: show the command with a $ prompt prefix. Falls back to
         command_preview if the full text wasn't captured. -->
    <div
      v-if="span.attributes?.tool_name === 'Bash' && (span.attributes?.command || span.attributes?.command_preview)"
      class="flex items-start gap-2 mb-1.5 text-[12.5px] font-mono"
    >
      <span class="text-emerald-700 font-semibold shrink-0 select-none">$</span>
      <pre class="text-slate-800 whitespace-pre-wrap break-words leading-snug flex-1 min-w-0">{{ span.attributes.command || span.attributes.command_preview }}</pre>
    </div>
    <!-- Non-Bash tools (Edit/Write/Read/etc.) surface file_path. -->
    <div
      v-else-if="span.attributes?.file_path"
      class="text-[12.5px] font-mono text-slate-700 mb-1.5 break-all"
    >{{ span.attributes.file_path }}</div>
    <pre
      v-if="span.attributes?.error"
      class="text-[12.5px] text-red-900 whitespace-pre-wrap break-words font-mono leading-snug"
    >{{ span.attributes.error }}</pre>
    <div
      v-else
      class="text-[12.5px] text-red-700/70 italic"
    >no error message recorded</div>
  </div>
</template>
