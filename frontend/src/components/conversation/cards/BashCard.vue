<script setup>
import { computed } from 'vue'
import { fmtDuration, fmtBytes, fullLabel } from '../../../utils/traceFormatters.js'
import CopyButton from './CopyButton.vue'

// Bash row: flat one-liner like other inline tool rows when collapsed, led by
// a `Shell` verb. Output expands into a dark terminal-themed panel; the
// disclosure caret sits on the right, so every row's title stays left-aligned.
const props = defineProps({
  span: { type: Object, required: true },
  selectedSpan: { type: Object, default: null },
  // useConversationFolding: { bashExpanded, toggleBashExpanded }
  folding: { type: Object, required: true },
})
defineEmits(['activate'])

const hasBody = computed(() => {
  const a = props.span?.attributes || {}
  return Boolean(a.command || a.stdout || a.stderr)
})
</script>

<template>
  <!-- Same open/closed split as DiffCard: open, the row is the card's header
       bar and the terminal body attaches to it inside one bordered unit. -->
  <div
    class="group"
    :class="[
      folding.bashExpanded(span.span_id) ? 'rounded-[9px] border border-slate-200 overflow-hidden' : '',
      folding.bashExpanded(span.span_id) && selectedSpan && selectedSpan.span_id === span.span_id
        ? 'border-blue-300' : '',
    ]"
  >
    <div
      tabindex="0"
      class="flex items-baseline gap-[9px] cursor-pointer px-2.5 py-[5px] focus-visible:outline-2 focus-visible:outline-blue-500"
      :class="[
        folding.bashExpanded(span.span_id)
          ? 'bg-slate-100 border-b border-slate-200 px-3 py-[6px]'
          : 'rounded-lg border border-transparent hover:bg-slate-50',
        !folding.bashExpanded(span.span_id) && selectedSpan && selectedSpan.span_id === span.span_id
          ? 'event-selected' : '',
      ]"
      @click="$emit('activate', span); hasBody && folding.toggleBashExpanded(span.span_id)"
    >
      <span class="text-[12px] font-semibold text-slate-600 shrink-0">Shell</span>
      <span
        class="font-mono text-[11.5px] flex-1 min-w-0 truncate"
        :class="folding.bashExpanded(span.span_id) ? 'text-slate-900' : 'text-slate-500'"
      >{{ span.attributes?.command_preview || fullLabel(span) }}</span>
      <span
        v-if="span.attributes?.interrupted"
        class="text-[10px] bg-amber-100 border border-amber-200 text-amber-800 px-1 rounded shrink-0"
      >interrupted</span>
      <span
        v-if="span.attributes?.background_task_id"
        class="text-[10px] bg-sky-100 border border-sky-200 text-sky-800 px-1 rounded shrink-0"
        :title="`background task ${span.attributes.background_task_id}`"
      >background</span>
      <span
        v-if="span.attributes?.return_code_interpretation"
        class="text-[11px] text-slate-400 italic truncate shrink-0 max-w-[35%]"
      >{{ span.attributes.return_code_interpretation }}</span>
      <span v-if="span.duration_ms" class="font-mono text-[10.5px] text-slate-400 shrink-0">{{ fmtDuration(span.duration_ms) }}</span>
      <!-- Only when there is a body to reveal: an `interrupted`-only Bash span
           carries no command or output, and its caret opened an empty box. -->
      <span
        v-if="hasBody"
        class="text-[10.5px] text-slate-400 shrink-0 select-none w-3 text-center"
      >{{ folding.bashExpanded(span.span_id) ? '▾' : '▸' }}</span>
    </div>
    <div
      v-if="folding.bashExpanded(span.span_id)"
      class="code-surface bg-slate-900"
    >
      <div v-if="span.attributes?.command" class="px-3 py-2">
        <div class="flex items-center gap-2 mb-1">
          <span class="text-[10px] font-semibold uppercase tracking-wider text-emerald-400">command</span>
          <span
            v-if="span.attributes.command_truncated_bytes"
            class="text-[10px] text-amber-300 bg-amber-900/40 border border-amber-700/60 px-1 rounded"
          >truncated {{ fmtBytes(span.attributes.command_truncated_bytes) }}</span>
          <CopyButton :text="span.attributes.command" />
        </div>
        <pre class="text-[12px] text-emerald-200 whitespace-pre-wrap break-words font-mono leading-snug max-h-96 overflow-auto">{{ span.attributes.command }}</pre>
      </div>
      <div
        v-if="span.attributes?.stdout"
        class="px-3 py-2"
        :class="span.attributes?.command ? 'border-t border-slate-800' : ''"
      >
        <div class="flex items-center gap-2 mb-1">
          <span class="text-[10px] font-semibold uppercase tracking-wider text-slate-400">stdout</span>
          <span
            v-if="span.attributes.stdout_truncated_bytes"
            class="text-[10px] text-amber-300 bg-amber-900/40 border border-amber-700/60 px-1 rounded"
          >truncated {{ fmtBytes(span.attributes.stdout_truncated_bytes) }}</span>
          <CopyButton :text="span.attributes.stdout" />
        </div>
        <pre class="text-[12px] text-slate-100 whitespace-pre-wrap break-words font-mono leading-snug max-h-96 overflow-auto">{{ span.attributes.stdout }}</pre>
      </div>
      <div
        v-if="span.attributes?.stderr"
        class="px-3 py-2"
        :class="(span.attributes?.stdout || span.attributes?.command) ? 'border-t border-slate-800' : ''"
      >
        <div class="flex items-center gap-2 mb-1">
          <span class="text-[10px] font-semibold uppercase tracking-wider text-red-400">stderr</span>
          <span
            v-if="span.attributes.stderr_truncated_bytes"
            class="text-[10px] text-amber-300 bg-amber-900/40 border border-amber-700/60 px-1 rounded"
          >truncated {{ fmtBytes(span.attributes.stderr_truncated_bytes) }}</span>
          <CopyButton :text="span.attributes.stderr" tint="text-red-400 hover:bg-red-900/40" />
        </div>
        <pre class="text-[12px] text-red-300 whitespace-pre-wrap break-words font-mono leading-snug max-h-64 overflow-auto">{{ span.attributes.stderr }}</pre>
      </div>
    </div>
  </div>
</template>
