<script setup>
import { ref, computed, nextTick, watch, provide, toRef } from 'vue'
import PromptBody from './PromptBody.vue'
import Icon from './ui/Icon.vue'
import ConversationToc from './conversation/ConversationToc.vue'
import ConversationSpanCard from './conversation/ConversationSpanCard.vue'
import TurnUsageFooter from './conversation/TurnUsageFooter.vue'
import RewindCard from './conversation/RewindCard.vue'
import { useSpanTree } from '../composables/useSpanTree.js'
import { useConversationPins } from '../composables/useConversationPins.js'
import { useConversationFolding } from '../composables/useConversationFolding.js'
import { useAgentLaunchMerge } from '../composables/useAgentLaunchMerge.js'
import { useCopy } from '../composables/useCopy.js'
import {
  fmtClock, fmtTokens, fullLabel, dotColor, toolDisplayName,
  promptPreviewText, promptPreviewMeta, isUnresolvedPrompt,
} from '../utils/traceFormatters.js'

const props = defineProps({
  spans: { type: Array, default: () => [] },
  turns: { type: Array, default: null },
  selectedSpan: { type: Object, default: null },
  traceId: { type: String, default: '' },
  contextWindowTokens: { type: Number, default: null },
  // run_id → enriched run summary (agent_count, phase_count, status, tokens)
  // from /workflow-runs, so an inline `tool.Workflow` row can render a rich
  // collapsed summary linking to the captured run.
  workflowRunsById: { type: Object, default: () => ({}) },
  // Set of span_ids whose `deep=1` subtree fetch has settled — lets a lazily
  // loaded card (e.g. RewindCard) tell "still loading" from "loaded, empty".
  loadedSubtrees: { default: () => new Set() },
  // Per-agent scope: a useLiveAgents roster entry, or null for the main feed.
  // While set, the spine collapses to that agent's single auto-expanded group.
  scopeAgent: { type: Object, default: null },
  // True while the scoped agent's subtree fetch is in flight — drives the
  // spinner-vs-unreachable split in the scoped empty state.
  scopeLoading: { type: Boolean, default: false },
  // agent_id currently open in the companion pane (≥xl), forwarded to the
  // subagent cards so the originating card shows the active-scope highlight.
  // Distinct from `scopeAgent`: the ≥xl main feed stays UNSCOPED (full feed)
  // and only highlights, while the pane renders the scoped projection.
  scopedAgentId: { type: String, default: '' },
  // The fixed follow-latest pill is a session-level affordance; the pane's
  // embedded scoped instance suppresses it so there's one, on the main feed.
  showFollowTail: { type: Boolean, default: true },
  // Scroll container for the pin/follow-tail machinery. Defaults to the
  // page-level `.content-scroll`; the ≥xl companion pane passes its own
  // scrollable element so its embedded instance never attaches listeners to —
  // or writes scrollTop / overflow-anchor on — the page scroller.
  scrollerGetter: { type: Function, default: null },
  // True while the companion pane is open on a viewport too narrow for three
  // columns (xl but not 2xl): the TOC rail (user-resizable, shrink-0) yields
  // so the feed column keeps a readable width beside the pane.
  hideToc: { type: Boolean, default: false },
})

const emit = defineEmits(['select-span', 'fetch-content', 'load-subtree', 'jump-live', 'enter-scope'])

// Active-scope highlight signal for the descendant SubagentCards (≥xl split):
// provided rather than prop-threaded through the ConversationSpanCard
// dispatcher. The pane's embedded scoped instance passes '', so only the main
// feed's originating card lights up.
provide('traceScopedAgentId', toRef(props, 'scopedAgentId'))

const { copyText } = useCopy()

// Tracks span_ids we've already asked the parent to fetch content for, so the
// lazy fetch fires at most once per span.
const requestedContent = ref(new Set())

// DOM ref maps — populated by inline `:ref` callbacks in THIS template and read
// by the pin/scroll/cross-highlight logic. They stay in the orchestrator on
// purpose; child cards render only bodies and never see these maps.
const promptRefs = ref(new Map())
const spanRefs = ref(new Map())
// Standalone (non-prompt) root entries — rare (legacy background-task
// notifications that didn't get grafted under a prompt) + the compaction
// dividers. Tracked separately so the cross-highlight watcher can scroll them.
const standaloneRefs = ref(new Map())

// Span tree derivations: lookup maps, roots, recursive descendants, prompt
// groups, and the turnItems / phaseItems metadata the TOC and spine consume.
const {
  spanById, childrenOf,
  entries, promptGroups, turnItems, isWorkflow, phaseItems,
  hasPhaseSpans, phasePlan,
} = useSpanTree(() => props.spans, () => props.turns, () => (props.scopeAgent
  ? {
      startSpanId: props.scopeAgent.spanId,
      agentId: props.scopeAgent.agentId,
      promptText: props.scopeAgent.promptPreview || props.scopeAgent.description || '',
      spanCount: props.scopeAgent.spanCount,
      running: props.scopeAgent.running,
    }
  : null))

