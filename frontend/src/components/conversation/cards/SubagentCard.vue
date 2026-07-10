<script setup>
import { computed, inject, ref } from 'vue'
import MarkdownContent from '../../MarkdownContent.vue'
import {
  fmtClock, fmtModel, fmtTokens, fmtDuration, fullLabel, toolRowDotClass,
} from '../../../utils/traceFormatters.js'
import { AGENT_PROMPT_PREVIEW_CHARS } from '../../../composables/useAgentLaunchMerge.js'
import AgentResultCard from './AgentResultCard.vue'
import Button from '../../ui/Button.vue'

// Subagent launch: subagent.start with its tool.Agent (description + prompt)
// folded in. Workflow agents have no launch span, so description/prompt fall
// back to the subagent.start span's own attributes (see agentMerge helpers).
const props = defineProps({
  span: { type: Object, required: true },
  selectedSpan: { type: Object, default: null },
  // useConversationFolding: agentHasChildren, isAgentExpanded, toggleAgentExpanded,
  //                         isAgentPromptExpanded, toggleAgentPrompt
  folding: { type: Object, required: true },
  // useAgentLaunchMerge: launchForSubagent, agentDescription, agentPrompt,
  //                      agentPromptOwnerId, agentPromptPreview
  agentMerge: { type: Object, required: true },
})
const emit = defineEmits(['activate', 'enter-scope'])

const prompt = computed(() => props.agentMerge.agentPrompt(props.span))
const promptOwnerId = computed(() => props.agentMerge.agentPromptOwnerId(props.span))

// A workflow-run projection emits its own synthetic workflow.agent_result
// after the agent's turns — only render the inline result card outside it
// (e.g. a launching session whose markers were enriched from the run trace).
const isWorkflowRun = inject('traceIsWorkflowRun', ref(false))
const resultSpan = computed(() => {
  if (isWorkflowRun.value || !props.agentMerge.agentResultText(props.span)) return null
  return { ...props.span, span_id: `${props.span.span_id}::result`, name: 'workflow.agent_result' }
})

// agent_id currently open in the companion pane (≥xl split), provided by the
// hosting SessionConversationView. Injected (not a prop) so the marker doesn't
// thread through the ConversationSpanCard dispatcher's template. The scoped
// feed inside the pane provides '' — only the main feed highlights.
const scopedAgentId = inject('traceScopedAgentId', ref(''))
const isScoped = computed(() => !!scopedAgentId.value
  && props.span.attributes?.agent_id === scopedAgentId.value)

// Select the agent's launch span (its task prompt) — falls back to the
// subagent.start span itself for workflow agents (no launch span).
function selectAgentPrompt() {
  emit('activate', props.agentMerge.launchForSubagent(props.span) || props.span)
}
</script>

