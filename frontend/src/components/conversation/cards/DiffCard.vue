<script setup>
import DiffBlock from '../../DiffBlock.vue'
import {
  fmtDuration, fmtBytes, diffOpLabel, diffFileName,
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
  <!-- Open, the row stops being a standalone line and becomes the card's
       header: one orange-bordered unit (header bar + dark diff), per the
       design. Collapsed it stays a flat spine row like every other tool. -->
  <div
    class="group"
    :class="[
      folding.diffExpanded(span.span_id) ? 'rounded-[9px] border border-orange-200 overflow-hidden' : '',
      folding.diffExpanded(span.span_id) && selectedSpan && selectedSpan.span_id === span.span_id
        ? 'border-blue-300' : '',
    ]"
  >
    <div
      tabindex="0"
      class="flex items-baseline gap-[9px] cursor-pointer px-2.5 py-[5px] focus-visible:outline-2 focus-visible:outline-blue-500"
      :class="[
        folding.diffExpanded(span.span_id)
          ? 'bg-orange-50 border-b border-orange-200 px-3 py-[7px]'
          : 'rounded-lg border border-transparent hover:bg-slate-50',
        !folding.diffExpanded(span.span_id) && selectedSpan && selectedSpan.span_id === span.span_id
          ? 'event-selected' : '',
      ]"
      @click="$emit('activate', span); folding.toggleDiffExpanded(span.span_id)"
    >
      <span class="text-[12px] font-semibold text-orange-700 shrink-0">{{ diffOpLabel(span.attributes?.edit_op) }}</span>
      <span
        class="font-mono text-[11.5px] flex-1 min-w-0 truncate"
        :class="folding.diffExpanded(span.span_id) ? 'text-orange-800' : 'text-slate-500'"
        :title="span.attributes?.file_path"
      >{{ diffFileName(span) }}</span>
      <span
        v-if="span.attributes?.added_lines"
        class="font-mono text-[10.5px] text-emerald-600 shrink-0"
      >+{{ span.attributes.added_lines }}</span>
      <span
        v-if="span.attributes?.removed_lines"
        class="font-mono text-[10.5px] text-red-600 shrink-0"
      >−{{ span.attributes.removed_lines }}</span>
      <span v-if="span.duration_ms" class="font-mono text-[10.5px] text-slate-400 shrink-0">{{ fmtDuration(span.duration_ms) }}</span>
      <span class="text-[10.5px] text-slate-400 shrink-0 select-none w-3 text-center">{{ folding.diffExpanded(span.span_id) ? '▾' : '▸' }}</span>
    </div>
    <div
      v-if="folding.diffExpanded(span.span_id)"
      class="code-surface bg-slate-900"
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
