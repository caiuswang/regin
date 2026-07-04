<script setup>
// Span detail panel, extracted from SessionTraceView: the sticky sidebar Card
// that renders everything about the currently-selected span — meta grid,
// prompt / assistant-response / rule-check / workflow-run / AskUserQuestion /
// deny sections, and the generic attributes table.
//
// Reads only `selectedSpan` plus the rule-trigger info needed for the
// mark-as-noise control; all the per-section display helpers live here. The
// parent still owns trigger fetching (we emit `suppress-changed` to re-fetch).
import { ref, computed } from 'vue'
import { mcpParts, fmtTokens } from '../utils/traceFormatters.js'
import Card from './Card.vue'
import McpCallDetail from './McpCallDetail.vue'
import MarkdownContent from './MarkdownContent.vue'
import SuppressButton from './triggers/SuppressButton.vue'
import Button from './ui/Button.vue'

const props = defineProps({
  selectedSpan: { type: Object, default: null },
  ruleTriggersByRuleId: { type: Object, default: () => ({}) },
  canSuppressRule: { type: Boolean, default: false },
  workflowRunsById: { type: Object, default: () => ({}) },
})

defineEmits(['suppress-changed', 'view-message'])

const promptExpanded = ref(false)

// A send_to_user MCP call: its `user_message` is full markdown prose that the
// Messages tab already renders properly. Don't dump the raw text in the
// attributes table — offer a jump to the rendered feed instead.
const isSendToUser = computed(() =>
  /send[_-]to[_-]user/i.test(props.selectedSpan?.name || '')
  || /send[_-]to[_-]user/i.test(props.selectedSpan?.attributes?.tool_name || ''))

// An MCP tool call (e.g. memory recall): show the params asked and the
// result returned as a dedicated round-trip, so the keys aren't just dumped
// as opaque rows in the generic attributes table. send_to_user is excluded —
// it has its own "View in Messages" affordance.
const mcpCall = computed(() => {
  const span = props.selectedSpan
  if (!span || isSendToUser.value) return null
  const a = span.attributes || {}
  if (!a.mcp && !mcpParts(span.name)) return null
  if (a.mcp_input == null && a.mcp_result == null) return null
  return {
    input: a.mcp_input,
    inputDropped: a.mcp_input_truncated_bytes,
    result: a.mcp_result,
    resultDropped: a.mcp_result_truncated_bytes,
  }
})
const MCP_HIDDEN_KEYS = ['mcp', 'mcp_input', 'mcp_result',
  'mcp_input_truncated_bytes', 'mcp_result_truncated_bytes', 'tool_input_keys']

// Local date/duration formatters — exact copies of SessionTraceView's, kept
// local to avoid the differently-behaved traceFormatters variants (see that
// file's note).
function fmtTime(iso) {
  if (!iso) return '-'
  const d = new Date(iso)
  return d.toLocaleTimeString() + '.' + String(d.getMilliseconds()).padStart(3, '0')
}
function fmtDuration(ms) {
  if (!ms) return '-'
  if (ms < 1000) return `${ms}ms`
  const seconds = Math.floor(ms / 1000) % 60
  const minutes = Math.floor(ms / 60000) % 60
  const hours = Math.floor(ms / 3600000) % 24
  const days = Math.floor(ms / 86400000)
  const units = [
    { value: days, label: 'd' },
    { value: hours, label: 'h' },
    { value: minutes, label: 'm' },
    { value: seconds, label: 's' },
  ]
  const start = units.findIndex(u => u.value > 0)
  if (start === -1) return '-'
  let end = units.length - 1
  while (end > start && units[end].value === 0) end--
  return units.slice(start, end + 1).map(u => `${u.value}${u.label}`).join('')
}