// Workflow-run projections emit a synthetic workflow.agent_result AFTER each
// agent's turns (_pushAgent), so SubagentCard must not render its own result
// card there — provided so the flag skips the dispatcher's prop chain.
provide('traceIsWorkflowRun', isWorkflow)

// A surviving `promptlive-` placeholder is only "stranded" once the session
// can no longer resolve it — i.e. it has ended. While live, the newest
// placeholder is just the in-flight prompt. Gate the unresolved styling on
// `session.end` being present so a live prompt is never flagged. See
// `isUnresolvedPrompt`.
const sessionEnded = computed(() => props.spans.some(s => s.name === 'session.end'))
function promptUnresolved(prompt) {
  return sessionEnded.value && isUnresolvedPrompt(prompt)
}

// ── Pin a span / follow tail ──────────────────────────────────
// Pin holds a chosen span at its on-screen position across the live poll;
// follow-tail auto-sticks to the newest span like a terminal.
function resolvePinEl(spanId) {
  return spanRefs.value.get(spanId)
    || promptRefs.value.get(spanId)
    || standaloneRefs.value.get(spanId)
    || null
}
function getConversationScroller() {
  if (props.scrollerGetter) return props.scrollerGetter()
  return document.querySelector('.content-scroll')
    || document.scrollingElement
    || document.documentElement
}
// Force-expand the prompt/agent that owns a freshly-pinned span so the
// auto-fold watcher can't collapse the pinned row out of the DOM.
function ensurePinVisible(spanId) {
  const owner = promptGroups.value.find(g =>
    g.prompt.span_id === spanId || g.descendants.some(d => d.span.span_id === spanId))
  if (owner && !isPromptExpanded(owner.prompt.span_id)) togglePromptExpanded(owner.prompt.span_id, true)
  const agentId = agentAncestorId(spanId)
  if (agentId && !isAgentExpanded(agentId)) toggleAgentExpanded(agentId)
}
function promptOwnsPinned(promptId) {
  const pid = pinnedSpanId.value
  if (!pid) return false
  if (promptId === pid) return true
  const g = promptGroups.value.find(g => g.prompt.span_id === promptId)
  return !!g && g.descendants.some(d => d.span.span_id === pid)
}
const {
  pinnedSpanId, followTail, atBottom, newSinceScroll,
  isPinnable, togglePin, enableFollow, disableFollow,
} = useConversationPins({
  spans: () => props.spans,
  resolveEl: resolvePinEl,
  getScroller: getConversationScroller,
  onPinExpand: ensurePinVisible,
})

// Subagent launch merge + the agent-metadata helpers the subagent / phase /
// result cards consume (passed down as the `agentMerge` object).
const agentMerge = useAgentLaunchMerge(() => props.spans, childrenOf)

// All expand/collapse state. Two callbacks keep it decoupled from this
// component's emit + content-fetch concerns.
const folding = useConversationFolding({
  getSpans: () => props.spans,
  childrenOf,
  onLoadSubtree: (id) => emit('load-subtree', id),
  onExpandPrompt: (id) => { emit('load-subtree', id); fetchMissingContentForPrompt(id) },
})
const {
  expandedPromptIds,
  isPromptExpanded, isPromptBodyExpanded, togglePromptExpanded, togglePromptBodyExpanded,
  isAgentExpanded, toggleAgentExpanded, isWorkflowExpanded,
  isRewindExpanded, toggleRewindExpanded,
  foldableAgentIds, allAgentsExpanded, expandAllAgents, collapseAllAgents,
} = folding

// Context bundle the discarded-branch span cards (rendered inside RewindCard)
// need — passed as one prop so the marker's template footprint stays small.
const rewindCtx = computed(() => ({
  selectedSpan: props.selectedSpan,
  folding,
  agentMerge,
  workflowRunsById: props.workflowRunsById,
}))

// Climb the parent_id chain to the nearest enclosing agent (inclusive), so a
// span selected from the TOC or a deep-link can auto-expand the agent that
// owns it.
function agentAncestorId(spanId) {
  let cur = spanById.value.get(spanId)
  while (cur) {
    if (cur.name === 'subagent.start') return cur.span_id
    cur = cur.parent_id ? spanById.value.get(cur.parent_id) : null
  }
  return null
}

