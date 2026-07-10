<script setup>
import DiffBlock from '../../DiffBlock.vue'
import {
  fmtClock, fmtDuration, fmtBytes, dotColor, diffOpLabel, diffFileName,
} from '../../../utils/traceFormatters.js'
import CopyButton from './CopyButton.vue'

// Edit / Write / MultiEdit diff card. Mirrors the Claude TUI's
// `Update(path) +N -M` view: a flat header row that expands into a dark
// terminal-style unified diff.
defineProps({
  span: { type: Object, required: true },
  selectedSpan: { type: Object, default: null },
  // useConversationFolding: { diffExpanded, toggleDiffExpanded }
  folding: { type: Object, required: true },
})
defineEmits(['activate'])
</script>

<template>
  <div class="group">
    <div
      tabindex="0"
      class="flex items-center gap-2 text-xs pl-3 cursor-pointer rounded px-2 py-1 -mx-2 hover:bg-slate-50 focus-visible:outline-2 focus-visible:outline-blue-500"
      :class="selectedSpan && selectedSpan.span_id === span.span_id ? 'bg-blue-50' : ''"
      @click="$emit('activate', span); folding.toggleDiffExpanded(span.span_id)"
    >
      <span class="inline-block w-1.5 h-1.5 rounded-full shrink-0" :class="dotColor(span.name)"></span>
      <span class="font-mono text-[11px] text-slate-400 shrink-0">{{ fmtClock(span.start_time) }}</span>
      <span class="text-slate-400 shrink-0 select-none w-3 text-center">{{ folding.diffExpanded(span.span_id) ? '▾' : '▸' }}</span>
      <span class="font-mono text-slate-700 min-w-0 truncate">
        <span class="font-semibold">{{ diffOpLabel(span.attributes?.edit_op) }}</span><span class="text-slate-500">({{ diffFileName(span) }})</span>
      </span>
      <span class="flex-1 min-w-0 flex items-center gap-2 overflow-hidden">
        <span
          v-if="span.attributes?.added_lines"
          class="font-mono text-[11px] text-emerald-600 shrink-0"
        >+{{ span.attributes.added_lines }}</span>
        <span
          v-if="span.attributes?.removed_lines"
          class="font-mono text-[11px] text-red-600 shrink-0"
        >-{{ span.attributes.removed_lines }}</span>
      </span>
      <span v-if="span.duration_ms" class="font-mono text-[11px] text-slate-400 shrink-0">{{ fmtDuration(span.duration_ms) }}</span>
    </div>
    <div
      v-if="folding.diffExpanded(span.span_id)"
      class="code-surface ml-6 mt-1 rounded-md bg-slate-900 border border-slate-800 overflow-hidden"
    >
      <div class="flex items-center gap-2 px-3 py-1.5 border-b border-slate-800">
        <span class="font-mono text-[11px] text-slate-300">
          <span class="font-semibold">{{ diffOpLabel(span.attributes?.edit_op) }}</span><span class="text-slate-500">({{ span.attributes?.file_path }})</span>
        </span>
        <span class="font-mono text-[11px] text-slate-400">
          Added <span class="text-emerald-300">{{ span.attributes?.added_lines || 0 }}</span> lines, removed <span class="text-red-300">{{ span.attributes?.removed_lines || 0 }}</span> lines
        </span>
        <span
          v-if="span.attributes?.diff_truncated_bytes"
          class="text-[10px] text-amber-300 bg-amber-900/40 border border-amber-700/60 px-1 rounded"
        >truncated {{ fmtBytes(span.attributes.diff_truncated_bytes) }}</span>
        <CopyButton :text="span.attributes.diff" />
      </div>
      <DiffBlock :diff="span.attributes.diff" :file-path="span.attributes?.file_path || ''" />
    </div>
  </div>
</template>
