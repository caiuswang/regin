<script setup>
import { fmtClock, fmtDuration, fullLabel, dotColor } from '../../../utils/traceFormatters.js'
import { useCopy } from '../../../composables/useCopy.js'

// Local command (`!ls` bang/bash or `/clear` slash): one-liner showing the
// command, expandable into a dark terminal panel with stdout/stderr — mirrors
// the tool.Bash row. The leading `!` / `/` already signals the kind, so no `$`
// shell prefix here. Reuses the bash expand set (folding.bashExpanded).
defineProps({
  span: { type: Object, required: true },
  selectedSpan: { type: Object, default: null },
  // useConversationFolding: { bashExpanded, toggleBashExpanded }
  folding: { type: Object, required: true },
})
defineEmits(['activate'])
const { copyText } = useCopy()
</script>

<template>
  <div class="group">
    <div
      tabindex="0"
      class="flex items-center gap-2 text-xs pl-3 cursor-pointer rounded px-2 py-1 -mx-2 hover:bg-slate-50 focus-visible:outline-2 focus-visible:outline-blue-500"
      :class="selectedSpan && selectedSpan.span_id === span.span_id ? 'bg-blue-50' : ''"
      @click="$emit('activate', span); folding.toggleBashExpanded(span.span_id)"
    >
      <span class="inline-block w-1.5 h-1.5 rounded-full shrink-0" :class="dotColor(span.name)"></span>
      <span class="font-mono text-[11px] text-slate-400 shrink-0">{{ fmtClock(span.start_time) }}</span>
      <span
        v-if="span.attributes?.stdout || span.attributes?.stderr"
        class="text-slate-400 shrink-0 select-none w-3 text-center"
      >{{ folding.bashExpanded(span.span_id) ? '▾' : '▸' }}</span>
      <span v-else class="w-3 shrink-0"></span>
      <span class="break-all flex-1 min-w-0 whitespace-pre-line font-mono text-teal-700 font-semibold">{{ span.attributes?.command_name || fullLabel(span) }}</span>
      <span v-if="span.duration_ms" class="font-mono text-[11px] text-slate-400 shrink-0">{{ fmtDuration(span.duration_ms) }}</span>
    </div>
    <div
      v-if="folding.bashExpanded(span.span_id) && (span.attributes?.stdout || span.attributes?.stderr)"
      class="ml-6 mt-1 rounded-md bg-slate-900 border border-slate-800 overflow-hidden"
    >
      <div v-if="span.attributes?.stdout" class="px-3 py-2">
        <div class="flex items-center gap-2 mb-1">
          <span class="text-[10px] font-semibold uppercase tracking-wider text-slate-400">stdout</span>
          <span
            v-if="span.attributes?.stdout_truncated"
            class="text-[10px] text-amber-300 bg-amber-900/40 border border-amber-700/60 px-1 rounded"
          >truncated</span>
          <button
            type="button"
            class="ml-auto opacity-0 group-hover:opacity-100 transition-opacity px-1.5 py-0.5 rounded text-[10px] text-slate-400 hover:bg-slate-700/60 focus-visible:outline-2 focus-visible:outline-slate-500"
            title="Copy"
            @click.stop="copyText(span.attributes.stdout)"
          >Copy</button>
        </div>
        <pre class="text-[12px] text-slate-100 whitespace-pre-wrap break-words font-mono leading-snug max-h-96 overflow-auto">{{ span.attributes.stdout }}</pre>
      </div>
      <div
        v-if="span.attributes?.stderr"
        class="px-3 py-2"
        :class="span.attributes?.stdout ? 'border-t border-slate-800' : ''"
      >
        <div class="flex items-center gap-2 mb-1">
          <span class="text-[10px] font-semibold uppercase tracking-wider text-red-400">stderr</span>
          <span
            v-if="span.attributes?.stderr_truncated"
            class="text-[10px] text-amber-300 bg-amber-900/40 border border-amber-700/60 px-1 rounded"
          >truncated</span>
          <button
            type="button"
            class="ml-auto opacity-0 group-hover:opacity-100 transition-opacity px-1.5 py-0.5 rounded text-[10px] text-red-400 hover:bg-red-900/40 focus-visible:outline-2 focus-visible:outline-red-500"
            title="Copy"
            @click.stop="copyText(span.attributes.stderr)"
          >Copy</button>
        </div>
        <pre class="text-[12px] text-red-300 whitespace-pre-wrap break-words font-mono leading-snug max-h-64 overflow-auto">{{ span.attributes.stderr }}</pre>
      </div>
    </div>
  </div>
</template>