// Fold collapsed agents/workflows out of an expanded prompt's descendant list.
// Reads the fold predicates so it stays reactive — keep it here (not in a
// card) so toggling a chevron reflows the spine and re-registers spanRefs.
function renderableDescendants(entry, keepRewound = false) {
  const merged = agentMerge.agentLaunchMerge.value.merged
  // Discarded `/rewind` turns are surfaced only behind their marker — never
  // inline in the live branch — so drop any that slipped into a descendant
  // list. `keepRewound` is the one exception: the marker's own card renders
  // its discarded subtree, so it asks for those spans back.
  const live = keepRewound
    ? entry.descendants
    : entry.descendants.filter(({ span }) => !span.attributes?.rewound_away)
  const base = merged.size
    ? live.filter(({ span }) => !merged.has(span.span_id))
    : live
  // Every row inside an agent carries `inAgent` (set once flattenDescendants
  // crosses a `subagent.start`), so a collapsed agent's rows are a contiguous
  // run from its header to the next agent/phase. Drop those — keep the header.
  const visible = []
  let collapsing = false
  let collapsingWorkflow = false
  for (const d of base) {
    // Workflow fold takes precedence: a collapsed `tool.Workflow` hides its
    // entire subtree behind the single workflow card. Keep the card row.
    if (d.span.name === 'tool.Workflow') {
      collapsingWorkflow = !isWorkflowExpanded(d.span.span_id)
      collapsing = false
      visible.push(d)
      continue
    }
    if (d.inWorkflow) {
      if (collapsingWorkflow) continue
    } else {
      collapsingWorkflow = false
    }
    if (d.span.name === 'subagent.start') {
      collapsing = !isAgentExpanded(d.span.span_id)
      visible.push(d)
      continue
    }
    if (!d.inAgent) { collapsing = false; visible.push(d); continue }
    if (collapsing) continue
    visible.push(d)
  }
  // Flag each agent header that should get a separator line above it: between
  // consecutive agents, but NOT the first agent of a phase nor the first item.
  return visible.map((d, i) => ({
    ...d,
    agentSep: d.span.name === 'subagent.start' && i > 0
      && visible[i - 1].span.name !== 'workflow.phase',
  }))
}

// ── Auto-expand the latest prompt ─────────────────────────────
// Land the reader on the in-progress turn; fold the previously auto-expanded
// one when a newer prompt arrives. `lastAutoExpandedId` tracks what we
// auto-opened so a manual collapse of the latest prompt sticks.
const lastAutoExpandedId = ref(null)
watch(promptGroups, (groups) => {
  if (!groups.length) return
  const latest = groups[groups.length - 1].prompt
  if (lastAutoExpandedId.value === latest.span_id) return
  // Don't auto-fold the turn that owns a pinned span — the pinned row must
  // stay mounted for the scroll anchor to track it.
  if (lastAutoExpandedId.value && !promptOwnsPinned(lastAutoExpandedId.value)) {
    expandedPromptIds.value.delete(lastAutoExpandedId.value)
  }
  expandedPromptIds.value.add(latest.span_id)
  lastAutoExpandedId.value = latest.span_id
  emit('load-subtree', latest.span_id)
  fetchMissingContentForPrompt(latest.span_id)
}, { immediate: true })

// ── Selection + scroll (targets live in this component) ───────
function onSelectSpan(span) { emit('select-span', span) }
// User clicked a card body → select + lazily fetch its content.
function onActivate(span) { onSelectSpan(span); maybeFetchContent(span) }

function onPromptClick(prompt) {
  // Skip the toggle when the click ended a drag-selection — otherwise
  // selecting prompt text to copy would also fold the turn.
  if (typeof window !== 'undefined') {
    const sel = window.getSelection?.()
    if (sel && sel.toString().length > 0) return
  }
  emit('select-span', prompt)
  maybeFetchContent(prompt)
  togglePromptExpanded(prompt.span_id)
}

