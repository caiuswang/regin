<script setup>
import { computed, inject, ref } from 'vue'
import {
  fmtModel, fmtTokens, fmtDuration, fmtCost, fullLabel,
  subagentSegment, agentRosterEntryFor,
} from '../../../utils/traceFormatters.js'
import { AGENT_PROMPT_PREVIEW_CHARS } from '../../../composables/useAgentLaunchMerge.js'
import { useAgentElapsed } from '../../../composables/useAgentElapsed.js'
import { agentStatusLabel, humanMain } from '../../../utils/liveRows.js'
import AgentResultCard from './AgentResultCard.vue'
import Button from '../../ui/Button.vue'

// Subagent launch: subagent.start with its tool.Agent (description + prompt)
// folded in. Workflow agents have no launch span, so description/prompt fall
// back to the subagent.start span's own attributes (see agentMerge helpers).
const props = defineProps({
  span: { type: Object, required: true },
  selectedSpan: { type: Object, default: null },
  // useConversationFolding: isAgentPromptExpanded, toggleAgentPrompt
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

// The agent's roster entry — the server's verdict on whether it is still
// running, and its newest span. The card is the parent's only window on the
// agent while it runs, so while the roster says it is alive the card carries
// the same line the terminal would: status, elapsed, and the step in
// progress. A finished agent says nothing here; its result card does.
const agentById = inject('traceAgentById', ref(new Map()))
const attrs = computed(() => props.span.attributes || {})
const clock = inject('traceServerClock', ref({ now: '', at: 0 }))
// Null on an earlier run of a resumed agent — the roster entry keyed on
// `agent_id` describes only the newest one, and the whole point of this card
// is that the older runs must stop echoing it.
const agent = computed(() => agentRosterEntryFor(attrs.value, agentById.value))
const segment = computed(() => subagentSegment(attrs.value))
const alive = computed(() => !!agent.value?.running)
const elapsed = useAgentElapsed(
  () => agent.value?.startTime || '',
  () => clock.value.now,
  () => clock.value.at,
  () => alive.value,
)
const progress = computed(() => {
  if (!alive.value) return null
  const a = agent.value
  const last = a.lastSpan
  // `humanMain` phrases a span the way the /live card does ("$ pytest -q",
  // "Edited a.py"); the roster's `last_span` carries just enough of the
  // attribute bag for that.
  const step = last
    ? humanMain({ name: last.name, attributes: {
      command_preview: last.preview, file_path: last.preview, description: last.preview,
      tool_name: (last.name || '').replace(/^tool\./, ''),
    } })
    : null
  return {
    status: agentStatusLabel(a, elapsed.value),
    waiting: a.status === 'waiting',
    spans: a.spanCount,
    step: step ? `${step.pre ? `${step.pre} ` : ''}${step.text}` : '',
    stepLive: !!last && last.status_code === 'PENDING',
  }
})

// Select the agent's launch span (its task prompt) — falls back to the
// subagent.start span itself for workflow agents (no launch span).
function selectAgentPrompt() {
  emit('activate', props.agentMerge.launchForSubagent(props.span) || props.span)
}

const tokenTotal = computed(() => {
  const { agent_input_tokens: input, agent_output_tokens: output } = attrs.value
  if (input == null && output == null) return null
  return (input || 0) + (output || 0)
})

// Whole-agent rollups, stamped on the marker at serve time. An absent key
// means no source supplied the figure, so its chip is omitted entirely —
// rendering it as 0 would claim the agent did nothing.
const metrics = computed(() => {
  const a = attrs.value
  const out = []
  const push = (key, value, text, title) => {
    if (value != null) out.push({ key, text, title })
  }
  // Elapsed and span count are each described by TWO sources, and mid-run the
  // two disagree: this extent covers only the work already recorded (49s
  // against the live line's 1m03s on one measured run), and this count is
  // window-scoped where the roster's is session-wide — on a paginated view, or
  // on one run of a resumed agent, they are different numbers for one thing.
  // So while the roster says the agent is alive the progress line owns both;
  // these chips return with the finished summary, when they are the only
  // source left.
  const whenFinished = (key, value, text, title) => {
    if (!alive.value) push(key, value, text, title)
  }
  const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`
  const usage = () => {
    push('tokens', tokenTotal.value, `${fmtTokens(tokenTotal.value)} tok`,
      `${a.agent_input_tokens || 0} in / ${a.agent_output_tokens || 0} out`)
    push('cost', a.agent_cost_usd, fmtCost(a.agent_cost_usd),
      "this agent's own spend")
  }
  // A card of a resumed agent stands for ONE run, and the `run N/M` chip right
  // beside these says so — so every figure it can scope to that run is scoped.
  // The whole-agent totals stay on the first marker (summing them across
  // markers would over-report the bill), but rendering them here read as "this
  // run did 131 spans in 5m52s" when the run did 83 in 1m38s.
  if (segment.value) {
    whenFinished('spans', a.agent_segment_span_count,
      plural(a.agent_segment_span_count, 'span'), 'spans this run produced')
    push('tools', a.agent_segment_tool_count,
      plural(a.agent_segment_tool_count, 'tool'), 'tool calls in this run')
    whenFinished('duration', a.agent_segment_duration_ms,
      fmtDuration(a.agent_segment_duration_ms), 'first to last span of this run')
    // Usage has no per-run split to scope it to: the stop marker totals the
    // agent's whole transcript, and the bill reads that one row. So it is
    // carried by the first card and named for what it covers.
    usage()
    if (out.some(m => m.key === 'tokens' || m.key === 'cost')) {
      out.push({ key: 'scope', text: 'all runs',
        title: 'tokens and spend are billed per agent, not per run, so these cover every run' })
    }
    return out
  }
  whenFinished('spans', a.agent_span_count, plural(a.agent_span_count, 'span'),
    'spans this agent produced, served by its own trace')
  push('tools', a.agent_tool_count, plural(a.agent_tool_count, 'tool'),
    'tool calls this agent made')
  usage()
  whenFinished('duration', a.agent_duration_ms, fmtDuration(a.agent_duration_ms),
    'first to last span of this agent')
  return out
})
</script>

<template>
  <div>
    <div
      tabindex="0"
      class="flex flex-wrap items-center gap-x-[9px] gap-y-1 text-xs cursor-pointer rounded-[10px] px-3 py-[9px] border border-violet-200 bg-violet-50 hover:bg-violet-100 hover:border-violet-300 transition-colors focus-visible:outline-2 focus-visible:outline-violet-500"
      :class="[
        selectedSpan && selectedSpan.span_id === span.span_id ? 'event-selected' : '',
        isScoped ? 'trace-subagent-scoped' : '',
      ]"
      :data-testid="isScoped ? 'trace-subagent-scoped' : undefined"
      @click="$emit('activate', span)"
    >
      <!-- Label: normal subagents read `subagent: <type> · <desc>`. Workflow
           agents have no agent_type, so the `subagent:` prefix would render
           bare — show just the agent's label instead. Single-line ellipsis, not
           break-all wrapping: under the ≥xl companion-pane squeeze a wrapping
           label degenerated into a one-character-per-line column. min-w-32
           keeps the ellipsized label legible; the row wraps trailing metric
           chips to a second line instead of crushing it further. -->
      <span class="truncate flex-1 min-w-32 text-[12.5px] font-bold text-violet-700">
        <template v-if="span.attributes?.agent_type">{{ fullLabel(span) }}<template v-if="agentMerge.agentDescription(span)"><span class="text-violet-300"> · </span><span class="text-[11.5px] text-violet-600 font-normal">{{ agentMerge.agentDescription(span) }}</span></template></template>
        <template v-else>{{ agentMerge.agentDescription(span) || 'agent' }}</template>
      </span>
      <!-- Which run of a resumed agent this is. Without it two segments of one
           agent are indistinguishable and read as a duplicated span. -->
      <span
        v-if="segment"
        class="font-mono text-[10px] text-violet-700 bg-violet-100 border border-violet-200 px-1 rounded shrink-0"
        data-testid="subagent-segment"
        :title="`This agent was resumed: run ${segment.index + 1} of ${segment.count} under one agent id`"
      >run {{ segment.index + 1 }}/{{ segment.count }}</span>
      <span
        v-if="span.attributes?.model"
        class="font-mono text-[10px] text-slate-500 bg-slate-100 border border-slate-200 px-1 rounded shrink-0"
        :title="span.attributes.model"
      >{{ fmtModel(span.attributes.model) }}</span>
      <span
        v-for="m in metrics"
        :key="m.key"
        class="font-mono text-[11px] text-slate-400 shrink-0"
        :data-testid="`subagent-metric-${m.key}`"
        :title="m.title"
      >{{ m.text }}</span>
      <!-- Main-session impact: tokens this subagent's result added back into the
           parent context (serve-time estimate). Shown only on unambiguous 1:1
           turns whose launch carried result tokens. -->
      <span
        v-if="span.attributes?.main_session_impact_tokens"
        class="font-mono text-[11px] text-violet-600 bg-violet-50 border border-violet-200 px-1 rounded shrink-0"
        title="Estimated tokens the subagent's result added back into the main (parent) context"
      >+{{ fmtTokens(span.attributes.main_session_impact_tokens) }} → main</span>
      <!-- Scope the conversation feed to this agent's subtree (desktop
           sibling of the /live card's agent scoping). Not offered for agents
           on a discarded /rewind branch: the scoped feed drops rewound_away
           spans, so the affordance would open an empty scope — the discarded
           run stays readable inline behind its rewind marker instead. -->
      <Button
        v-if="span.attributes?.agent_id && !span.attributes?.rewound_away"
        variant="ghost"
        data-testid="trace-agent-view"
        class="shrink-0 h-auto -my-0.5 rounded-[7px] border border-violet-200 bg-surface px-2.5 py-[3px] text-[11px] font-semibold text-violet-700 hover:bg-violet-100 hover:text-violet-800"
        :aria-label="`Scope the view to ${span.attributes?.agent_type || 'this agent'}`"
        @click.stop="emit('enter-scope', span.attributes.agent_id)"
      >Agent view →</Button>
    </div>
    <!-- What the agent is doing NOW, while the roster says it is alive. -->
    <div
      v-if="progress"
      class="ml-2.5 mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11.5px]"
      :class="progress.waiting ? 'text-amber-700' : 'text-emerald-700'"
      data-testid="subagent-progress"
    >
      <span class="inline-flex items-center gap-1.5 font-semibold">
        <span class="span-running-pip" aria-hidden="true"></span>{{ progress.status }}
      </span>
      <span class="text-slate-400">· {{ progress.spans }} spans</span>
      <span
        v-if="progress.step"
        class="min-w-0 truncate font-mono text-slate-600"
        :title="progress.step"
      >· {{ progress.step }}<span v-if="progress.stepLive" class="text-slate-400"> (in progress)</span></span>
    </div>
    <!-- Task prompt card (collapsed by default) -->
    <div
      v-if="prompt"
      class="ml-2.5 mt-1 mb-1 rounded-lg border border-violet-200 bg-violet-50/40 px-3 py-2 cursor-pointer hover:border-violet-300"
      :class="selectedSpan && selectedSpan.span_id === promptOwnerId ? 'event-selected' : ''"
      @click="selectAgentPrompt"
    >
      <div class="flex items-center justify-between gap-2 mb-1">
        <span class="text-[10px] font-semibold uppercase tracking-wider text-violet-500">task prompt</span>
        <Button
          v-if="prompt.length > AGENT_PROMPT_PREVIEW_CHARS"
          variant="link"
          size="sm"
          class="min-w-0 max-w-full text-[11px] font-medium text-blue-600 hover:text-blue-800"
          @click.stop="folding.toggleAgentPrompt(span.span_id)"
        ><span class="truncate">{{ folding.isAgentPromptExpanded(span.span_id) ? 'Collapse' : `Show full prompt · ${prompt.length} chars` }}</span></Button>
      </div>
      <div :class="folding.isAgentPromptExpanded(span.span_id) ? 'max-h-[32rem] overflow-y-auto' : ''">
        <!-- A launch prompt is the INPUT handed to the agent, not model-authored
             markdown: rendered through the markdown pipeline its indentation
             collapsed and marks inside paths/snippets were swallowed. Plain,
             like the typed-prompt card. -->
        <div
          class="text-[12.5px] text-slate-700 whitespace-pre-wrap break-words leading-relaxed"
        >{{ folding.isAgentPromptExpanded(span.span_id) ? prompt : agentMerge.agentPromptPreview(prompt) }}</div>
      </div>
    </div>
    <div v-if="resultSpan" class="ml-2.5">
      <AgentResultCard :span="resultSpan" :agent-merge="agentMerge" :folding="folding" />
    </div>
  </div>
</template>