// Spans whose duration_ms is a semantic latency (inference time) rather than a
// wall-clock envelope; their start_time marks completion, not start.
const SEMANTIC_DURATION_NAMES = new Set(['assistant_response', 'assistant.thinking'])
function estStart(span) {
  if (!span) return null
  if (span.attributes?.estimated_start_time) return span.attributes.estimated_start_time
  if (!SEMANTIC_DURATION_NAMES.has(span.name) || !span.duration_ms) return null
  return new Date(new Date(span.start_time).getTime() - span.duration_ms).toISOString()
}

// Hide keys from the generic attributes table that are rendered by a dedicated
// section above (prose, Q&A, rule list, deny panel) to avoid duplicate reads.
const visibleAttributeKeys = computed(() => {
  const span = props.selectedSpan
  if (!span || !span.attributes) return []
  const keys = Object.keys(span.attributes)
  const attrs = span.attributes
  if (isSendToUser.value) {
    return keys.filter(k => k !== 'user_message')
  }
  if (mcpCall.value) {
    return keys.filter(k => !MCP_HIDDEN_KEYS.includes(k))
  }
  if (span.name === 'assistant_response' || span.name === 'prompt') {
    return keys.filter(k => k !== 'text' && k !== 'estimated_start_time')
  }
  if (span.name === 'assistant.thinking') {
    return keys.filter(k => k !== 'estimated_start_time')
  }
  if (span.name === 'tool.AskUserQuestion') {
    return keys.filter(k => !['questions', 'answers', 'annotations',
                              'denied', 'denial_reason', 'denial_reason_truncated_bytes',
                              'deny_kind'].includes(k))
  }
  if (attrs.denied) {
    return keys.filter(k => !['denied', 'denial_reason',
                              'denial_reason_truncated_bytes', 'deny_kind'].includes(k))
  }
  if (span.name === 'rule.check') {
    return keys.filter(k => ![
      'applicable_rules', 'engine_tags',
      'applicable_rule_count', 'violating_rule_count',
      'total_rules', 'status', 'relative_path',
    ].includes(k))
  }
  return keys
})

const selectedPromptText = computed(() => {
  if (props.selectedSpan?.name !== 'prompt') return ''
  return props.selectedSpan?.attributes?.text || ''
})

const PROMPT_COLLAPSE_CHAR_THRESHOLD = 500
const PROMPT_COLLAPSE_LINE_THRESHOLD = 8
const selectedPromptNeedsExpand = computed(() => {
  const text = selectedPromptText.value
  return (
    text.length > PROMPT_COLLAPSE_CHAR_THRESHOLD
    || text.split('\n').length > PROMPT_COLLAPSE_LINE_THRESHOLD
  )
})

function ruleSeverityClass(sev) {
  if (sev === 'error') return 'text-red-700 bg-red-50 border-red-200'
  if (sev === 'warn') return 'text-amber-700 bg-amber-50 border-amber-200'
  return 'text-slate-600 bg-slate-50 border-slate-200'
}

function _answerFor(q) {
  if (!props.selectedSpan) return undefined
  const answers = props.selectedSpan.attributes?.answers || {}
  return answers[q?.question]
}
function optLabel(opt) {
  if (opt && typeof opt === 'object') return opt.label
  return opt
}
function optDescription(opt) {
  return opt && typeof opt === 'object' ? (opt.description || '') : ''
}
function isChosenOption(q, opt) {
  const ans = _answerFor(q)
  if (!ans) return false
  const label = optLabel(opt)
  if (q?.multiSelect && Array.isArray(ans)) return ans.includes(label)
  return ans === label
}
function freeTextAnswer(q) {
  const ans = _answerFor(q)
  if (!ans || typeof ans !== 'string') return ''
  const labels = (q?.options || []).map(optLabel)
  if (labels.includes(ans)) return ''
  return ans
}
function annotationNote(q) {
  if (!props.selectedSpan) return ''
  const ann = props.selectedSpan.attributes?.annotations || {}
  return ann[q?.question]?.notes || ''
}
</script>