async function selectTurn(item) {
  emit('select-span', item.prompt || spanById.value.get(item.promptSpanId))
  maybeFetchContent(spanById.value.get(item.promptSpanId))
  togglePromptExpanded(item.promptSpanId, true)
  await nextTick()
  const el = promptRefs.value.get(item.promptSpanId)
  if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

// Phase/agent TOC click (workflow runs): select the span and scroll its row
// (phase divider or agent card) into view via the shared spanRefs map.
async function selectWorkflowRow(spanId) {
  const span = spanById.value.get(spanId)
  if (span) { emit('select-span', span); maybeFetchContent(span) }
  await nextTick()
  const el = spanRefs.value.get(spanId)
  if (el && typeof el.scrollIntoView === 'function') {
    el.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }
}

// Cross-highlight from external selection (e.g. clicking a colored bar in the
// parent's mini-timeline strip): scroll the matching prompt — or the prompt
// that owns the selected descendant — into view. `flush: 'post'` so the
// fold-toggle reflow (re-registering spanRefs) lands before we read the ref.
watch([() => props.selectedSpan?.span_id, () => props.spans.length], async ([id]) => {
  if (!id) return
  const owner = promptGroups.value.find(g =>
    g.descendants.some(d => d.span.span_id === id)
  )
  if (owner && !isPromptExpanded(owner.prompt.span_id)) {
    togglePromptExpanded(owner.prompt.span_id, true)
    await nextTick()
  }
  // Folded agent: if the selected span lives inside (or is) a collapsed agent,
  // expand it so its row exists to scroll to.
  const agentId = agentAncestorId(id)
  if (agentId && !isAgentExpanded(agentId)) {
    toggleAgentExpanded(agentId)
    await nextTick()
  }
  await nextTick()
  const spanEl = spanRefs.value.get(id)
  if (spanEl && typeof spanEl.scrollIntoView === 'function') {
    spanEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    return
  }
  const direct = promptRefs.value.get(id) || standaloneRefs.value.get(id)
  if (direct && typeof direct.scrollIntoView === 'function') {
    direct.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    return
  }
  const el = owner && promptRefs.value.get(owner.prompt.span_id)
  if (el && typeof el.scrollIntoView === 'function') {
    el.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  }
}, { flush: 'post' })

// ── On-demand content fetching ────────────────────────────────
function needsContentFetch(span) {
  if (!span) return false
  const a = span.attributes || {}
  return (
    (span.name === 'prompt' && !a.text) ||
    (span.name === 'assistant_response' && !a.text) ||
    // memory.recall carries the injected block lazily — fetch it so the
    // MemoryRecallRow `block` toggle and the detail panel can show it.
    (span.name === 'memory.recall' && !a.block) ||
    // Server-side tools (e.g. advisor) carry their textual response in
    // `response_text` rather than producing a tool_result.
    (a.server_side && !a.response_text) ||
    (span.name === 'tool.advisor' && !a.response_text)
  )
}
function maybeFetchContent(span) {
  if (!needsContentFetch(span)) return
  if (requestedContent.value.has(span.span_id)) return
  requestedContent.value.add(span.span_id)
  emit('fetch-content', span.span_id)
}
function fetchMissingContentForPrompt(promptSpanId) {
  const entry = promptGroups.value.find(g => g.prompt.span_id === promptSpanId)
  if (!entry) return
  maybeFetchContent(entry.prompt)
  for (const { span } of entry.descendants) maybeFetchContent(span)
}

// ── Tool-chip categorization (collapsed-turn preview) ─────────
// Group descendant spans into semantic chips so the user sees
// `Read×2  Edit×4  doc-hygiene` instead of an unstructured wall.
function toolChipsForEntry(entry) {
  const buckets = new Map() // label -> { count, color }
  function add(label, color) {
    const cur = buckets.get(label) || { count: 0, color }
    cur.count++
    buckets.set(label, cur)
  }
  for (const { span } of entry.descendants) {
    const n = span.name || ''
    if (n.startsWith('tool.')) {
      add(toolDisplayName(n.slice(5)), 'bg-blue-50 text-blue-700 border-blue-200')
    } else if (n === 'skill.read' || n === 'skill.invoke') {
      add(span.attributes?.skill_id || 'skill', 'bg-green-50 text-green-700 border-green-200')
    } else if (n === 'file.edit' || n === 'plan.edit') {
      add('Edit', 'bg-orange-50 text-orange-700 border-orange-200')
    } else if (n === 'rule.check') {
      add('rule', 'bg-red-50 text-red-700 border-red-200')
    } else if (n === 'memory.recall') {
      add('recall', 'bg-fuchsia-50 text-fuchsia-700 border-fuchsia-200')
    } else if (n.startsWith('subagent.')) {
      add('subagent', 'bg-pink-50 text-pink-700 border-pink-200')
    }
  }
  return Array.from(buckets.entries()).map(([label, v]) => ({ label, ...v }))
}
</script>

<template>
  <div class="flex gap-4 items-start">
    <!-- ──────── LEFT RAIL: TURNS / PHASES TOC ──────── -->
    <ConversationToc
      v-if="!scopeAgent && !hideToc"
      :is-workflow="isWorkflow"
      :has-phase-spans="hasPhaseSpans"
      :phase-items="phaseItems"
      :phase-plan="phasePlan"
      :turn-items="turnItems"
      :selected-span="selectedSpan"
      :foldable-agent-ids="foldableAgentIds"
      :all-agents-expanded="allAgentsExpanded"
      @select-turn="selectTurn"
      @select-workflow-row="selectWorkflowRow"
      @jump-live="$emit('jump-live')"
      @expand-all-agents="expandAllAgents"
      @collapse-all-agents="collapseAllAgents"
    />

    <!-- ──────── CENTER COLUMN: CHAT DOCUMENT ──────── -->
    <div class="flex-1 min-w-0 font-sans text-sm leading-relaxed space-y-5">
      <div
        v-for="(entry, entryIdx) in entries"
        :key="entryIdx"
      >
        <!-- Prompt group -->
        <div
          v-if="entry.type === 'group'"
          :ref="(el) => { if (el) promptRefs.set(entry.prompt.span_id, el); else promptRefs.delete(entry.prompt.span_id) }"
          class="space-y-2"
          :style="{ scrollMarginTop: 'calc(var(--regin-trace-header-h, 5rem) + 0.75rem)' }"
        >
          <!-- USER prompt card (purple tint) -->
          <div
            class="group relative rounded-md border bg-purple-50 border-purple-200 px-3 py-2 cursor-pointer hover:border-purple-300 transition-colors"
            :class="[
              selectedSpan && selectedSpan.span_id === entry.prompt.span_id ? 'ring-2 ring-purple-300' : '',
              pinnedSpanId === entry.prompt.span_id ? 'ring-2 ring-amber-400' : '',
              promptUnresolved(entry.prompt) ? 'border-dashed !border-amber-300 !bg-amber-50/50' : '',
            ]"
            @click="onPromptClick(entry.prompt)"
          >
            <button
              v-if="isPinnable(entry.prompt)"
              type="button"
              class="absolute -left-4 top-1.5 z-10 w-5 h-5 flex items-center justify-center rounded text-[11px] leading-none transition-opacity focus-visible:outline-2 focus-visible:outline-amber-400"
              :class="pinnedSpanId === entry.prompt.span_id ? 'opacity-100' : 'opacity-0 group-hover:opacity-100 grayscale'"
              :title="pinnedSpanId === entry.prompt.span_id ? 'Unpin from view' : 'Pin to view (hold position across updates)'"
              :aria-pressed="pinnedSpanId === entry.prompt.span_id"
              @click.stop="togglePin(entry.prompt.span_id)"
            >📌</button>
            <div class="flex items-center gap-2 text-[11px] font-mono text-purple-700/80 mb-0.5">
              <span class="font-semibold uppercase tracking-wider text-[10px]">USER</span>
              <span
                v-if="promptUnresolved(entry.prompt)"
                class="px-1 rounded bg-amber-200/70 text-amber-800 text-[9px] font-semibold uppercase tracking-wider not-italic"
                title="Unresolved — a live prompt placeholder whose real anchor never landed. Usually a scheduled/loop wakeup (delivered as a plain prompt, never anchored) or an interrupted final prompt, not a turn the user typed."
              >unresolved</span>
              <span class="text-purple-300">·</span>
              <span>{{ fmtClock(entry.prompt.start_time) }}</span>
              <button
                v-if="entry.prompt.attributes?.text && isPromptBodyExpanded(entry.prompt.span_id)"
                type="button"
                class="ml-auto inline-flex items-center gap-1 transition-colors px-1.5 py-0.5 rounded text-[10px] text-purple-600 hover:bg-purple-200/60 focus-visible:outline-2 focus-visible:outline-purple-400"
                title="Collapse full prompt"
                @click.stop="togglePromptBodyExpanded(entry.prompt.span_id)"
              ><Icon name="chevron-down" :size="12" class="rotate-180" />Collapse</button>
              <button
                v-if="entry.prompt.attributes?.text"
                type="button"
                class="ml-auto opacity-0 group-hover:opacity-100 transition-opacity px-1.5 py-0.5 rounded text-[10px] text-purple-600 hover:bg-purple-200/60 focus-visible:outline-2 focus-visible:outline-purple-400"
                title="Copy"
                @click.stop="copyText(entry.prompt.attributes.text)"
              >Copy</button>
            </div>
            <div
              v-if="entry.prompt.attributes?.text && isPromptBodyExpanded(entry.prompt.span_id)"
              class="max-h-[60vh] overflow-y-auto rounded border border-purple-200/70 bg-white/40 px-2 py-1"
            >
              <PromptBody
                :text="entry.prompt.attributes.expanded_text || entry.prompt.attributes.text"
                :trace-id="traceId"
                :span-id="entry.prompt.span_id"
                :image-indices="entry.prompt.attributes?.image_indices || []"
              />
            </div>
            <PromptBody
              v-if="entry.prompt.attributes?.text && !isPromptBodyExpanded(entry.prompt.span_id)"
              :text="promptPreviewText(entry.prompt)"
              :trace-id="traceId"
              :span-id="entry.prompt.span_id"
              :image-indices="entry.prompt.attributes?.image_indices || []"
            />
            <div
              v-if="entry.prompt.attributes?.text && !isPromptBodyExpanded(entry.prompt.span_id) && (promptPreviewMeta(entry.prompt).truncated || promptPreviewMeta(entry.prompt).imageCount)"
              class="mt-1 flex flex-wrap items-center gap-2 text-[11px] text-purple-700/70"
            >
              <button
                v-if="promptPreviewMeta(entry.prompt).truncated"
                type="button"
                class="inline-flex items-center gap-1 rounded border border-purple-200 bg-white/70 px-1.5 py-0.5 text-purple-600 hover:bg-purple-200/60 hover:border-purple-300 focus-visible:outline-2 focus-visible:outline-purple-400 transition-colors"
                title="Show full prompt"
                @click.stop="togglePromptBodyExpanded(entry.prompt.span_id)"
              ><Icon name="chevron-down" :size="12" />Show full prompt</button>
              <span
                v-if="promptPreviewMeta(entry.prompt).imageCount"
                class="inline-flex items-center rounded border border-purple-200 bg-white/70 px-1.5 py-0.5"
                :title="promptPreviewMeta(entry.prompt).imageTokens
                  ? `Estimated image cost: ~${promptPreviewMeta(entry.prompt).imageTokens} tokens (rolled into the next turn's input_tokens / cache_creation_input_tokens by Anthropic)`
                  : null"
              >{{ promptPreviewMeta(entry.prompt).imageCount }} image<span v-if="promptPreviewMeta(entry.prompt).imageCount > 1">s</span><span
                v-if="promptPreviewMeta(entry.prompt).imageTokens"
                class="ml-1 text-purple-600/80"
              >· ~{{ promptPreviewMeta(entry.prompt).imageTokens }} tok</span></span>
            </div>
            <span v-if="!entry.prompt.attributes?.text" class="text-purple-700">{{ fullLabel(entry.prompt) }}</span>
          </div>

          <!-- Tool/skill chips for this turn (collapsed preview) -->
          <div
            v-if="!isPromptExpanded(entry.prompt.span_id)"
            class="flex flex-wrap items-center gap-1.5 pl-2"
          >
            <span
              v-for="chip in toolChipsForEntry(entry)"
              :key="chip.label"
              class="inline-flex items-center gap-0.5 px-1.5 py-0.5 text-[11px] rounded border"
              :class="chip.color"
            >{{ chip.label }}<span v-if="chip.count > 1" class="opacity-70 ml-0.5">×{{ chip.count }}</span></span>
            <button
              type="button"
              class="text-[11px] text-slate-400 hover:text-slate-700 ml-1 cursor-pointer rounded px-1 focus-visible:outline-2 focus-visible:outline-blue-500"
              @click="togglePromptExpanded(entry.prompt.span_id)"
            >show details ▾</button>
          </div>

          <!-- Descendant cards (when expanded). The `:ref` wrapper + pin button
               stay here; ConversationSpanCard renders only the card body. -->
          <template v-if="isPromptExpanded(entry.prompt.span_id)">
            <template v-for="{ span, inAgent, agentSep } in renderableDescendants(entry)" :key="span.span_id">
              <div
                :ref="(el) => { if (el) spanRefs.set(span.span_id, el); else spanRefs.delete(span.span_id) }"
                class="relative group/pin"
                :class="[
                  inAgent ? 'border-l-2 border-pink-300 ml-1.5 pl-2.5' : '',
                  agentSep ? 'border-t-2 border-slate-200 mt-6 pt-5' : '',
                  pinnedSpanId === span.span_id ? 'rounded-md ring-2 ring-amber-400' : '',
                ]"
              >
                <button
                  v-if="isPinnable(span)"
                  type="button"
                  class="absolute -left-4 top-1 z-10 w-5 h-5 flex items-center justify-center rounded text-[11px] leading-none transition-opacity focus-visible:outline-2 focus-visible:outline-amber-400"
                  :class="pinnedSpanId === span.span_id ? 'opacity-100' : 'opacity-0 group-hover/pin:opacity-100 grayscale'"
                  :title="pinnedSpanId === span.span_id ? 'Unpin from view' : 'Pin to view (hold position across updates)'"
                  :aria-pressed="pinnedSpanId === span.span_id"
                  @click.stop="togglePin(span.span_id)"
                >📌</button>
                <ConversationSpanCard
                  :span="span"
                  :selected-span="selectedSpan"
                  :folding="folding"
                  :agent-merge="agentMerge"
                  :workflow-runs-by-id="workflowRunsById"
                  @activate="onActivate"
                  @enter-scope="$emit('enter-scope', $event)"
                />
              </div>
            </template>

            <!-- Collapse trigger when expanded -->
            <button
              type="button"
              class="text-[11px] text-slate-400 hover:text-slate-700 pl-2 cursor-pointer rounded px-1 focus-visible:outline-2 focus-visible:outline-blue-500"
              @click="togglePromptExpanded(entry.prompt.span_id)"
            >hide details ▴</button>
          </template>

          <!-- Turn metadata footer: rollup of every API turn this prompt drove -->
          <TurnUsageFooter
            v-if="turnItems[entryIdx]?.turnAgg"
            :key="entry.prompt.span_id"
            :item="turnItems[entryIdx]"
            :context-window-tokens="contextWindowTokens"
          />
        </div>

        <!-- Rewind boundary divider (collapsed discarded branch + file rollback) -->
        <div
          v-else-if="entry.span.name === 'rewind'"
          :ref="(el) => { if (el) standaloneRefs.set(entry.span.span_id, el); else standaloneRefs.delete(entry.span.span_id) }"
        >
          <RewindCard
            :span="entry.span"
            :trace-id="props.traceId"
            :descendants="renderableDescendants(entry, true)"
            :ctx="rewindCtx"
            :expanded="isRewindExpanded(entry.span.span_id)"
            :loaded="props.loadedSubtrees.has(entry.span.span_id)"
            @select="onSelectSpan"
            @toggle="toggleRewindExpanded(entry.span.span_id)"
            @activate="onActivate"
            @enter-scope="$emit('enter-scope', $event)"
          />
        </div>

        <!-- Compaction boundary divider (PreCompact / PostCompact) -->
        <div
          v-else-if="entry.span.name === 'compact.pre' || entry.span.name === 'compact.post'"
          :ref="(el) => { if (el) standaloneRefs.set(entry.span.span_id, el); else standaloneRefs.delete(entry.span.span_id) }"
          class="my-3 cursor-pointer group rounded transition-colors hover:bg-amber-50/60 focus-visible:outline-2 focus-visible:outline-amber-400"
          :class="selectedSpan && selectedSpan.span_id === entry.span.span_id ? 'ring-2 ring-amber-300' : ''"
          tabindex="0"
          role="button"
          @click="onSelectSpan(entry.span)"
          @keydown.enter.prevent="onSelectSpan(entry.span)"
          @keydown.space.prevent="onSelectSpan(entry.span)"
        >
          <div class="flex items-center gap-2 text-amber-700">
            <div class="flex-1 border-t border-dashed border-amber-300"></div>
            <span class="inline-flex items-center gap-1.5 text-[11px] font-mono uppercase tracking-wider whitespace-nowrap px-2 py-0.5 rounded bg-amber-50 border border-amber-200">
              <span>{{ entry.span.name === 'compact.pre' ? '▼ context compacting' : '▲ context compacted' }}</span>
              <span v-if="entry.span.attributes?.trigger" class="text-amber-500">·</span>
              <span v-if="entry.span.attributes?.trigger" class="lowercase">{{ entry.span.attributes.trigger }}</span>
              <span
                v-if="entry.span.attributes?.reclaimed_tokens"
                class="text-emerald-700 normal-case"
                :title="'context tokens reclaimed: last turn before compaction minus first turn after (measured from turn_usage.context_used_tokens)'"
              >· freed ~{{ fmtTokens(entry.span.attributes.reclaimed_tokens) }}</span>
              <span class="text-amber-400">·</span>
              <span class="text-amber-600 normal-case">{{ fmtClock(entry.span.start_time) }}</span>
            </span>
            <div class="flex-1 border-t border-dashed border-amber-300"></div>
          </div>
          <div
            v-if="entry.span.attributes?.custom_instructions"
            class="mt-1 text-xs text-amber-800/80 italic text-center px-4 break-words"
          >
            “{{ entry.span.attributes.custom_instructions }}”
          </div>
          <div
            v-if="entry.span.attributes?.summary"
            class="mt-1 text-[11px] text-slate-500 px-4"
          >
            <div v-if="!isPromptBodyExpanded(entry.span.span_id)" class="text-center break-words">
              <span class="text-slate-600">summary:</span>
              {{ entry.span.attributes.summary.slice(0, 180) }}{{ entry.span.attributes.summary.length > 180 ? '…' : '' }}
              <span v-if="entry.span.attributes.summary_chars" class="text-slate-400">({{ entry.span.attributes.summary_chars.toLocaleString() }} chars)</span>
              <button
                v-if="entry.span.attributes.summary.length > 180"
                type="button"
                class="ml-1 text-amber-600 hover:text-amber-700 hover:underline cursor-pointer focus-visible:outline-2 focus-visible:outline-amber-400 rounded"
                @click.stop="togglePromptBodyExpanded(entry.span.span_id)"
              >show full ▾</button>
            </div>
            <div v-else>
              <div class="flex items-center justify-between mb-1">
                <span class="text-slate-600 font-medium">summary <span v-if="entry.span.attributes.summary_chars" class="text-slate-400 font-normal">({{ entry.span.attributes.summary_chars.toLocaleString() }} chars)</span></span>
                <button
                  type="button"
                  class="text-amber-600 hover:text-amber-700 hover:underline cursor-pointer focus-visible:outline-2 focus-visible:outline-amber-400 rounded"
                  @click.stop="togglePromptBodyExpanded(entry.span.span_id)"
                >collapse ▴</button>
              </div>
              <pre class="whitespace-pre-wrap break-words text-slate-600 bg-amber-50/30 border border-amber-100 rounded px-3 py-2 max-h-[60vh] overflow-y-auto font-sans">{{ entry.span.attributes.summary }}</pre>
            </div>
          </div>
        </div>

        <!-- Standalone root (non-prompt) -->
        <div
          v-else
          :ref="(el) => { if (el) standaloneRefs.set(entry.span.span_id, el); else standaloneRefs.delete(entry.span.span_id) }"
          class="relative group/pin flex items-center gap-2 text-xs text-slate-500 px-2 py-1 rounded cursor-pointer hover:bg-slate-50"
          :class="pinnedSpanId === entry.span.span_id ? 'ring-2 ring-amber-400' : ''"
          @click="onSelectSpan(entry.span)"
        >
          <button
            v-if="isPinnable(entry.span)"
            type="button"
            class="absolute -left-4 top-1 z-10 w-5 h-5 flex items-center justify-center rounded text-[11px] leading-none transition-opacity focus-visible:outline-2 focus-visible:outline-amber-400"
            :class="pinnedSpanId === entry.span.span_id ? 'opacity-100' : 'opacity-0 group-hover/pin:opacity-100 grayscale'"
            :title="pinnedSpanId === entry.span.span_id ? 'Unpin from view' : 'Pin to view (hold position across updates)'"
            :aria-pressed="pinnedSpanId === entry.span.span_id"
            @click.stop="togglePin(entry.span.span_id)"
          >📌</button>
          <span class="inline-block w-1.5 h-1.5 rounded-full shrink-0" :class="dotColor(entry.span.name)"></span>
          <span class="font-mono text-[11px]">{{ fmtClock(entry.span.start_time) }}</span>
          <span class="break-words">{{ fullLabel(entry.span) }}</span>
        </div>
      </div>

      <!-- Empty state. While scoped, "no spans ever captured" (server
           span_count 0) and "spans exist but the subtree fetch is in flight"
           are different states — never flash a false empty for the latter. -->
      <div v-if="!entries.length" class="text-slate-400 text-center py-8">
        <span v-if="scopeAgent && !scopeAgent.spanCount && !scopeAgent.running" data-testid="trace-scope-empty">no spans captured for this agent</span>
        <span v-else-if="scopeAgent && (scopeLoading || scopeAgent.running)" data-testid="trace-scope-loading" class="inline-flex items-center gap-2">
          <svg class="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="2" stroke-opacity="0.25"/>
            <path d="M22 12a10 10 0 0 1-10 10" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
          </svg>
          Loading agent spans…
        </span>
        <span v-else-if="scopeAgent" data-testid="trace-scope-unreachable">agent spans not loaded — load earlier history to view</span>
        <template v-else>No events recorded for this session.</template>
      </div>
    </div>

    <!-- Follow-tail pill: terminal-style stick-to-newest. Hidden once you're
         already parked at the bottom and not following; while scrolled up it
         surfaces a count of spans that arrived since. -->
    <button
      v-if="showFollowTail && (followTail || !atBottom)"
      type="button"
      class="fixed bottom-6 left-1/2 -translate-x-1/2 z-20 inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-[12px] font-medium shadow-lg transition-colors focus-visible:outline-2 focus-visible:outline-orange-400"
      :class="followTail
        ? 'bg-orange-500 border-orange-600 text-white hover:bg-orange-600'
        : 'bg-white border-orange-300 text-orange-700 hover:bg-orange-50'"
      :title="followTail ? 'Stop following the latest activity' : 'Jump to and follow the latest activity'"
      @click="followTail ? disableFollow() : enableFollow()"
    >
      <span aria-hidden="true">⤓</span>
      <span>{{ followTail ? 'Following' : 'Follow latest' }}</span>
      <span
        v-if="!followTail && newSinceScroll > 0"
        class="inline-flex items-center justify-center min-w-[1rem] h-4 px-1 rounded-full bg-orange-500 text-white text-[10px] tabular-nums"
      >{{ newSinceScroll > 99 ? '99+' : newSinceScroll }}</span>
    </button>
  </div>
</template>
