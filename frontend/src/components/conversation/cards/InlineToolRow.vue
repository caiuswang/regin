<script setup>
import { computed } from 'vue'
import {
  fmtDuration, mcpParts, rowParts, toolRowTextClass,
  taskRowStatus, taskRowIcon, taskRowIconClass,
} from '../../../utils/traceFormatters.js'

// Generic inline tool / skill / edit row — the fallback renderer for
// `tool.*`, `skill.read|invoke`, `file.edit`, `plan.edit`, and bare
// `subagent.*` markers that don't get a richer card.
//
// Title-first: the semantic verb leads, the subject follows in mono and
// ellipsizes, and the only right-aligned meta is the duration. The wall clock
// lives on the turn header, not once per row; there is no disclosure caret
// because this row has no body to open.
const props = defineProps({
  span: { type: Object, required: true },
  selectedSpan: { type: Object, default: null },
})
defineEmits(['activate'])

const parts = computed(() => rowParts(props.span))
const selected = computed(() =>
  !!(props.selectedSpan && props.selectedSpan.span_id === props.span.span_id))
</script>

<template>
  <div
    tabindex="0"
    class="flex items-baseline gap-[9px] cursor-pointer rounded-lg border border-transparent px-2.5 py-[5px] hover:bg-slate-50 focus-visible:outline-2"
    :class="[
      span.attributes?.denied ? 'focus-visible:outline-amber-500' : (span.attributes?.rejected ? 'focus-visible:outline-red-500' : 'focus-visible:outline-blue-500'),
      selected ? 'event-selected' : '',
    ]"
    @click="$emit('activate', span)"
  >
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
    <!-- The title must be allowed to shrink. As `shrink-0` a long one (an MCP
         tool's `server · method`) pushed the whole row past the pane on a
         phone — the flex children spilled outside the row box, so nothing
         clipped it and the feed scrolled sideways. -->
    <span
      class="min-w-0 truncate text-[12.5px] font-semibold"
      :class="toolRowTextClass(span)"
      :title="parts.title"
    >{{ parts.title }}</span>
    <span
      v-if="parts.subject"
      class="font-mono text-[12.5px] text-slate-500 flex-1 min-w-0 truncate"
      :title="parts.subject"
    >{{ parts.subject }}</span>
    <span v-else class="flex-1 min-w-0"></span>
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
    <span
      v-if="span.duration_ms"
      class="font-mono text-[10.5px] text-slate-400 shrink-0"
    >{{ fmtDuration(span.duration_ms) }}</span>
  </div>
</template>
