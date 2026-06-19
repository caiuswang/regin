<script setup>
import {
  fmtClock, fmtDuration, fullLabel, mcpParts,
  toolRowDotClass, toolRowTextClass,
  taskRowStatus, taskRowIcon, taskRowIconClass,
} from '../../../utils/traceFormatters.js'

// Generic inline tool / skill / edit row — the fallback renderer for
// `tool.*`, `skill.read|invoke`, `file.edit`, `plan.edit`, and bare
// `subagent.*` markers that don't get a richer card.
defineProps({
  span: { type: Object, required: true },
  selectedSpan: { type: Object, default: null },
})
defineEmits(['activate'])
</script>

<template>
  <div
    tabindex="0"
    class="flex items-center gap-2 text-xs pl-3 cursor-pointer rounded px-2 py-1 -mx-2 hover:bg-slate-50 focus-visible:outline-2"
    :class="[
      span.attributes?.denied ? 'focus-visible:outline-amber-500' : (span.attributes?.rejected ? 'focus-visible:outline-red-500' : 'focus-visible:outline-blue-500'),
      selectedSpan && selectedSpan.span_id === span.span_id
        ? (span.attributes?.denied ? 'bg-amber-50' : (span.attributes?.rejected ? 'bg-red-50' : 'bg-blue-50'))
        : '',
    ]"
    @click="$emit('activate', span)"
  >
    <span class="inline-block w-1.5 h-1.5 rounded-full shrink-0"
          :class="toolRowDotClass(span)"></span>
    <span class="font-mono text-[11px] text-slate-400 shrink-0">{{ fmtClock(span.start_time) }}</span>
    <span
      v-if="mcpParts(span.name)"
      class="inline-block text-[9px] font-semibold uppercase tracking-wider px-1 py-px rounded bg-cyan-100 text-cyan-800 shrink-0"
    >MCP</span>
    <span
      v-if="taskRowStatus(span)"
      class="font-mono text-[13px] shrink-0 leading-none"
      :class="taskRowIconClass(taskRowStatus(span))"
      :title="`task ${taskRowStatus(span)}`"
    >{{ taskRowIcon(taskRowStatus(span)) }}</span>
    <span
      class="break-all flex-1 min-w-0 whitespace-pre-line"
      :class="toolRowTextClass(span)"
    >{{ fullLabel(span) }}</span>
    <!-- Interrupt badge for any non-AskUserQuestion permission-deny synth span
         (`tooldeny-*` from turn_trace). "Interrupted" matches Claude Code's own
         terminal label for the same event. -->
    <span
      v-if="span.attributes?.denied"
      class="font-sans uppercase tracking-wider text-[10px] bg-amber-100 border border-amber-200 text-amber-800 px-1 rounded shrink-0"
    >{{ span.attributes.deny_kind === 'chat' ? 'chat instead' : 'Interrupted' }}</span>
    <span
      v-else-if="span.attributes?.rejected"
      class="font-sans uppercase tracking-wider text-[10px] bg-red-100 border border-red-200 text-red-800 px-1 rounded shrink-0"
    >Rejected</span>
    <!-- User interrupted a non-Bash tool mid-run (Bash gets its badge in
         BashCard). Synth span from turn_trace carries `interrupted`/`is_interrupt`. -->
    <span
      v-else-if="span.attributes?.interrupted || span.attributes?.is_interrupt"
      class="font-sans uppercase tracking-wider text-[10px] bg-amber-100 border border-amber-200 text-amber-800 px-1 rounded shrink-0"
    >Interrupted</span>
    <span v-if="span.duration_ms" class="font-mono text-[11px] text-slate-400 shrink-0">{{ fmtDuration(span.duration_ms) }}</span>
  </div>
</template>