<template>
  <div>
    <div
      tabindex="0"
      class="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs cursor-pointer rounded-md px-2.5 py-1.5 border border-slate-200 bg-slate-50 hover:bg-slate-100 hover:border-slate-300 transition-colors focus-visible:outline-2 focus-visible:outline-blue-500"
      :class="[
        selectedSpan && selectedSpan.span_id === span.span_id ? '!bg-blue-50 !border-blue-300 ring-1 ring-blue-200' : '',
        isScoped ? 'trace-subagent-scoped' : '',
      ]"
      :data-testid="isScoped ? 'trace-subagent-scoped' : undefined"
      @click="$emit('activate', span)"
    >
      <!-- Fold toggle: collapses the agent's whole subtree to just this header.
           Shown only when the agent has captured descendants to hide. Click
           target is the chevron only — the row body still selects the span. -->
      <Button
        v-if="folding.agentHasChildren(span.span_id)"
        variant="ghost"
        class="shrink-0 h-auto -my-1 px-0.5 text-slate-400 hover:bg-transparent hover:text-slate-700 font-mono text-[11px] leading-none"
        :title="folding.isAgentExpanded(span.span_id) ? 'Collapse agent' : 'Expand agent'"
        :aria-expanded="folding.isAgentExpanded(span.span_id)"
        @click.stop="folding.toggleAgentExpanded(span.span_id)"
      >{{ folding.isAgentExpanded(span.span_id) ? '▾' : '▸' }}</Button>
      <span class="inline-block w-1.5 h-1.5 rounded-full shrink-0" :class="toolRowDotClass(span)"></span>
      <span class="font-mono text-[11px] text-slate-400 shrink-0">{{ fmtClock(span.start_time) }}</span>
      <!-- Label: normal subagents read `subagent: <type> · <desc>`. Workflow
           agents have no agent_type, so the `subagent:` prefix would render
           bare — show just the agent's label instead. Single-line ellipsis, not
           break-all wrapping: under the ≥xl companion-pane squeeze a wrapping
           label degenerated into a one-character-per-line column. min-w-32
           keeps the ellipsized label legible; the row wraps trailing metric
           chips to a second line instead of crushing it further. -->
      <span class="truncate flex-1 min-w-32 text-sm font-semibold text-slate-800">
        <template v-if="span.attributes?.agent_type">{{ fullLabel(span) }}<template v-if="agentMerge.agentDescription(span)"><span class="text-slate-300"> · </span><span class="text-slate-600 font-normal">{{ agentMerge.agentDescription(span) }}</span></template></template>
        <template v-else>{{ agentMerge.agentDescription(span) || 'agent' }}</template>
      </span>
      <span
        v-if="span.attributes?.model"
        class="font-mono text-[10px] text-slate-500 bg-slate-100 border border-slate-200 px-1 rounded shrink-0"
        :title="span.attributes.model"
      >{{ fmtModel(span.attributes.model) }}</span>
      <span v-if="span.attributes?.tool_calls" class="font-mono text-[11px] text-slate-400 shrink-0">{{ span.attributes.tool_calls }} tool<span v-if="span.attributes.tool_calls !== 1">s</span></span>
      <span v-if="span.attributes?.tokens" class="font-mono text-[11px] text-slate-400 shrink-0">{{ fmtTokens(span.attributes.tokens) }} tok</span>
      <!-- Main-session impact: tokens this subagent's result added back into the
           parent context (serve-time estimate). Shown only on unambiguous 1:1
           turns whose launch carried result tokens. -->
      <span
        v-if="span.attributes?.main_session_impact_tokens"
        class="font-mono text-[11px] text-violet-600 bg-violet-50 border border-violet-200 px-1 rounded shrink-0"
        title="Estimated tokens the subagent's result added back into the main (parent) context"
      >+{{ fmtTokens(span.attributes.main_session_impact_tokens) }} → main</span>
      <span v-if="span.duration_ms" class="font-mono text-[11px] text-slate-400 shrink-0">{{ fmtDuration(span.duration_ms) }}</span>
      <!-- Scope the conversation feed to this agent's subtree (desktop
           sibling of the /live card's agent scoping). Not offered for agents
           on a discarded /rewind branch: the scoped feed drops rewound_away
           spans, so the affordance would open an empty scope — the discarded
           run stays readable inline behind its rewind marker instead. -->
      <Button
        v-if="span.attributes?.agent_id && !span.attributes?.rewound_away"
        variant="ghost"
        data-testid="trace-agent-view"
        class="shrink-0 h-auto -my-1 px-1.5 py-0.5 text-[11px] text-violet-600 hover:bg-violet-50 hover:text-violet-800"
        :aria-label="`Scope the view to ${span.attributes?.agent_type || 'this agent'}`"
        @click.stop="emit('enter-scope', span.attributes.agent_id)"
      >Agent view →</Button>
    </div>
    <!-- Task prompt card (collapsed by default) -->
    <div
      v-if="prompt"
      class="ml-6 mt-1 mb-1 rounded-md border border-slate-200 bg-slate-50 px-3 py-2 cursor-pointer hover:border-slate-300"
      :class="selectedSpan && selectedSpan.span_id === promptOwnerId ? 'ring-1 ring-blue-300' : ''"
      @click="selectAgentPrompt"
    >
      <div class="flex items-center justify-between gap-2 mb-1">
        <span class="text-[10px] font-semibold uppercase tracking-wider text-slate-400">task prompt</span>
        <Button
          v-if="prompt.length > AGENT_PROMPT_PREVIEW_CHARS"
          variant="link"
          size="sm"
          class="text-[11px] font-medium text-blue-600 hover:text-blue-800"
          @click.stop="folding.toggleAgentPrompt(span.span_id)"
        >{{ folding.isAgentPromptExpanded(span.span_id) ? 'Collapse' : `Show full prompt · ${prompt.length} chars` }}</Button>
      </div>
      <div :class="folding.isAgentPromptExpanded(span.span_id) ? 'max-h-[32rem] overflow-y-auto' : ''">
        <MarkdownContent
          v-if="folding.isAgentPromptExpanded(span.span_id)"
          :markdown="prompt"
        />
        <div
          v-else
          class="text-[12.5px] text-slate-700 whitespace-pre-wrap break-words leading-relaxed"
        >{{ agentMerge.agentPromptPreview(prompt) }}</div>
      </div>
    </div>
    <div v-if="resultSpan" class="ml-6">
      <AgentResultCard :span="resultSpan" :agent-merge="agentMerge" :folding="folding" />
    </div>
  </div>
</template>
