<script setup>
import { computed } from 'vue'
import { AGENT_RESULT_PREVIEW_CHARS } from '../../../composables/useAgentLaunchMerge.js'

// Deferred agent RESULT card (workflow runs): the span-tree projection emits
// this AFTER the agent's turns, so each agent reads prompt → work → result.
// `span` is a synthetic marker carrying the agent's attributes.
const props = defineProps({
  span: { type: Object, required: true },
  // useAgentLaunchMerge: { agentResultText, agentResultPreview }
  agentMerge: { type: Object, required: true },
  // useConversationFolding: { isAgentResultExpanded, toggleAgentResult }
  folding: { type: Object, required: true },
})

const resultText = computed(() => props.agentMerge.agentResultText(props.span))
const expanded = computed(() => props.folding.isAgentResultExpanded(props.span.span_id))
</script>

<template>
  <div class="mt-1 mb-2 rounded-md border border-emerald-200 bg-emerald-50/50 px-3 py-2">
    <div class="flex items-center justify-between gap-2 mb-1">
      <span class="text-[10px] font-semibold uppercase tracking-wider text-emerald-700">result</span>
      <button
        v-if="resultText.length > AGENT_RESULT_PREVIEW_CHARS"
        type="button"
        class="text-[11px] font-medium text-emerald-700 hover:text-emerald-900 rounded focus-visible:outline-2 focus-visible:outline-emerald-500"
        @click.stop="folding.toggleAgentResult(span.span_id)"
      >{{ expanded ? 'Collapse' : `Show full · ${resultText.length} chars` }}</button>
    </div>
    <div
      class="text-[12.5px] text-slate-700 whitespace-pre-wrap break-words leading-relaxed font-mono"
      :class="expanded ? 'max-h-[32rem] overflow-y-auto' : ''"
    >{{ expanded ? resultText : agentMerge.agentResultPreview(resultText) }}</div>
  </div>
</template>
