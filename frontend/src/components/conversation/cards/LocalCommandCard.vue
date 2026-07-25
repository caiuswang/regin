<script setup>
import { fmtDuration, fullLabel } from '../../../utils/traceFormatters.js'
import CopyButton from './CopyButton.vue'

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
</script>

<template>
  <div class="group">
    <div
      tabindex="0"
      class="flex items-baseline gap-[9px] cursor-pointer rounded-lg border border-transparent px-2.5 py-[5px] hover:bg-slate-50 focus-visible:outline-2 focus-visible:outline-blue-500"
      :class="selectedSpan && selectedSpan.span_id === span.span_id ? 'event-selected' : ''"
      @click="$emit('activate', span); folding.toggleBashExpanded(span.span_id)"
    >
      <span class="text-[12.5px] font-semibold text-teal-700 shrink-0">Command</span>
      <span class="font-mono text-[12.5px] text-slate-500 flex-1 min-w-0 truncate">{{ span.attributes?.command_name || fullLabel(span) }}</span>
      <span v-if="span.duration_ms" class="font-mono text-[10.5px] text-slate-400 shrink-0">{{ fmtDuration(span.duration_ms) }}</span>
      <span
        v-if="span.attributes?.stdout || span.attributes?.stderr"
        class="text-[10.5px] text-slate-400 shrink-0 select-none w-3 text-center"
      >{{ folding.bashExpanded(span.span_id) ? '▾' : '▸' }}</span>
    </div>
    <div
      v-if="folding.bashExpanded(span.span_id) && (span.attributes?.stdout || span.attributes?.stderr)"
      class="code-surface ml-2.5 mt-1 rounded-lg bg-slate-900 border border-slate-800 overflow-hidden"
    >
      <div v-if="span.attributes?.stdout" class="px-3 py-2">
        <div class="flex items-center gap-2 mb-1">
          <span class="text-[10px] font-semibold uppercase tracking-wider text-slate-400">stdout</span>
          <span
            v-if="span.attributes?.stdout_truncated"
            class="text-[10px] text-amber-300 bg-amber-900/40 border border-amber-700/60 px-1 rounded"
          >truncated</span>
          <CopyButton :text="span.attributes.stdout" />
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
          <CopyButton :text="span.attributes.stderr" tint="text-red-400 hover:bg-red-900/40" />
        </div>
        <pre class="text-[12px] text-red-300 whitespace-pre-wrap break-words font-mono leading-snug max-h-64 overflow-auto">{{ span.attributes.stderr }}</pre>
      </div>
    </div>
  </div>
</template>
