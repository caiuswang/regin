<script setup>
import { fmtClock, fmtDuration } from '../../../utils/traceFormatters.js'
import Button from '../../ui/Button.vue'

// Dynamic-workflow launch: the Workflow tool call as a first-class row with an
// inline jump to the captured run. `workflow_run_id` + `workflow_name` are
// stamped at ingest; the "view run →" link appears once the run is captured.
defineProps({
  span: { type: Object, required: true },
  selectedSpan: { type: Object, default: null },
  // run_id → enriched run summary (agent_count, status, …)
  workflowRunsById: { type: Object, default: () => ({}) },
  // useConversationFolding: { isWorkflowExpanded, toggleWorkflowExpanded, workflowAgentCount }
  folding: { type: Object, required: true },
})
defineEmits(['activate'])
</script>

<template>
  <div
    tabindex="0"
    class="flex items-center gap-2 text-xs pl-3 cursor-pointer rounded px-2 py-1 -mx-2 hover:bg-emerald-50/60 focus-visible:outline-2 focus-visible:outline-emerald-500"
    :class="selectedSpan && selectedSpan.span_id === span.span_id ? 'bg-emerald-50' : ''"
    @click="$emit('activate', span)"
  >
    <!-- Fold the run's re-parented agent markers behind the card. -->
    <Button
      v-if="folding.workflowAgentCount(span.span_id)"
      variant="ghost"
      size="sm"
      class="shrink-0 h-auto px-0.5 text-slate-400 hover:bg-transparent hover:text-emerald-700"
      :title="folding.isWorkflowExpanded(span.span_id) ? 'Collapse agents' : `Expand ${folding.workflowAgentCount(span.span_id)} agent markers`"
      @click.stop="folding.toggleWorkflowExpanded(span.span_id)"
    >{{ folding.isWorkflowExpanded(span.span_id) ? '▾' : '▸' }}</Button>
    <span v-else class="inline-block w-1.5 h-1.5 rounded-full shrink-0 bg-emerald-500"></span>
    <span class="font-mono text-[11px] text-slate-400 shrink-0">{{ fmtClock(span.start_time) }}</span>
    <span class="font-medium text-emerald-700 shrink-0">⚙ Workflow</span>
    <span
      v-if="span.attributes?.workflow_name"
      class="font-mono text-[11px] text-slate-600 truncate flex-1 min-w-0"
    >{{ span.attributes.workflow_name }}</span>
    <span v-else class="flex-1"></span>
    <!-- Collapsed run summary: agents/status, computed server-side. -->
    <template v-if="workflowRunsById[span.attributes?.workflow_run_id]">
      <span class="font-mono text-[11px] text-slate-500 shrink-0">{{ workflowRunsById[span.attributes.workflow_run_id].agent_count }} agent<span v-if="workflowRunsById[span.attributes.workflow_run_id].agent_count !== 1">s</span></span>
      <span
        v-if="workflowRunsById[span.attributes.workflow_run_id].status"
        class="shrink-0 px-1 rounded text-[10px] border font-medium"
        :class="workflowRunsById[span.attributes.workflow_run_id].status === 'running'
          ? 'bg-amber-50 border-amber-200 text-amber-700'
          : 'bg-emerald-50 border-emerald-200 text-emerald-700'"
      >{{ workflowRunsById[span.attributes.workflow_run_id].status }}</span>
    </template>
    <router-link
      v-if="span.attributes?.workflow_run_id"
      :to="`/trace/sessions/${span.attributes.workflow_run_id}`"
      class="shrink-0 inline-flex items-center gap-1 px-1.5 py-0.5 rounded border border-emerald-300 bg-emerald-50 text-[11px] font-medium text-emerald-700 hover:bg-emerald-100 no-underline focus-visible:outline-2 focus-visible:outline-emerald-500"
      title="Open the captured trace for this workflow run"
      @click.stop
    >view run →</router-link>
    <span v-if="span.duration_ms" class="font-mono text-[11px] text-slate-400 shrink-0">{{ fmtDuration(span.duration_ms) }}</span>
  </div>
</template>