<template>
  <Card v-if="selectedSpan">
    <h2 class="text-sm font-semibold text-slate-700 mb-3">Span details</h2>
    <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 text-sm mb-4">
      <div>
        <div class="text-xs text-gray-400">Name</div>
        <div class="font-medium break-words flex items-center gap-1 flex-wrap">
          <span
            v-if="mcpParts(selectedSpan.name)"
            class="inline-block text-[9px] font-semibold uppercase tracking-wider px-1 py-px rounded bg-cyan-100 text-cyan-800"
          >MCP</span>
          <span>{{ selectedSpan.name }}</span>
        </div>
      </div>
      <div>
        <div class="text-xs text-gray-400">Kind</div>
        <div>{{ selectedSpan.kind }}</div>
      </div>
      <div>
        <div class="text-xs text-gray-400">Source</div>
        <div>
          <span
            class="inline-block text-[10px] font-medium px-1.5 py-0.5 rounded border"
            :class="selectedSpan.source === 'transcript'
              ? 'bg-indigo-50 border-indigo-200 text-indigo-700'
              : 'bg-slate-50 border-slate-200 text-slate-600'"
            :title="selectedSpan.source === 'transcript'
              ? 'Written by the transcript scan (prompt / response / thinking anchors, local commands)'
              : 'Written by live hook events (tool timing, permissions, skill reads)'"
          >{{ selectedSpan.source || 'hook' }}</span>
        </div>
      </div>
      <div>
        <div class="text-xs text-gray-400">Status</div>
        <div>{{ selectedSpan.status_code }}</div>
      </div>
      <div>
        <div class="text-xs text-gray-400">Duration</div>
        <div>{{ fmtDuration(selectedSpan.duration_ms) }}</div>
      </div>
      <div v-if="estStart(selectedSpan)">
        <div class="text-xs text-gray-400">Est. start</div>
        <div :title="'estimated inference start: completion − inference latency'">{{ fmtTime(estStart(selectedSpan)) }}</div>
      </div>
      <div>
        <div class="text-xs text-gray-400">{{ estStart(selectedSpan) ? 'Recorded' : 'Start' }}</div>
        <div>{{ fmtTime(selectedSpan.start_time) }}</div>
      </div>
      <div>
        <div class="text-xs text-gray-400">End</div>
        <div>{{ fmtTime(selectedSpan.end_time || selectedSpan.start_time) }}</div>
      </div>
      <div class="col-span-2">
        <div class="text-xs text-gray-400">Span ID</div>
        <div class="font-mono text-xs break-all">{{ selectedSpan.span_id }}</div>
      </div>
    </div>
    <div
      v-if="selectedSpan.name === 'prompt' && selectedPromptText"
      class="mb-4"
    >
      <div class="flex items-center justify-between gap-3 mb-1.5">
        <div class="text-xs text-gray-400">Prompt</div>
        <div class="flex items-center gap-2">
          <span class="text-[10px] font-mono text-slate-500">
            {{ selectedSpan.attributes.chars || selectedPromptText.length }} chars
          </span>
          <Button
            v-if="selectedPromptNeedsExpand"
            variant="link"
            size="sm"
            class="text-[11px]"
            @click="promptExpanded = !promptExpanded"
          >
            {{ promptExpanded ? 'Collapse' : 'Show full prompt' }}
          </Button>
        </div>
      </div>
      <div
        class="relative bg-slate-50 border border-slate-200 rounded px-3 py-2 text-sm text-slate-800 whitespace-pre-wrap break-words"
        :class="promptExpanded || !selectedPromptNeedsExpand ? 'max-h-[40rem] overflow-y-auto' : 'max-h-40 overflow-hidden'"
      >
        {{ selectedPromptText }}
        <div
          v-if="selectedPromptNeedsExpand && !promptExpanded"
          class="pointer-events-none absolute inset-x-0 bottom-0 h-12 rounded-b bg-gradient-to-t from-slate-50 to-transparent"
        />
      </div>
    </div>
    <!-- Assistant response: render markdown above the attributes table. -->
    <div
      v-if="selectedSpan.name === 'assistant_response' && selectedSpan.attributes.text"
      class="mb-4"
    >
      <div class="flex items-center justify-between mb-1">
        <div class="text-xs text-gray-400">Response</div>
        <span
          v-if="selectedSpan.attributes.truncated"
          class="text-[10px] text-amber-700 bg-amber-50 border border-amber-200 px-1.5 py-0.5 rounded"
          :title="`text capped at trace.assistant_response_max_bytes (${selectedSpan.attributes.response_chars} chars stored)`"
        >truncated</span>
      </div>
      <div class="bg-gray-50 border border-gray-200 rounded px-3 py-2 max-h-96 overflow-y-auto text-sm">
        <MarkdownContent :markdown="selectedSpan.attributes.text" />
      </div>
    </div>
    <!-- Rule check: header chip + per-rule pass/fail list. -->
    <div
      v-if="selectedSpan.name === 'rule.check'"
      class="mb-4 space-y-2"
    >
      <div class="flex items-center gap-2 flex-wrap">
        <span class="text-xs text-gray-400">rule check</span>
        <span
          v-for="(tag, ti) in (selectedSpan.attributes.engine_tags || [])"
          :key="ti"
          class="text-[10px] font-mono text-slate-700 bg-slate-100 border border-slate-200 px-1.5 py-0.5 rounded"
          :title="`engine: ${tag.engine}, language: ${tag.language}`"
        >{{ tag.engine }}·{{ tag.language }}</span>
        <span
          v-if="selectedSpan.attributes.status === 'violation'"
          class="text-[10px] font-semibold text-red-700 bg-red-50 border border-red-200 px-1.5 py-0.5 rounded uppercase tracking-wider"
        >⚠ {{ selectedSpan.attributes.violating_rule_count }} violation{{ selectedSpan.attributes.violating_rule_count === 1 ? '' : 's' }}</span>
        <span
          v-else-if="selectedSpan.attributes.status === 'no_applicable_rules'"
          class="text-[10px] font-semibold text-slate-500 bg-white border border-dashed border-slate-300 px-1.5 py-0.5 rounded uppercase tracking-wider"
          title="no rules applied to this file (check passed)"
        >ok · no applicable rules</span>
        <span
          v-else-if="selectedSpan.attributes.status === 'all_rules_out_of_scope'"
          class="text-[10px] font-semibold text-slate-500 bg-white border border-dashed border-slate-300 px-1.5 py-0.5 rounded uppercase tracking-wider"
          title="all configured rules are out of scope (check passed)"
        >ok · out of scope</span>
        <span
          v-else
          class="text-[10px] font-semibold text-emerald-700 bg-emerald-50 border border-emerald-200 px-1.5 py-0.5 rounded uppercase tracking-wider"
        >ok</span>
        <span class="text-xs text-slate-500 font-mono">
          {{ selectedSpan.attributes.applicable_rule_count || 0 }} of
          {{ selectedSpan.attributes.total_rules || 0 }} configured
        </span>
      </div>
      <div
        v-if="selectedSpan.attributes.relative_path"
        class="text-xs font-mono text-slate-700 break-all"
      >{{ selectedSpan.attributes.relative_path }}</div>
      <ul
        v-if="(selectedSpan.attributes.applicable_rules || []).length"
        class="border border-slate-200 rounded-md divide-y divide-slate-100 bg-white"
      >
        <li
          v-for="rule in selectedSpan.attributes.applicable_rules"
          :key="rule.id"
          class="px-3 py-2 text-sm flex items-start gap-2"
          :class="[
            rule.violated ? 'bg-red-50/50' : '',
            ruleTriggersByRuleId[rule.id]?.suppressed ? 'opacity-60' : '',
          ]"
        >
          <span
            class="shrink-0 mt-0.5 w-4 text-center font-mono text-xs"
            :class="rule.violated ? 'text-red-600' : 'text-emerald-600'"
            :title="rule.violated ? `${rule.match_count} match(es)` : 'no matches'"
          >{{ rule.violated ? '✗' : '✓' }}</span>
          <span
            class="min-w-0 flex-1"
            :class="ruleTriggersByRuleId[rule.id]?.suppressed ? 'line-through decoration-slate-300' : ''"
          >
            <span class="flex items-center gap-1.5 flex-wrap">
              <span class="font-mono text-[12px] text-slate-800">{{ rule.id }}</span>
              <span
                v-if="rule.severity"
                class="text-[10px] uppercase tracking-wider px-1 rounded border"
                :class="ruleSeverityClass(rule.severity)"
              >{{ rule.severity }}</span>
              <span
                v-if="rule.violated && rule.match_count > 1"
                class="text-[10px] text-red-600 font-mono tabular-nums"
              >×{{ rule.match_count }}</span>
              <span
                v-if="ruleTriggersByRuleId[rule.id]?.suppression"
                class="text-[10px] text-slate-500 italic"
                :title="ruleTriggersByRuleId[rule.id]?.suppression?.reason || 'no reason given'"
              >noise · {{ ruleTriggersByRuleId[rule.id]?.suppression?.suppressed_by_username }}</span>
            </span>
            <span
              v-if="rule.summary"
              class="block text-[12px] text-slate-600 mt-0.5"
            >{{ rule.summary }}</span>
            <span
              v-if="rule.guide"
              class="block text-[11px] font-mono text-blue-600 mt-0.5"
              :title="`guide: patterns/${rule.guide}.md`"
            >patterns/{{ rule.guide }}.md</span>
          </span>
          <SuppressButton
            v-if="canSuppressRule && ruleTriggersByRuleId[rule.id]"
            class="shrink-0 ml-1"
            :trigger-id="ruleTriggersByRuleId[rule.id].id"
            :suppressed="ruleTriggersByRuleId[rule.id].suppressed"
            :enabled="!!rule.violated"
            @changed="$emit('suppress-changed')"
          />
        </li>
      </ul>
    </div>

    <!-- Dynamic-workflow launch: jump to the captured run this Workflow call started. -->
    <div
      v-if="selectedSpan.name === 'tool.Workflow' && selectedSpan.attributes?.workflow_run_id"
      class="mb-4"
    >
      <div
        v-if="workflowRunsById[selectedSpan.attributes.workflow_run_id]"
        class="flex flex-wrap items-center gap-1.5 mb-2 text-[11px]"
      >
        <span
          v-if="workflowRunsById[selectedSpan.attributes.workflow_run_id].status"
          class="px-1.5 py-0.5 rounded border font-medium"
          :class="workflowRunsById[selectedSpan.attributes.workflow_run_id].status === 'running'
            ? 'bg-amber-50 border-amber-200 text-amber-700'
            : 'bg-emerald-50 border-emerald-200 text-emerald-700'"
        >{{ workflowRunsById[selectedSpan.attributes.workflow_run_id].status }}</span>
        <span class="px-1.5 py-0.5 rounded border border-slate-200 bg-slate-50 text-slate-600 font-mono">{{ workflowRunsById[selectedSpan.attributes.workflow_run_id].agent_count }} agent<span v-if="workflowRunsById[selectedSpan.attributes.workflow_run_id].agent_count !== 1">s</span></span>
        <span v-if="workflowRunsById[selectedSpan.attributes.workflow_run_id].phase_count" class="px-1.5 py-0.5 rounded border border-slate-200 bg-slate-50 text-slate-600 font-mono">{{ workflowRunsById[selectedSpan.attributes.workflow_run_id].phase_count }} phase<span v-if="workflowRunsById[selectedSpan.attributes.workflow_run_id].phase_count !== 1">s</span></span>
        <span v-if="workflowRunsById[selectedSpan.attributes.workflow_run_id].tokens" class="px-1.5 py-0.5 rounded border border-slate-200 bg-slate-50 text-slate-600 font-mono">{{ fmtTokens(workflowRunsById[selectedSpan.attributes.workflow_run_id].tokens) }} tok</span>
      </div>
      <router-link
        :to="`/trace/sessions/${selectedSpan.attributes.workflow_run_id}`"
        class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md border border-emerald-300 bg-emerald-50 text-sm font-medium text-emerald-700 hover:bg-emerald-100 no-underline focus-visible:outline-2 focus-visible:outline-emerald-500"
        title="Open the captured trace for this workflow run"
      >⚙ View workflow run →</router-link>
    </div>

    <!-- AskUserQuestion: render the Q&A round-trip as cards. -->
    <div
      v-if="selectedSpan.name === 'tool.AskUserQuestion' && selectedSpan.attributes.questions"
      class="mb-4 space-y-3"
    >
      <div class="text-xs text-gray-400 mb-1">
        Questions &amp; answers
        <span
          v-if="selectedSpan.attributes.denied"
          class="ml-1 text-[10px] uppercase tracking-wider bg-amber-100 border border-amber-200 text-amber-800 px-1 rounded"
        >{{ selectedSpan.attributes.deny_kind === 'chat' ? 'chat instead' : 'denied' }}</span>
      </div>
      <div
        v-for="(q, qi) in selectedSpan.attributes.questions"
        :key="qi"
        class="border border-slate-200 rounded-md overflow-hidden bg-white"
      >
        <div class="bg-slate-50 px-3 py-2 border-b border-slate-200">
          <div
            v-if="q.header"
            class="text-[10px] font-semibold uppercase tracking-wider text-slate-500 mb-0.5"
          >{{ q.header }}{{ q.multiSelect ? ' · multi-select' : '' }}</div>
          <div class="text-sm font-medium text-slate-800">{{ q.question }}</div>
        </div>
        <ul class="divide-y divide-slate-100">
          <li
            v-for="(opt, oi) in (q.options || [])"
            :key="oi"
            class="flex items-start gap-2 px-3 py-2 text-sm"
            :class="isChosenOption(q, opt) ? 'bg-green-50' : ''"
          >
            <span
              class="shrink-0 mt-0.5 w-4 text-center font-mono text-xs"
              :class="isChosenOption(q, opt) ? 'text-green-600' : 'text-slate-300'"
            >{{ isChosenOption(q, opt) ? '✓' : '○' }}</span>
            <span class="min-w-0 flex-1">
              <span
                class="block font-medium"
                :class="isChosenOption(q, opt) ? 'text-slate-900' : 'text-slate-800'"
              >{{ optLabel(opt) }}</span>
              <span
                v-if="optDescription(opt)"
                class="block text-slate-500 mt-0.5"
              >{{ optDescription(opt) }}</span>
              <details
                v-if="opt && opt.preview"
                class="mt-1"
                open
              >
                <summary class="cursor-pointer text-[10px] text-slate-500 hover:text-slate-700 select-none">Preview</summary>
                <pre class="mt-1 text-[11px] text-slate-700 bg-slate-50 border border-slate-200 rounded p-2 whitespace-pre-wrap break-words max-h-80 overflow-y-auto font-mono">{{ opt.preview }}</pre>
              </details>
            </span>
          </li>
          <!-- Free-text "Other" answer that didn't match any option -->
          <li
            v-if="freeTextAnswer(q)"
            class="flex items-start gap-2 px-3 py-1.5 text-sm bg-amber-50"
          >
            <span class="shrink-0 mt-0.5 w-4 text-center font-mono text-xs text-amber-600">✎</span>
            <span class="text-slate-900">{{ freeTextAnswer(q) }}</span>
          </li>
        </ul>
        <div
          v-if="annotationNote(q)"
          class="px-3 py-1.5 bg-slate-50 border-t border-slate-100 text-xs text-slate-600 italic"
        >
          Note: {{ annotationNote(q) }}
        </div>
      </div>
      <div
        v-if="selectedSpan.attributes.denied && selectedSpan.attributes.denial_reason"
        class="border border-amber-200 bg-amber-50 rounded-md px-3 py-2 text-sm text-slate-700 whitespace-pre-wrap"
      >
        <div
          class="text-[10px] font-semibold uppercase tracking-wider text-amber-700 mb-1"
          title="Templated text the agent harness (Claude Code) injects when the user denies a tool call — not user prose."
        >Denied (agent injected prompt)</div>
        {{ selectedSpan.attributes.denial_reason }}
      </div>
    </div>

    <!-- Generic-tool deny panel (any non-AskUserQuestion denied tool). -->
    <div
      v-if="selectedSpan.attributes?.denied
            && selectedSpan.name !== 'tool.AskUserQuestion'
            && selectedSpan.attributes.denial_reason"
      class="mb-4 border border-amber-200 bg-amber-50 rounded-md px-3 py-2 text-sm text-slate-700 whitespace-pre-wrap"
    >
      <div class="flex items-center gap-2 mb-1">
        <span
          class="text-[10px] font-semibold uppercase tracking-wider text-amber-700"
          title="Templated text the agent harness (Claude Code) injects when the user interrupts a tool call — not user prose."
        >Interrupted (agent injected prompt)</span>
        <span
          class="text-[10px] uppercase tracking-wider bg-amber-100 border border-amber-200 text-amber-800 px-1 rounded"
        >{{ selectedSpan.attributes.deny_kind === 'chat' ? 'chat instead' : 'Interrupted' }}</span>
      </div>
      {{ selectedSpan.attributes.denial_reason }}
    </div>
    <!-- MCP call: show the params asked and the result returned. -->
    <McpCallDetail v-if="mcpCall" :call="mcpCall" />
    <!-- send_to_user: jump to the rendered message in the Messages tab. -->
    <div v-if="isSendToUser" class="mb-4">
      <div class="text-xs text-gray-400 mb-1.5">Agent message</div>
      <button
        type="button"
        class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md border border-blue-300 bg-blue-50 text-sm font-medium text-blue-700 hover:bg-blue-100 focus-visible:outline-2 focus-visible:outline-blue-500"
        title="Open this message in the Messages tab, rendered as markdown"
        @click="$emit('view-message', selectedSpan)"
      >
        <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M22 12h-6l-2 3h-4l-2-3H2"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/></svg>
        View in Messages →
      </button>
    </div>
    <div v-if="visibleAttributeKeys.length">
      <div class="text-xs text-gray-400 mb-1">Attributes</div>
      <table class="w-full text-sm border-collapse table-fixed">
        <tbody>
          <tr v-for="key in visibleAttributeKeys" :key="key" class="border-b border-gray-100 align-top">
            <td class="py-1.5 pr-2 w-28 text-gray-500 font-mono text-xs break-all">{{ key }}</td>
            <td class="py-1.5 min-w-0">
              <code
                v-if="typeof selectedSpan.attributes[key] === 'string' && (selectedSpan.attributes[key].length > 60 || selectedSpan.attributes[key].includes('\n'))"
                class="text-xs bg-gray-50 px-1.5 py-1 rounded block whitespace-pre-wrap break-words max-h-80 overflow-y-auto"
              >{{ selectedSpan.attributes[key] }}</code>
              <code v-else class="text-xs bg-gray-50 px-1.5 py-0.5 rounded break-words">{{ selectedSpan.attributes[key] }}</code>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </Card>
  <Card v-else>
    <p class="text-sm text-gray-500">Select a span to see details.</p>
  </Card>
</template>
