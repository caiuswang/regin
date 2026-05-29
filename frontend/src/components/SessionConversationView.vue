<script setup>
import { computed, ref, nextTick, watch } from 'vue'
import MarkdownContent from './MarkdownContent.vue'
import PromptBody from './PromptBody.vue'
import DiffBlock from './DiffBlock.vue'
import { useFlash } from '../composables/useFlash.js'
import { useSpanTree } from '../composables/useSpanTree.js'
import { useStickyMaxHeight } from '../composables/useStickyMaxHeight.js'
import {
  fmtTime, fmtClock, fmtDuration, fmtTokens, fmtModel, fmtBytes, truncate,
  toolDisplayName, toolFilePath, ruleCheckOneLiner, fullLabel, mcpParts,
  dotColor, isRejectedToolSpan, isDeniedToolSpan, isErrorToolSpan,
  toolRowDotClass, toolRowTextClass,
  taskRowStatus, taskRowIcon, taskRowIconClass,
  diffOpLabel, diffFileName,
  promptPreviewText, promptPreviewMeta,
  askOptLabel, askOptDescription, askIsChosen, askFreeText, askNote,
} from '../utils/traceFormatters.js'

const { flash } = useFlash()

function copyText(text) {
  if (!text) return
  navigator.clipboard.writeText(text).then(() => flash('Copied!'))
}

const props = defineProps({
  spans: { type: Array, default: () => [] },
  turns: { type: Array, default: null },
  selectedSpan: { type: Object, default: null },
  traceId: { type: String, default: '' },
  contextWindowTokens: { type: Number, default: null },
})

const emit = defineEmits(['select-span', 'fetch-content', 'load-subtree', 'jump-live'])

const requestedContent = ref(new Set())
// Prompts are expanded by default — the conversation tab is meant to
// read like a continuous document (USER → ASSISTANT → tools → USER →
// ASSISTANT…), not a list of headers you have to click to open. The
// auto-expand watcher below seeds this set whenever the prompts list
// grows, so reloads + lazy subtree merges keep filling in.
const expandedPromptIds = ref(new Set())
const expandedPromptBodyIds = ref(new Set())
// Bash spans collapse their stdout/stderr by default — turns with many
// shell calls would otherwise become a wall of output.
const expandedBashIds = ref(new Set())
// Edit/Write/MultiEdit spans default to collapsed for the same reason
// as bash: a refactor turn can contain a dozen edits and we want a
// scannable spine.
const expandedDiffIds = ref(new Set())
// ToolSearch attributes — query / loaded_tools / total_deferred_tools —
// are useful detail but noisy in the spine. Default-collapsed; click
// expands a small attribute card the same way Bash output does.
const expandedToolSearchIds = ref(new Set())
const promptRefs = ref(new Map())
const spanRefs = ref(new Map())
// Standalone (non-prompt) root entries — rare but possible (legacy
// background-task notifications that didn't get grafted under a prompt).
// Tracked separately so the strip-bar -> selectedSpan watcher can still
// scroll to them.
const standaloneRefs = ref(new Map())
// TOC (left-rail) refs: the scrolling region itself and one entry per
// turn card. Used by the active-turn auto-follow watcher and by
// jumpToLive() to snap the rail to the bottom on demand.
const tocScrollEl = ref(null)
const turnTocRefs = new Map()

// The Turns TOC rail is `position: sticky`. useStickyMaxHeight keeps
// its max-height fitted to the viewport in both natural and stuck
// positions (a static `calc(100vh - header)` only fits while stuck).
const turnsAsideEl = ref(null)
const { maxH: turnsMaxH } = useStickyMaxHeight(turnsAsideEl)

// Resizable rail: width is dragged via the handle on the rail's right edge and
// persisted in localStorage. Clamped so it can't collapse or swallow the chat
// column. Default 224px = the previous fixed `w-56`.
const RAIL_MIN = 176
const RAIL_MAX = 560
const RAIL_KEY = 'regin_trace_rail_width'
function _clampRail(w) { return Math.min(RAIL_MAX, Math.max(RAIL_MIN, w)) }
const railWidth = ref((() => {
  const v = parseInt(localStorage.getItem(RAIL_KEY), 10)
  return Number.isFinite(v) ? _clampRail(v) : 224
})())
let _railStartX = 0
let _railStartW = 0
function onRailResizeMove(e) {
  railWidth.value = _clampRail(_railStartW + (e.clientX - _railStartX))
}
function onRailResizeEnd() {
  document.removeEventListener('mousemove', onRailResizeMove)
  document.removeEventListener('mouseup', onRailResizeEnd)
  document.body.style.userSelect = ''
  document.body.style.cursor = ''
  localStorage.setItem(RAIL_KEY, String(Math.round(railWidth.value)))
}
function onRailResizeStart(e) {
  _railStartX = e.clientX
  _railStartW = railWidth.value
  document.addEventListener('mousemove', onRailResizeMove)
  document.addEventListener('mouseup', onRailResizeEnd)
  document.body.style.userSelect = 'none'      // suppress text selection while dragging
  document.body.style.cursor = 'col-resize'
  e.preventDefault()
}
function onRailResizeKey(e) {
  const step = e.shiftKey ? 32 : 8
  if (e.key === 'ArrowLeft') railWidth.value = _clampRail(railWidth.value - step)
  else if (e.key === 'ArrowRight') railWidth.value = _clampRail(railWidth.value + step)
  else return
  e.preventDefault()
  localStorage.setItem(RAIL_KEY, String(Math.round(railWidth.value)))
}

// Span tree derivations live in useSpanTree (PR 2.3b): lookup maps,
// roots, recursive descendants, prompt groups, and the turnItems
// metadata the TOC and spine consume.
const {
  spanById, rootSpans, childrenOf, flattenDescendants,
  entries, promptGroups, turnItems, isWorkflow, phaseItems,
  hasPhaseSpans, phasePlan,
} = useSpanTree(() => props.spans, () => props.turns)

// ── Subagent launch merge ─────────────────────────────────────
// `tool.Agent` (the launch — carries description + prompt) and
// `subagent.start` (the run) are separate spans sharing no id. Fold the
// launch into the subagent row: pair by same parent prompt + matching
// subagent_type/agent_type + nearest start_time. Unpaired launches keep
// their normal marker rendering.
const AGENT_PROMPT_PREVIEW_CHARS = 140
const agentLaunchMerge = computed(() => {
  const byStart = new Map()   // subagent.start span_id -> tool.Agent span
  const merged = new Set()    // tool.Agent span_ids folded into a subagent row
  const spans = props.spans || []
  const launches = spans.filter(s => s.name === 'tool.Agent')
  const claimed = new Set()
  for (const start of spans.filter(s => s.name === 'subagent.start')) {
    const aType = start.attributes?.agent_type || ''
    let best = null
    let bestDt = Infinity
    for (const lc of launches) {
      if (claimed.has(lc.span_id)) continue
      if ((lc.parent_id || null) !== (start.parent_id || null)) continue
      if ((lc.attributes?.subagent_type || '') !== aType) continue
      const dt = Math.abs(new Date(lc.start_time) - new Date(start.start_time))
      if (dt < bestDt) { bestDt = dt; best = lc }
    }
    if (best) {
      byStart.set(start.span_id, best)
      merged.add(best.span_id)
      claimed.add(best.span_id)
    }
  }
  return { byStart, merged }
})
function launchForSubagent(startSpan) {
  return agentLaunchMerge.value.byStart.get(startSpan.span_id) || null
}
function renderableDescendants(entry) {
  const merged = agentLaunchMerge.value.merged
  const list = merged.size
    ? entry.descendants.filter(({ span }) => !merged.has(span.span_id))
    : entry.descendants
  // Flag each agent header that should get a separator line above it: between
  // consecutive agents, but NOT the first agent of a phase (the phase divider
  // already separates it) nor the very first item.
  return list.map((d, i) => ({
    ...d,
    agentSep: d.span.name === 'subagent.start' && i > 0
      && list[i - 1].span.name !== 'workflow.phase',
  }))
}
function agentPromptPreview(text) {
  if (!text) return ''
  return text.length > AGENT_PROMPT_PREVIEW_CHARS
    ? text.slice(0, AGENT_PROMPT_PREVIEW_CHARS).trimEnd() + '…'
    : text
}
function selectAgentLaunch(startSpan) {
  const lc = launchForSubagent(startSpan)
  if (lc) { onSelectSpan(lc); maybeFetchContent(lc) }
}
// Agent metadata with a fallback for workflow agents, which have NO
// `tool.Agent` launch span — the dispatched prompt / description / result
// live on the `subagent.start` span's own attributes instead.
function agentDescription(span) {
  return launchForSubagent(span)?.attributes?.description || span.attributes?.label || ''
}
function agentPrompt(span) {
  return launchForSubagent(span)?.attributes?.prompt || span.attributes?.prompt || ''
}
function agentPromptOwnerId(span) {
  return launchForSubagent(span)?.span_id || span.span_id
}
function selectAgentPrompt(span) {
  const lc = launchForSubagent(span)
  if (lc) { onSelectSpan(lc); maybeFetchContent(lc) }
  else { onSelectSpan(span); maybeFetchContent(span) }
}
// Number of agents under a workflow.phase, for the phase-divider label.
function agentCountForPhase(spanId) {
  return childrenOf(spanId).filter((s) => s.name === 'subagent.start').length
}

// Per-subagent prompt-card expand state.
const expandedAgentPrompts = ref(new Set())
function isAgentPromptExpanded(id) { return expandedAgentPrompts.value.has(id) }
function toggleAgentPrompt(id) {
  const next = new Set(expandedAgentPrompts.value)
  if (next.has(id)) next.delete(id); else next.add(id)
  expandedAgentPrompts.value = next
}
// Per-agent RESULT card expand state (workflow agents). Separate set from
// the prompt card so the two collapse independently on the same span.
const expandedAgentResults = ref(new Set())
function isAgentResultExpanded(id) { return expandedAgentResults.value.has(id) }
function toggleAgentResult(id) {
  const next = new Set(expandedAgentResults.value)
  if (next.has(id)) next.delete(id); else next.add(id)
  expandedAgentResults.value = next
}
const AGENT_RESULT_PREVIEW_CHARS = 280
function agentResultText(span) { return span.attributes?.result_full || span.attributes?.result_preview || '' }
function agentResultPreview(text) {
  if (!text) return ''
  return text.length > AGENT_RESULT_PREVIEW_CHARS
    ? text.slice(0, AGENT_RESULT_PREVIEW_CHARS).trimEnd() + '…'
    : text
}

// Task writes (TaskCreate / TaskUpdate) render as ordinary compact
// inline tool rows — `fullLabel` formats them as `TaskCreate #N:
// subject` / `TaskUpdate #N → status`, so each write reads as one
// line in the spine. The session-header badge in SessionTraceView
// handles the "current full list" view; rendering the cumulative
// snapshot at every write site (18 cards each repeating the prior + 1
// row) was strictly worse than the badge for orientation.

// Auto-expand only the latest prompt so the reader lands on the
// in-progress turn without a wall of historical content. When a newer
// prompt arrives, fold the previously auto-expanded one — old turns
// collapse to their preview chips so the spine stays scannable.
// `lastAutoExpandedId` tracks what we auto-opened so a manual collapse
// of the latest prompt sticks (we only re-expand when the *latest*
// span_id changes, not on every watcher fire from descendant merges).
const lastAutoExpandedId = ref(null)
watch(promptGroups, (groups) => {
  if (!groups.length) return
  const latest = groups[groups.length - 1].prompt
  if (lastAutoExpandedId.value === latest.span_id) return
  if (lastAutoExpandedId.value) {
    expandedPromptIds.value.delete(lastAutoExpandedId.value)
  }
  expandedPromptIds.value.add(latest.span_id)
  lastAutoExpandedId.value = latest.span_id
  emit('load-subtree', latest.span_id)
  fetchMissingContentForPrompt(latest.span_id)
}, { immediate: true })

// ── Active turn derivation ────────────────────────────────────
// Source of truth = the parent's `selectedSpan`. Map back to the turn
// whose [start, end] window contains the selection. Falls back to the
// last selected prompt id if no span matches.

const activeTurnIdx = computed(() => {
  if (!props.selectedSpan || !turnItems.value.length) return -1
  // Direct prompt match.
  const direct = turnItems.value.findIndex(t => t.promptSpanId === props.selectedSpan.span_id)
  if (direct >= 0) return direct
  const t = props.selectedSpan.start_time
    ? new Date(props.selectedSpan.start_time).getTime() : null
  if (t == null) return -1
  // Time-based window match: the descendant tools fall inside the
  // owning prompt's interval, but the prompt itself sits at the
  // start, so a closed-interval test works.
  for (let i = 0; i < turnItems.value.length; i++) {
    const item = turnItems.value[i]
    if (t >= item.startMs && t <= item.endMs) return i
  }
  return -1
})

async function selectTurn(item) {
  emit('select-span', item.prompt || spanById.value.get(item.promptSpanId))
  maybeFetchContent(spanById.value.get(item.promptSpanId))
  togglePromptExpanded(item.promptSpanId, true)
  await nextTick()
  const el = promptRefs.value.get(item.promptSpanId)
  if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

// Phase/agent TOC click (workflow runs): select the span and scroll its
// row (phase divider or agent card) into view via the shared spanRefs map.
async function selectWorkflowRow(spanId) {
  const span = spanById.value.get(spanId)
  if (span) { emit('select-span', span); maybeFetchContent(span) }
  await nextTick()
  const el = spanRefs.value.get(spanId)
  if (el && typeof el.scrollIntoView === 'function') {
    el.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }
}

// Cross-highlight from external selection (e.g. clicking a colored bar
// in the parent's mini-timeline strip): scroll the matching prompt — or
// the prompt that owns the selected descendant — into view.
// `block: 'nearest'` is a no-op when the element is already visible,
// so internal clicks on a tool span inside an already-open prompt
// don't yank the page.
watch([() => props.selectedSpan?.span_id, () => props.spans.length], async ([id]) => {
  if (!id) return
  const owner = promptGroups.value.find(g =>
    g.descendants.some(d => d.span.span_id === id)
  )
  if (owner && !isPromptExpanded(owner.prompt.span_id)) {
    togglePromptExpanded(owner.prompt.span_id, true)
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

// Keep the active turn visible inside the TOC's own scroll region. Uses
// `block: 'nearest'` so we only nudge when the highlighted card is
// actually off-screen — the user's manual scroll position is preserved
// while they're browsing.
watch(activeTurnIdx, async (idx) => {
  if (idx < 0) return
  await nextTick()
  const item = turnItems.value[idx]
  if (!item) return
  const el = turnTocRefs.get(item.promptSpanId)
  if (el && typeof el.scrollIntoView === 'function') {
    el.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }
})

// "Jump to live" — scroll to the most recent activity. Prefers the last
// prompt-anchored entry so the reader lands on the start of the latest
// turn; falls back to the page bottom when the last activity isn't
// owned by a prompt (e.g. a trailing standalone span). Also snaps the
// TOC rail to its bottom so the rail's last card lines up with the
// freshly-scrolled chat.
function jumpToLive() {
  if (tocScrollEl.value) {
    tocScrollEl.value.scrollTop = tocScrollEl.value.scrollHeight
  }
  emit('jump-live')
}

// ── Expand / collapse (prompt-level only now) ─────────────────

function isPromptExpanded(spanId) {
  return expandedPromptIds.value.has(spanId)
}

function isPromptBodyExpanded(spanId) {
  return expandedPromptBodyIds.value.has(spanId)
}

function togglePromptExpanded(spanId, forceOpen = false) {
  if (forceOpen) {
    if (!expandedPromptIds.value.has(spanId)) {
      expandedPromptIds.value.add(spanId)
      emit('load-subtree', spanId)
      fetchMissingContentForPrompt(spanId)
    }
    return
  }
  if (expandedPromptIds.value.has(spanId)) {
    expandedPromptIds.value.delete(spanId)
    expandedPromptBodyIds.value.delete(spanId)
  } else {
    expandedPromptIds.value.add(spanId)
    emit('load-subtree', spanId)
    fetchMissingContentForPrompt(spanId)
  }
}

function togglePromptBodyExpanded(spanId) {
  if (expandedPromptBodyIds.value.has(spanId)) {
    expandedPromptBodyIds.value.delete(spanId)
  } else {
    expandedPromptBodyIds.value.add(spanId)
  }
}

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

// ── Turn metadata helpers ─────────────────────────────────────

function turnCtxPct(turn) {
  if (!turn || !turn.context_used_tokens || !props.contextWindowTokens) return null
  const window = props.contextWindowTokens
  if (!window || window <= 0) return null
  return Math.max(0, Math.min(100, (turn.context_used_tokens / window) * 100))
}

// ── On-demand content fetching ────────────────────────────────

function needsContentFetch(span) {
  if (!span) return false
  const a = span.attributes || {}
  return (
    (span.name === 'prompt' && !a.text) ||
    (span.name === 'assistant_response' && !a.text) ||
    // Server-side tools (e.g. advisor) carry their textual response in
    // `response_text` rather than producing a tool_result; treat it the
    // same way as assistant_response for lazy fetch purposes.
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
  for (const { span } of entry.descendants) {
    maybeFetchContent(span)
  }
}

// ── Tool-chip categorization (center column) ──────────────────
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
      const tool = toolDisplayName(n.slice(5))
      add(tool, 'bg-blue-50 text-blue-700 border-blue-200')
    } else if (n === 'skill.read' || n === 'skill.invoke') {
      const skillId = span.attributes?.skill_id || 'skill'
      add(skillId, 'bg-green-50 text-green-700 border-green-200')
    } else if (n === 'file.edit' || n === 'plan.edit') {
      add('Edit', 'bg-orange-50 text-orange-700 border-orange-200')
    } else if (n === 'rule.check') {
      add('rule', 'bg-red-50 text-red-700 border-red-200')
    } else if (n.startsWith('subagent.')) {
      add('subagent', 'bg-pink-50 text-pink-700 border-pink-200')
    }
  }
  return Array.from(buckets.entries()).map(([label, v]) => ({ label, ...v }))
}

function bashExpanded(spanId) {
  return expandedBashIds.value.has(spanId)
}

function toggleBashExpanded(spanId) {
  if (expandedBashIds.value.has(spanId)) {
    expandedBashIds.value.delete(spanId)
  } else {
    expandedBashIds.value.add(spanId)
  }
}

function diffExpanded(spanId) {
  return expandedDiffIds.value.has(spanId)
}

function toggleDiffExpanded(spanId) {
  if (expandedDiffIds.value.has(spanId)) {
    expandedDiffIds.value.delete(spanId)
  } else {
    expandedDiffIds.value.add(spanId)
  }
}

function toolSearchExpanded(spanId) {
  return expandedToolSearchIds.value.has(spanId)
}

function toggleToolSearchExpanded(spanId) {
  if (expandedToolSearchIds.value.has(spanId)) {
    expandedToolSearchIds.value.delete(spanId)
  } else {
    expandedToolSearchIds.value.add(spanId)
  }
}

function onSelectSpan(span) {
  emit('select-span', span)
}

// Click handler for clickable rows whose label text the reader may want
// to copy or Ctrl-F search. Skip the row-select side effect when the
// click ended a drag-selection — otherwise the highlighted text would
// vanish the instant `selectedSpan` reactivity re-rendered the row.
function onRowClick(span) {
  if (typeof window !== 'undefined') {
    const sel = window.getSelection?.()
    if (sel && sel.toString().length > 0) return
  }
  onSelectSpan(span)
  maybeFetchContent(span)
}

</script>

<template>
  <div class="flex gap-4 items-start">
    <!-- ──────── LEFT RAIL: TURNS TOC ────────
         Flex column so the items area scrolls *inside* the aside while
         the header and "Jump to live" footer stay pinned. Without this
         split, a long list would push the only "jump to latest"
         affordance off-screen — the very button the user needs to
         reach when there are too many turns to scroll. -->
    <aside
      ref="turnsAsideEl"
      class="shrink-0 sticky self-start flex flex-col"
      :style="{
        width: railWidth + 'px',
        top: 'calc(var(--regin-trace-header-h, 5rem) + 0.5rem)',
        maxHeight: turnsMaxH || 'calc(100vh - var(--regin-trace-header-h, 5rem) - 2rem)',
      }"
    >
      <!-- Drag handle on the rail's right edge (sits in the gutter between the
           rail and the chat column). The aside is `sticky`, so this absolute
           handle is positioned relative to it. A <button> so it's keyboard
           focusable (arrow keys resize) and uses the resize cursor. -->
      <button
        type="button"
        class="absolute top-0 -right-1.5 w-3 h-full p-0 bg-transparent border-0 cursor-col-resize group z-10 select-none focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-400 rounded"
        title="Drag (or ←/→) to resize panel"
        aria-label="Resize panel"
        @mousedown="onRailResizeStart"
        @keydown="onRailResizeKey"
      >
        <span class="block mx-auto w-px h-full bg-slate-200 group-hover:bg-blue-400 transition-colors"></span>
      </button>
      <div class="flex items-baseline justify-between mb-2 shrink-0">
        <h3 class="text-[11px] uppercase tracking-wider text-slate-400 font-semibold">{{ isWorkflow ? 'Phases' : 'Turns' }}</h3>
        <span class="text-[11px] text-slate-400 tabular-nums">{{ isWorkflow ? (hasPhaseSpans ? phaseItems.length : (phasePlan.length || phaseItems.length)) : turnItems.length }}</span>
      </div>
      <!-- Workflow phase TOC: each phase is a titled section (number badge,
           title, detail subtitle, "N agents · tokens" meta) with its agents
           listed beneath a connector rail. Each agent row carries its own
           model, output tokens, and tool-call count so the reader can see
           the shape of the fan-out without opening the spine. -->
      <div
        v-if="isWorkflow"
        ref="tocScrollEl"
        class="flex-1 min-h-0 overflow-y-auto pr-1 space-y-3 [scrollbar-gutter:stable] [scrollbar-width:thin] [overscroll-behavior:contain]"
      >
        <!-- Declared phase plan (live runs only): the manifest with real
             per-agent phaseIndex isn't written until completion, so while the
             run is in progress we surface the script's planned phases here and
             list the running agents under a "Running" band below. -->
        <div
          v-if="!hasPhaseSpans && phasePlan.length"
          class="rounded-md border border-dashed border-slate-200 bg-slate-50/60 px-2 py-1.5"
        >
          <div class="text-[10px] uppercase tracking-wider text-slate-400 font-semibold mb-1">Planned phases</div>
          <div class="space-y-1">
            <div v-for="(ph, i) in phasePlan" :key="i" class="flex items-start gap-1.5">
              <span class="mt-px inline-flex items-center justify-center shrink-0 w-4 h-4 rounded bg-slate-200 text-slate-500 text-[10px] font-bold tabular-nums">{{ i + 1 }}</span>
              <div class="min-w-0 flex-1">
                <div class="text-[11px] font-medium text-slate-500 leading-tight truncate" :title="ph.detail || ph.title">{{ ph.title }}</div>
              </div>
            </div>
          </div>
          <div class="text-[10px] text-slate-400 italic mt-1">agents grouped by phase on completion</div>
        </div>
        <div v-for="p in phaseItems" :key="p.phaseSpanId">
          <!-- Running band header (live: agents not yet phase-mapped) -->
          <div v-if="p.running" class="px-1.5 py-1">
            <div class="flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-slate-400 font-semibold">
              <span class="inline-block w-1.5 h-1.5 rounded-full bg-blue-400 animate-pulse shrink-0"></span>
              <span>Running</span>
              <span class="ml-auto normal-case tracking-normal tabular-nums" title="finished / total agents">{{ p.doneCount }}/{{ p.agentCount }} agents</span>
            </div>
          </div>
          <!-- Phase header (completed runs: real phases) -->
          <div
            v-else
            class="cursor-pointer rounded-md px-1.5 py-1 transition-colors hover:bg-emerald-50/70"
            :class="selectedSpan && selectedSpan.span_id === p.phaseSpanId ? 'bg-emerald-50 ring-1 ring-emerald-200' : ''"
            @click="selectWorkflowRow(p.phaseSpanId)"
          >
            <div class="flex items-start gap-1.5">
              <!-- ✓ once every agent in the phase is done, else the phase
                   number; emerald=complete, blue=in-flight, slate=not started. -->
              <span
                class="mt-px inline-flex items-center justify-center shrink-0 w-4 h-4 rounded text-[10px] font-bold tabular-nums"
                :class="p.complete ? 'bg-emerald-100 text-emerald-700'
                  : p.agentCount ? 'bg-blue-100 text-blue-700' : 'bg-slate-100 text-slate-400'"
              >{{ p.complete ? '✓' : p.index }}</span>
              <div class="min-w-0 flex-1">
                <div class="text-xs font-semibold text-slate-800 leading-tight flex items-center gap-1.5">
                  <span class="truncate" :title="p.title">{{ p.title }}</span>
                  <span
                    v-if="p.inProgress"
                    class="inline-block w-1.5 h-1.5 rounded-full bg-blue-400 animate-pulse shrink-0"
                    title="phase in progress"
                  ></span>
                </div>
                <div v-if="p.detail" class="text-[10px] text-slate-400 leading-snug truncate" :title="p.detail">{{ p.detail }}</div>
                <div class="mt-0.5 flex items-center gap-1 text-[10px] text-slate-400 leading-tight">
                  <span v-if="p.agentCount" class="font-medium tabular-nums" title="finished / total agents">{{ p.doneCount }}/{{ p.agentCount }} agents</span>
                  <span v-else class="font-medium text-slate-300">pending</span>
                  <template v-if="p.tokens">
                    <span class="text-slate-300">·</span>
                    <span class="tabular-nums" title="total output tokens across this phase's agents">{{ fmtTokens(p.tokens) }}</span>
                  </template>
                </div>
              </div>
            </div>
          </div>
          <!-- Agents under this phase (connector rail on the left). Hidden for
               a declared-but-unstarted phase so it reads as a clean empty band
               rather than a dangling connector. -->
          <div v-if="p.agents.length" class="mt-1 ml-2.5 pl-2.5 border-l border-slate-200 space-y-0.5">
            <div
              v-for="a in p.agents"
              :key="a.spanId"
              class="cursor-pointer rounded px-1.5 py-1 transition-colors hover:bg-slate-50"
              :class="selectedSpan && selectedSpan.span_id === a.spanId ? 'bg-blue-50 ring-1 ring-blue-200' : ''"
              @click="selectWorkflowRow(a.spanId)"
            >
              <!-- Three states, like the Claude terminal: done = green ✓ +
                   normal text; actively running = bold BLUE filled dot + blue
                   text so it's easy to spot; queued/stopped = muted gray hollow
                   ring + gray text (it recedes). -->
              <div
                class="flex items-center gap-1.5 text-[11px] leading-tight"
                :class="a.done ? 'text-slate-600' : a.running ? 'text-blue-700 font-semibold' : 'text-slate-400'"
              >
                <span
                  v-if="a.done"
                  class="shrink-0 w-2 text-center text-emerald-500 text-[10px] leading-none"
                  title="done"
                >✓</span>
                <span
                  v-else-if="a.running"
                  class="inline-block w-2 h-2 rounded-full shrink-0 bg-blue-500 ring-2 ring-blue-200 animate-pulse"
                  title="running"
                ></span>
                <span
                  v-else
                  class="inline-block w-2 h-2 rounded-full border border-slate-300 shrink-0"
                  :title="a.state || 'queued'"
                ></span>
                <span class="truncate">{{ a.label }}</span>
              </div>
              <div
                v-if="a.model || a.tokens || a.toolCalls"
                class="ml-3 mt-0.5 flex flex-wrap items-center gap-x-1 gap-y-0.5 text-[10px] text-slate-400 leading-tight"
              >
                <span
                  v-if="a.model"
                  class="inline-flex items-center rounded bg-slate-100 text-slate-500 px-1 font-medium"
                  :title="a.model"
                >{{ fmtModel(a.model) }}</span>
                <span v-if="a.tokens" class="tabular-nums" title="output tokens">{{ fmtTokens(a.tokens) }}</span>
                <template v-if="a.toolCalls">
                  <span class="text-slate-300">·</span>
                  <span class="tabular-nums">{{ a.toolCalls }} tool<span v-if="a.toolCalls !== 1">s</span></span>
                </template>
              </div>
            </div>
          </div>
        </div>
      </div>
      <div v-else-if="!turnItems.length" class="text-xs text-slate-400">
        No prompts found.
      </div>
      <div
        v-else
        ref="tocScrollEl"
        class="flex-1 min-h-0 overflow-y-auto pr-1 space-y-1 [scrollbar-gutter:stable] [scrollbar-width:thin] [overscroll-behavior:contain]"
      >
        <div
          v-for="item in turnItems"
          :key="item.promptSpanId"
          :ref="(el) => { if (el) turnTocRefs.set(item.promptSpanId, el) }"
          tabindex="0"
          class="cursor-pointer rounded-md px-2 py-1.5 border transition-colors hover:bg-slate-50 focus-visible:outline-2 focus-visible:outline-blue-500"
          :class="activeTurnIdx === item.idx
            ? 'bg-blue-50 border-blue-300'
            : 'bg-white border-transparent hover:border-slate-200'"
          @click="selectTurn(item)"
        >
          <div class="flex items-baseline gap-1.5 text-xs leading-tight">
            <span class="text-slate-400 font-mono shrink-0 tabular-nums">#{{ item.idx + 1 }}</span>
            <span
              class="truncate"
              :class="activeTurnIdx === item.idx ? 'font-medium text-slate-900' : 'text-slate-700'"
            >{{ truncate(item.promptText, 32) }}</span>
          </div>
          <div class="text-[10px] text-slate-400 font-mono mt-0.5 flex items-center gap-1.5">
            <span>{{ fmtTime(item.timestamp) }}</span>
            <span v-if="item.durationMs" class="text-slate-300">·</span>
            <span v-if="item.durationMs">{{ fmtDuration(item.durationMs) }}</span>
            <span v-if="item.turn?.input_tokens" class="text-slate-300">·</span>
            <span v-if="item.turn?.input_tokens">↑{{ fmtTokens((item.turn.input_tokens || 0) + (item.turn.cache_creation_tokens || 0)) }}</span>
          </div>
        </div>
      </div>
      <button
        type="button"
        class="shrink-0 block mt-2 pt-2 border-t border-slate-100 text-[11px] text-blue-600 hover:text-blue-700 hover:underline cursor-pointer focus-visible:outline-2 focus-visible:outline-blue-500 rounded"
        title="Scroll to the most recent turn"
        @click="jumpToLive"
      >↓ Jump to live</button>
    </aside>

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
            class="group rounded-md border bg-purple-50 border-purple-200 px-3 py-2 cursor-pointer hover:border-purple-300 transition-colors"
            :class="selectedSpan && selectedSpan.span_id === entry.prompt.span_id ? 'ring-2 ring-purple-300' : ''"
            @click="onPromptClick(entry.prompt)"
          >
            <div class="flex items-center gap-2 text-[11px] font-mono text-purple-700/80 mb-0.5">
              <span class="font-semibold uppercase tracking-wider text-[10px]">USER</span>
              <span class="text-purple-300">·</span>
              <span>{{ fmtClock(entry.prompt.start_time) }}</span>
              <button
                v-if="entry.prompt.attributes?.text && (promptPreviewMeta(entry.prompt).truncated || isPromptBodyExpanded(entry.prompt.span_id))"
                type="button"
                class="ml-auto opacity-0 group-hover:opacity-100 transition-opacity px-1.5 py-0.5 rounded text-[10px] text-purple-600 hover:bg-purple-200/60 focus-visible:outline-2 focus-visible:outline-purple-400"
                :title="isPromptBodyExpanded(entry.prompt.span_id) ? 'Collapse full prompt' : 'Show full prompt'"
                @click.stop="togglePromptBodyExpanded(entry.prompt.span_id)"
              >{{ isPromptBodyExpanded(entry.prompt.span_id) ? 'Preview' : 'Full prompt' }}</button>
              <button
                v-if="entry.prompt.attributes?.text"
                type="button"
                :class="[
                  'opacity-0 group-hover:opacity-100 transition-opacity px-1.5 py-0.5 rounded text-[10px] text-purple-600 hover:bg-purple-200/60 focus-visible:outline-2 focus-visible:outline-purple-400',
                  !(promptPreviewMeta(entry.prompt).truncated || isPromptBodyExpanded(entry.prompt.span_id)) && 'ml-auto',
                ]"
                title="Copy"
                @click.stop="copyText(entry.prompt.attributes.text)"
              >Copy</button>
            </div>
            <div
              v-if="entry.prompt.attributes?.text && isPromptBodyExpanded(entry.prompt.span_id)"
              class="max-h-[60vh] overflow-y-auto rounded border border-purple-200/70 bg-white/40 px-2 py-1"
            >
              <PromptBody
                :text="entry.prompt.attributes.text"
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
              <span
                v-if="promptPreviewMeta(entry.prompt).truncated"
                class="inline-flex items-center rounded border border-purple-200 bg-white/70 px-1.5 py-0.5"
              >collapsed preview</span>
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

          <!-- Tool/skill chips for this turn (preview) -->
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

          <!-- ASSISTANT response card (slate tint) — always render when expanded -->
          <template v-if="isPromptExpanded(entry.prompt.span_id)">
            <template v-for="{ span, inAgent, agentSep } in renderableDescendants(entry)" :key="span.span_id">
              <div
                :ref="(el) => { if (el) spanRefs.set(span.span_id, el); else spanRefs.delete(span.span_id) }"
                :class="[
                  inAgent ? 'border-l-2 border-pink-300 ml-1.5 pl-2.5' : '',
                  agentSep ? 'border-t-2 border-slate-200 mt-6 pt-5' : '',
                ]"
              >
              <!-- Background-task notification card (amber tint) -->
              <div
                v-if="span.name === 'task.notification'"
                class="rounded-md border bg-amber-50 border-amber-200 px-3 py-2 cursor-pointer hover:border-amber-300 transition-colors"
                :class="selectedSpan && selectedSpan.span_id === span.span_id ? 'ring-2 ring-amber-300' : ''"
                @click="onSelectSpan(span); maybeFetchContent(span)"
              >
                <div class="flex items-center gap-2 text-[11px] font-mono text-amber-700/80 mb-0.5">
                  <span class="font-semibold uppercase tracking-wider text-[10px]">BACKGROUND TASK</span>
                  <span class="text-amber-300">·</span>
                  <span>{{ fmtClock(span.start_time) }}</span>
                  <span
                    v-if="span.attributes?.status"
                    class="px-1 rounded text-[10px] border"
                    :class="span.attributes.status === 'failed'
                      ? 'bg-red-50 text-red-700 border-red-200'
                      : 'bg-green-50 text-green-700 border-green-200'"
                  >{{ span.attributes.status }}</span>
                </div>
                <div
                  v-if="span.attributes?.summary"
                  class="text-[13.5px] break-words text-amber-900 leading-relaxed"
                >{{ span.attributes.summary }}</div>
                <div
                  v-if="span.attributes?.task_id"
                  class="text-[10px] font-mono text-amber-600/70 mt-0.5"
                >task {{ span.attributes.task_id }}</div>
              </div>

              <!-- Extended-thinking card (thinking-only turns) -->
              <div
                v-else-if="span.name === 'assistant.thinking'"
                class="group rounded-md border border-violet-200 bg-violet-50/40 px-3 py-2 cursor-pointer hover:border-violet-300 transition-colors"
                :class="selectedSpan && selectedSpan.span_id === span.span_id ? 'ring-2 ring-violet-300' : ''"
                @click="onSelectSpan(span); maybeFetchContent(span)"
              >
                <div class="flex items-center gap-2 text-[11px] font-mono text-violet-500 mb-1">
                  <span class="font-semibold uppercase tracking-wider text-[10px]">THINKING</span>
                  <span class="text-violet-300">·</span>
                  <span>{{ fmtClock(span.start_time) }}</span>
                </div>
                <div
                  v-if="span.attributes?.thinking_text"
                  class="text-[12.5px] text-violet-900/80 italic whitespace-pre-wrap break-words leading-relaxed max-h-72 overflow-y-auto"
                >{{ span.attributes.thinking_text }}</div>
                <div v-else class="text-[12px] text-violet-500 italic">reasoned (text not captured)</div>
              </div>

              <div
                v-else-if="span.name === 'assistant_response'"
                class="group rounded-md border bg-slate-50 border-slate-200 px-3 py-2 cursor-pointer hover:border-slate-300 transition-colors"
                :class="selectedSpan && selectedSpan.span_id === span.span_id ? 'ring-2 ring-slate-300' : ''"
                @click="onSelectSpan(span); maybeFetchContent(span)"
              >
                <div class="flex items-center gap-2 text-[11px] font-mono text-slate-500 mb-1">
                  <span class="font-semibold uppercase tracking-wider text-[10px]">ASSISTANT</span>
                  <span class="text-slate-300">·</span>
                  <span>{{ fmtClock(span.start_time) }}</span>
                  <span
                    v-if="span.duration_ms"
                    class="text-slate-400"
                    :title="span.attributes?.turn_total_duration_ms
                      ? `inference ${fmtDuration(span.duration_ms)}, whole turn ${fmtDuration(span.attributes.turn_total_duration_ms)}`
                      : `inference ${fmtDuration(span.duration_ms)}`"
                  >· {{ fmtDuration(span.duration_ms) }}</span>
                  <span v-if="span.attributes?.truncated" class="text-amber-700 bg-amber-50 border border-amber-200 px-1 rounded text-[10px]">truncated</span>
                  <button
                    v-if="span.attributes?.text"
                    type="button"
                    class="ml-auto opacity-0 group-hover:opacity-100 transition-opacity px-1.5 py-0.5 rounded text-[10px] text-slate-500 hover:bg-slate-200/60 focus-visible:outline-2 focus-visible:outline-slate-400"
                    title="Copy"
                    @click.stop="copyText(span.attributes.text)"
                  >Copy</button>
                </div>
                <div class="text-[13.5px] text-slate-800">
                  <MarkdownContent v-if="span.attributes?.text" :markdown="span.attributes.text" />
                  <span v-else class="text-slate-500">{{ fullLabel(span) }}</span>
                </div>
              </div>

              <!-- AskUserQuestion: full Q&A cards inline. Both approved
                   and denied calls land here — denied ones are synthesized
                   by turn_trace from the transcript when is_error=true.
                   `attributes.denied` flips the styling: amber border,
                   no chosen option, and the user's actual response text
                   (`denial_reason`) renders below the options. -->
              <div
                v-else-if="span.name === 'tool.AskUserQuestion' && span.attributes?.questions"
                tabindex="0"
                class="ml-3 -mx-1 my-1 cursor-pointer rounded focus-visible:outline-2"
                :class="[
                  span.attributes.denied ? 'focus-visible:outline-amber-500' : 'focus-visible:outline-blue-500',
                  selectedSpan && selectedSpan.span_id === span.span_id
                    ? (span.attributes.denied ? 'ring-2 ring-amber-200' : 'ring-2 ring-blue-200')
                    : '',
                ]"
                @click="onSelectSpan(span); maybeFetchContent(span)"
              >
                <div class="flex items-center gap-2 text-[11px] font-mono text-slate-400 mb-1 px-1">
                  <span class="inline-block w-1.5 h-1.5 rounded-full shrink-0"
                        :class="span.attributes.denied ? 'bg-amber-400' : dotColor(span.name)"></span>
                  <span>{{ fmtClock(span.start_time) }}</span>
                  <span class="font-sans uppercase tracking-wider text-[10px] text-slate-500 font-semibold">Ask user</span>
                  <span
                    v-if="span.attributes.denied"
                    class="font-sans uppercase tracking-wider text-[10px] bg-amber-100 border border-amber-200 text-amber-800 px-1 rounded"
                  >{{ span.attributes.deny_kind === 'chat' ? 'chat instead' : 'denied' }}</span>
                  <span v-if="span.duration_ms" class="ml-auto">{{ fmtDuration(span.duration_ms) }}</span>
                </div>
                <div class="space-y-2">
                  <div
                    v-for="(q, qi) in span.attributes.questions"
                    :key="qi"
                    class="border rounded-md overflow-hidden bg-white"
                    :class="span.attributes.denied ? 'border-amber-200 opacity-90' : 'border-slate-200'"
                  >
                    <div
                      class="px-3 py-1.5 border-b"
                      :class="span.attributes.denied ? 'bg-amber-50 border-amber-200' : 'bg-slate-50 border-slate-200'"
                    >
                      <div
                        v-if="q.header"
                        class="text-[10px] font-semibold uppercase tracking-wider mb-0.5"
                        :class="span.attributes.denied ? 'text-amber-700' : 'text-slate-500'"
                      >{{ q.header }}{{ q.multiSelect ? ' · multi-select' : '' }}</div>
                      <div class="text-[13px] font-medium text-slate-800">{{ q.question }}</div>
                    </div>
                    <ul class="divide-y divide-slate-100">
                      <li
                        v-for="(opt, oi) in (q.options || [])"
                        :key="oi"
                        class="flex items-start gap-2 px-3 py-1.5 text-[12.5px]"
                        :class="askIsChosen(span, q, opt) ? 'bg-green-50' : ''"
                      >
                        <span
                          class="shrink-0 mt-0.5 w-4 text-center font-mono text-xs"
                          :class="askIsChosen(span, q, opt) ? 'text-green-600' : 'text-slate-300'"
                        >{{ askIsChosen(span, q, opt) ? '✓' : '○' }}</span>
                        <span class="min-w-0 flex-1">
                          <span
                            class="block"
                            :class="askIsChosen(span, q, opt) ? 'text-slate-900 font-medium' : 'text-slate-800 font-medium'"
                          >{{ askOptLabel(opt) }}</span>
                          <span
                            v-if="askOptDescription(opt)"
                            class="block text-slate-500 mt-0.5"
                          >{{ askOptDescription(opt) }}</span>
                          <details
                            v-if="opt && opt.preview"
                            class="mt-1"
                            @click.stop
                          >
                            <summary class="cursor-pointer text-[10px] text-slate-500 hover:text-slate-700 select-none">Preview</summary>
                            <pre class="mt-1 text-[11px] text-slate-700 bg-slate-50 border border-slate-200 rounded p-2 whitespace-pre-wrap break-words max-h-64 overflow-y-auto font-mono">{{ opt.preview }}</pre>
                          </details>
                        </span>
                      </li>
                      <li
                        v-if="askFreeText(span, q)"
                        class="flex items-start gap-2 px-3 py-1 text-[12.5px] bg-amber-50"
                      >
                        <span class="shrink-0 mt-0.5 w-4 text-center font-mono text-xs text-amber-600">✎</span>
                        <span class="text-slate-900">{{ askFreeText(span, q) }}</span>
                      </li>
                    </ul>
                    <div
                      v-if="askNote(span, q)"
                      class="px-3 py-1 bg-slate-50 border-t border-slate-100 text-[11px] text-slate-600 italic"
                    >
                      Note: {{ askNote(span, q) }}
                    </div>
                  </div>
                  <div
                    v-if="span.attributes.denied && span.attributes.denial_reason"
                    class="border border-amber-200 bg-amber-50 rounded-md px-3 py-2 text-[12px] text-slate-700 whitespace-pre-wrap"
                  >
                    <div
                      class="text-[10px] font-semibold uppercase tracking-wider text-amber-700 mb-1"
                      title="Templated text the agent harness (Claude Code) injects when the user denies a tool call — not user prose."
                    >Denied (agent injected prompt)</div>
                    {{ span.attributes.denial_reason }}
                  </div>
                </div>
              </div>

              <!-- Tool-failure card: surface tool_name + the input that
                   failed (Bash command or file_path) + full error text
                   inline (red tint). The error is capped at 16 KB by
                   post_tool_failure.py — much larger than any realistic
                   traceback — so it fits without blowing up the row. -->
              <div
                v-else-if="span.name === 'tool.failure'"
                class="group rounded-md border bg-red-50 border-red-200 px-3 py-2 cursor-pointer hover:border-red-300 transition-colors"
                :class="selectedSpan && selectedSpan.span_id === span.span_id ? 'ring-2 ring-red-300' : ''"
                @click="onSelectSpan(span); maybeFetchContent(span)"
              >
                <div class="flex items-center gap-2 text-[11px] font-mono text-red-700 mb-1">
                  <span class="font-semibold uppercase tracking-wider text-[10px]">TOOL FAILURE</span>
                  <span class="text-red-300">·</span>
                  <span>{{ fmtClock(span.start_time) }}</span>
                  <span class="font-sans text-red-700 font-medium">{{ toolDisplayName(span.attributes?.tool_name || 'tool') }}</span>
                  <span
                    v-if="span.attributes?.is_interrupt"
                    class="text-[10px] bg-amber-100 border border-amber-200 text-amber-800 px-1 rounded font-sans"
                  >user interrupt</span>
                  <button
                    v-if="span.attributes?.error"
                    type="button"
                    class="ml-auto opacity-0 group-hover:opacity-100 transition-opacity px-1.5 py-0.5 rounded text-[10px] text-red-600 hover:bg-red-200/60 focus-visible:outline-2 focus-visible:outline-red-400"
                    title="Copy"
                    @click.stop="copyText(span.attributes.error)"
                  >Copy</button>
                </div>
                <!-- Bash failure: show the command with a $ prompt prefix so
                     the reader can see exactly what was executed. Falls back
                     to command_preview if the full text wasn't captured. -->
                <div
                  v-if="span.attributes?.tool_name === 'Bash' && (span.attributes?.command || span.attributes?.command_preview)"
                  class="flex items-start gap-2 mb-1.5 text-[12.5px] font-mono"
                >
                  <span class="text-emerald-700 font-semibold shrink-0 select-none">$</span>
                  <pre class="text-slate-800 whitespace-pre-wrap break-words leading-snug flex-1 min-w-0">{{ span.attributes.command || span.attributes.command_preview }}</pre>
                </div>
                <!-- Non-Bash tools (Edit/Write/Read/etc.) surface file_path
                     so the reader knows which file the call targeted. -->
                <div
                  v-else-if="span.attributes?.file_path"
                  class="text-[12.5px] font-mono text-slate-700 mb-1.5 break-all"
                >{{ span.attributes.file_path }}</div>
                <pre
                  v-if="span.attributes?.error"
                  class="text-[12.5px] text-red-900 whitespace-pre-wrap break-words font-mono leading-snug"
                >{{ span.attributes.error }}</pre>
                <div
                  v-else
                  class="text-[12.5px] text-red-700/70 italic"
                >no error message recorded</div>
              </div>

              <!-- Server-side tool result card (e.g. advisor) — renders
                   the full response_text as markdown, since the call's
                   value is the textual reply, not a side effect. -->
              <div
                v-else-if="span.attributes?.server_side && span.attributes?.response_text"
                class="group rounded-md border bg-violet-50 border-violet-200 px-3 py-2 cursor-pointer hover:border-violet-300 transition-colors"
                :class="selectedSpan && selectedSpan.span_id === span.span_id ? 'ring-2 ring-violet-300' : ''"
                @click="onSelectSpan(span); maybeFetchContent(span)"
              >
                <div class="flex items-center gap-2 text-[11px] font-mono text-violet-600 mb-1">
                  <span class="font-semibold uppercase tracking-wider text-[10px]">{{ (span.attributes?.tool_name || 'tool').toUpperCase() }}</span>
                  <span class="text-violet-300">·</span>
                  <span>{{ fmtClock(span.start_time) }}</span>
                  <span
                    v-if="span.attributes?.advisor_model"
                    class="text-[10px] text-violet-500 font-sans"
                  >{{ span.attributes.advisor_model }}</span>
                  <span v-if="span.attributes?.response_truncated" class="text-amber-700 bg-amber-50 border border-amber-200 px-1 rounded text-[10px]">truncated</span>
                  <button
                    type="button"
                    class="ml-auto opacity-0 group-hover:opacity-100 transition-opacity px-1.5 py-0.5 rounded text-[10px] text-violet-600 hover:bg-violet-200/60 focus-visible:outline-2 focus-visible:outline-violet-400"
                    title="Copy"
                    @click.stop="copyText(span.attributes.response_text)"
                  >Copy</button>
                </div>
                <div class="text-[13.5px] text-slate-800">
                  <MarkdownContent :markdown="span.attributes.response_text" />
                </div>
              </div>

              <!-- Bash row: flat one-liner like other inline tool rows
                   when collapsed (avoids visual collision with the
                   assistant_response card, which also uses slate chrome),
                   with a `$` shell-prompt prefix so it reads as a shell
                   command at a glance. Output expands into a dark
                   terminal-themed panel — universally recognised, and
                   impossible to confuse with the assistant card. -->
              <div
                v-else-if="span.name === 'tool.Bash' && (span.attributes?.stdout || span.attributes?.stderr || span.attributes?.interrupted || span.attributes?.command)"
                class="group"
              >
                <div
                  tabindex="0"
                  class="flex items-center gap-2 text-xs pl-3 cursor-pointer rounded px-2 py-1 -mx-2 hover:bg-slate-50 focus-visible:outline-2 focus-visible:outline-blue-500"
                  :class="selectedSpan && selectedSpan.span_id === span.span_id ? 'bg-blue-50' : ''"
                  @click="onSelectSpan(span); toggleBashExpanded(span.span_id); maybeFetchContent(span)"
                >
                  <span class="inline-block w-1.5 h-1.5 rounded-full shrink-0" :class="dotColor(span.name)"></span>
                  <span class="font-mono text-[11px] text-slate-400 shrink-0">{{ fmtClock(span.start_time) }}</span>
                  <span class="text-slate-400 shrink-0 select-none w-3 text-center">{{ bashExpanded(span.span_id) ? '▾' : '▸' }}</span>
                  <span class="font-mono text-[11px] text-emerald-600 font-semibold shrink-0 select-none">$</span>
                  <span class="break-all flex-1 min-w-0 whitespace-pre-line font-mono text-slate-700">{{ span.attributes?.command_preview || fullLabel(span) }}</span>
                  <span
                    v-if="span.attributes?.interrupted"
                    class="text-[10px] bg-amber-100 border border-amber-200 text-amber-800 px-1 rounded shrink-0"
                  >interrupted</span>
                  <span v-if="span.duration_ms" class="font-mono text-[11px] text-slate-400 shrink-0">{{ fmtDuration(span.duration_ms) }}</span>
                </div>
                <div
                  v-if="bashExpanded(span.span_id)"
                  class="ml-6 mt-1 rounded-md bg-slate-900 border border-slate-800 overflow-hidden"
                >
                  <div v-if="span.attributes?.command" class="px-3 py-2">
                    <div class="flex items-center gap-2 mb-1">
                      <span class="text-[10px] font-semibold uppercase tracking-wider text-emerald-400">command</span>
                      <span
                        v-if="span.attributes.command_truncated_bytes"
                        class="text-[10px] text-amber-300 bg-amber-900/40 border border-amber-700/60 px-1 rounded"
                      >truncated {{ fmtBytes(span.attributes.command_truncated_bytes) }}</span>
                      <button
                        type="button"
                        class="ml-auto opacity-0 group-hover:opacity-100 transition-opacity px-1.5 py-0.5 rounded text-[10px] text-slate-400 hover:bg-slate-700/60 focus-visible:outline-2 focus-visible:outline-slate-500"
                        title="Copy"
                        @click.stop="copyText(span.attributes.command)"
                      >Copy</button>
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
                      <button
                        type="button"
                        class="ml-auto opacity-0 group-hover:opacity-100 transition-opacity px-1.5 py-0.5 rounded text-[10px] text-slate-400 hover:bg-slate-700/60 focus-visible:outline-2 focus-visible:outline-slate-500"
                        title="Copy"
                        @click.stop="copyText(span.attributes.stdout)"
                      >Copy</button>
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
                      <button
                        type="button"
                        class="ml-auto opacity-0 group-hover:opacity-100 transition-opacity px-1.5 py-0.5 rounded text-[10px] text-red-400 hover:bg-red-900/40 focus-visible:outline-2 focus-visible:outline-red-500"
                        title="Copy"
                        @click.stop="copyText(span.attributes.stderr)"
                      >Copy</button>
                    </div>
                    <pre class="text-[12px] text-red-300 whitespace-pre-wrap break-words font-mono leading-snug max-h-64 overflow-auto">{{ span.attributes.stderr }}</pre>
                  </div>
                </div>
              </div>

              <!-- Edit / Write / MultiEdit diff card. Mirrors the
                   Claude TUI's `Update(path) +N -M` view: a flat header
                   row that expands into a dark terminal-style unified
                   diff with green additions, red deletions, dim hunk
                   markers, and slate context lines. -->
              <div
                v-else-if="
                  (span.name === 'tool.Edit' || span.name === 'tool.Write' || span.name === 'tool.MultiEdit')
                  && span.attributes?.diff
                "
                class="group"
              >
                <div
                  tabindex="0"
                  class="flex items-center gap-2 text-xs pl-3 cursor-pointer rounded px-2 py-1 -mx-2 hover:bg-slate-50 focus-visible:outline-2 focus-visible:outline-blue-500"
                  :class="selectedSpan && selectedSpan.span_id === span.span_id ? 'bg-blue-50' : ''"
                  @click="onSelectSpan(span); toggleDiffExpanded(span.span_id); maybeFetchContent(span)"
                >
                  <span class="inline-block w-1.5 h-1.5 rounded-full shrink-0" :class="dotColor(span.name)"></span>
                  <span class="font-mono text-[11px] text-slate-400 shrink-0">{{ fmtClock(span.start_time) }}</span>
                  <span class="text-slate-400 shrink-0 select-none w-3 text-center">{{ diffExpanded(span.span_id) ? '▾' : '▸' }}</span>
                  <span class="font-mono text-slate-700 shrink-0">
                    <span class="font-semibold">{{ diffOpLabel(span.attributes?.edit_op) }}</span><span class="text-slate-500">({{ diffFileName(span) }})</span>
                  </span>
                  <span class="flex-1 min-w-0 flex items-center gap-2">
                    <span
                      v-if="span.attributes?.added_lines"
                      class="font-mono text-[11px] text-emerald-600"
                    >+{{ span.attributes.added_lines }}</span>
                    <span
                      v-if="span.attributes?.removed_lines"
                      class="font-mono text-[11px] text-red-600"
                    >-{{ span.attributes.removed_lines }}</span>
                  </span>
                  <span v-if="span.duration_ms" class="font-mono text-[11px] text-slate-400 shrink-0">{{ fmtDuration(span.duration_ms) }}</span>
                </div>
                <div
                  v-if="diffExpanded(span.span_id)"
                  class="ml-6 mt-1 rounded-md bg-slate-900 border border-slate-800 overflow-hidden"
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
                    <button
                      type="button"
                      class="ml-auto opacity-0 group-hover:opacity-100 transition-opacity px-1.5 py-0.5 rounded text-[10px] text-slate-400 hover:bg-slate-700/60 focus-visible:outline-2 focus-visible:outline-slate-500"
                      title="Copy"
                      @click.stop="copyText(span.attributes.diff)"
                    >Copy</button>
                  </div>
                  <DiffBlock :diff="span.attributes.diff" :file-path="span.attributes?.file_path || ''" />
                </div>
              </div>

              <!-- ToolSearch: collapsed row matches the generic inline
                   look so the spine stays scannable; expanded panel
                   surfaces query, loaded_tools, max_results and the
                   search-universe size for the rare cases the reader
                   needs to audit what was loaded. -->
              <div
                v-else-if="span.name === 'tool.ToolSearch'"
                class="group"
              >
                <div
                  tabindex="0"
                  class="flex items-center gap-2 text-xs pl-3 cursor-pointer rounded px-2 py-1 -mx-2 hover:bg-slate-50 focus-visible:outline-2 focus-visible:outline-blue-500"
                  :class="selectedSpan && selectedSpan.span_id === span.span_id ? 'bg-blue-50' : ''"
                  @click="onSelectSpan(span); toggleToolSearchExpanded(span.span_id); maybeFetchContent(span)"
                >
                  <span class="inline-block w-1.5 h-1.5 rounded-full shrink-0" :class="dotColor(span.name)"></span>
                  <span class="font-mono text-[11px] text-slate-400 shrink-0">{{ fmtClock(span.start_time) }}</span>
                  <span class="text-slate-400 shrink-0 select-none w-3 text-center">{{ toolSearchExpanded(span.span_id) ? '▾' : '▸' }}</span>
                  <span class="break-all flex-1 min-w-0 whitespace-pre-line text-slate-700">{{ fullLabel(span) }}</span>
                  <span v-if="span.duration_ms" class="font-mono text-[11px] text-slate-400 shrink-0">{{ fmtDuration(span.duration_ms) }}</span>
                </div>
                <div
                  v-if="toolSearchExpanded(span.span_id)"
                  class="ml-6 mt-1 rounded-md bg-slate-50 border border-slate-200 px-3 py-2 text-[12px] font-mono text-slate-700 space-y-1"
                >
                  <div v-if="span.attributes?.query" class="flex gap-2">
                    <span class="text-slate-400 shrink-0 w-28">query</span>
                    <span class="break-all">{{ span.attributes.query }}</span>
                  </div>
                  <div v-if="span.attributes?.max_results != null" class="flex gap-2">
                    <span class="text-slate-400 shrink-0 w-28">max_results</span>
                    <span>{{ span.attributes.max_results }}</span>
                  </div>
                  <div v-if="span.attributes?.loaded_tools?.length" class="flex gap-2">
                    <span class="text-slate-400 shrink-0 w-28">loaded ({{ span.attributes.loaded_tools.length }})</span>
                    <span class="break-all">{{ span.attributes.loaded_tools.join(', ') }}</span>
                  </div>
                  <div
                    v-else-if="span.attributes?.selected_tools?.length"
                    class="flex gap-2"
                    :title="'No tool_response.matches captured — falling back to the parsed select: list. Pre-feature spans only.'"
                  >
                    <span class="text-slate-400 shrink-0 w-28">selected ({{ span.attributes.selected_tools.length }})</span>
                    <span class="break-all">{{ span.attributes.selected_tools.join(', ') }}</span>
                  </div>
                  <div v-if="span.attributes?.total_deferred_tools != null" class="flex gap-2">
                    <span class="text-slate-400 shrink-0 w-28">deferred pool</span>
                    <span>{{ span.attributes.total_deferred_tools }}</span>
                  </div>
                </div>
              </div>

              <!-- Rule check row: status + engine·lang chips + file basename
                   on the left, applicable/total count pinned to the right.
                   Full per-rule list (severity, summary, guide) lives in
                   the Span details side panel.

                   Layout caveat: the text-bearing content is wrapped in
                   ONE flex child (the inner div). Putting each span
                   directly under the flex row would make every span an
                   anonymous flex item, and `window.getSelection().toString()`
                   injects a newline between every flex item — pasting the
                   result into the browser's find bar then matches nothing.
                   Inside the wrapper, `{{ ' ' }}` text nodes put literal
                   space characters in the DOM (Vue's template compiler
                   otherwise strips whitespace between sibling tags),
                   matching what selection captures end-to-end. -->
              <div
                v-else-if="span.name === 'rule.check'"
                tabindex="0"
                class="flex items-center gap-2 text-xs pl-3 cursor-pointer rounded px-2 py-1 -mx-2 hover:bg-slate-50 focus-visible:outline-2 focus-visible:outline-blue-500"
                :class="selectedSpan && selectedSpan.span_id === span.span_id ? 'bg-blue-50' : ''"
                @click="onRowClick(span)"
              >
                <span class="inline-block w-1.5 h-1.5 rounded-full shrink-0" :class="dotColor(span.name)"></span>
                <span class="font-mono text-[11px] text-slate-400 shrink-0 cursor-text select-text">{{ fmtClock(span.start_time) }}</span>
                <div class="flex-1 min-w-0 truncate cursor-text select-text whitespace-nowrap">
                  <span class="text-slate-500">rule</span>
                  {{ ' ' }}<template v-for="(tag, ti) in (span.attributes?.engine_tags || [])" :key="ti"
                    ><span
                      class="font-mono text-[10px] text-slate-600 bg-slate-100 border border-slate-200 px-1 rounded"
                      :title="`engine: ${tag.engine}, language: ${tag.language}`"
                    >{{ tag.engine }}·{{ tag.language }}</span>{{ ' ' }}</template
                  ><span
                    v-if="span.attributes?.status === 'violation'"
                    class="text-red-700 bg-red-50 border border-red-200 px-1 rounded text-[10px]"
                  >⚠ {{ span.attributes.violating_rule_count }}</span
                  ><span
                    v-else-if="span.attributes?.status === 'no_applicable_rules'
                      || span.attributes?.status === 'all_rules_out_of_scope'"
                    class="text-slate-500 italic"
                    :title="span.attributes?.status === 'no_applicable_rules'
                      ? 'no rules applied to this file (check passed)'
                      : 'all configured rules are out of scope (check passed)'"
                  >ok·n/a</span
                  ><span
                    v-else
                    class="text-emerald-700"
                    title="all applicable rules passed"
                  >ok</span>
                  {{ ' ' }}<span
                    class="text-slate-700"
                    :title="span.attributes?.relative_path || ''"
                  >{{ span.attributes?.relative_path ? span.attributes.relative_path.split('/').pop() : '' }}</span>
                </div>
                <span
                  class="font-mono text-[11px] text-slate-400 shrink-0 tabular-nums cursor-text select-text"
                  :title="`${span.attributes?.applicable_rule_count || 0} applicable of ${span.attributes?.total_rules || 0} configured rules`"
                >{{ span.attributes?.applicable_rule_count || 0 }}/{{ span.attributes?.total_rules || 0 }}</span>
              </div>

              <!-- Local command (`!ls` bang/bash or `/clear` slash):
                   one-liner showing the command, expandable into a dark
                   terminal panel with stdout/stderr — mirrors the
                   tool.Bash row. The leading `!` / `/` already signals
                   the kind, so no `$` shell prefix here. -->
              <div
                v-else-if="span.name === 'harness.local_command'"
                class="group"
              >
                <div
                  tabindex="0"
                  class="flex items-center gap-2 text-xs pl-3 cursor-pointer rounded px-2 py-1 -mx-2 hover:bg-slate-50 focus-visible:outline-2 focus-visible:outline-blue-500"
                  :class="selectedSpan && selectedSpan.span_id === span.span_id ? 'bg-blue-50' : ''"
                  @click="onSelectSpan(span); toggleBashExpanded(span.span_id); maybeFetchContent(span)"
                >
                  <span class="inline-block w-1.5 h-1.5 rounded-full shrink-0" :class="dotColor(span.name)"></span>
                  <span class="font-mono text-[11px] text-slate-400 shrink-0">{{ fmtClock(span.start_time) }}</span>
                  <span
                    v-if="span.attributes?.stdout || span.attributes?.stderr"
                    class="text-slate-400 shrink-0 select-none w-3 text-center"
                  >{{ bashExpanded(span.span_id) ? '▾' : '▸' }}</span>
                  <span v-else class="w-3 shrink-0"></span>
                  <span class="break-all flex-1 min-w-0 whitespace-pre-line font-mono text-teal-700 font-semibold">{{ span.attributes?.command_name || fullLabel(span) }}</span>
                  <span v-if="span.duration_ms" class="font-mono text-[11px] text-slate-400 shrink-0">{{ fmtDuration(span.duration_ms) }}</span>
                </div>
                <div
                  v-if="bashExpanded(span.span_id) && (span.attributes?.stdout || span.attributes?.stderr)"
                  class="ml-6 mt-1 rounded-md bg-slate-900 border border-slate-800 overflow-hidden"
                >
                  <div v-if="span.attributes?.stdout" class="px-3 py-2">
                    <div class="flex items-center gap-2 mb-1">
                      <span class="text-[10px] font-semibold uppercase tracking-wider text-slate-400">stdout</span>
                      <span
                        v-if="span.attributes?.stdout_truncated"
                        class="text-[10px] text-amber-300 bg-amber-900/40 border border-amber-700/60 px-1 rounded"
                      >truncated</span>
                      <button
                        type="button"
                        class="ml-auto opacity-0 group-hover:opacity-100 transition-opacity px-1.5 py-0.5 rounded text-[10px] text-slate-400 hover:bg-slate-700/60 focus-visible:outline-2 focus-visible:outline-slate-500"
                        title="Copy"
                        @click.stop="copyText(span.attributes.stdout)"
                      >Copy</button>
                    </div>
                    <pre class="text-[12px] text-slate-100 whitespace-pre-wrap break-words font-mono leading-snug max-h-96 overflow-auto">{{ span.attributes.stdout }}</pre>
                  </div>
                  <div
                    v-if="span.attributes?.stderr"
                    class="px-3 py-2"
                    :class="span.attributes?.stdout ? 'border-t border-slate-800' : ''"
                  >
                    <div class="flex items-center gap-2 mb-1">
                      <span class="text-[10px] font-semibold uppercase tracking-wider text-red-400">stderr</span>
                      <span
                        v-if="span.attributes?.stderr_truncated"
                        class="text-[10px] text-amber-300 bg-amber-900/40 border border-amber-700/60 px-1 rounded"
                      >truncated</span>
                      <button
                        type="button"
                        class="ml-auto opacity-0 group-hover:opacity-100 transition-opacity px-1.5 py-0.5 rounded text-[10px] text-red-400 hover:bg-red-900/40 focus-visible:outline-2 focus-visible:outline-red-500"
                        title="Copy"
                        @click.stop="copyText(span.attributes.stderr)"
                      >Copy</button>
                    </div>
                    <pre class="text-[12px] text-red-300 whitespace-pre-wrap break-words font-mono leading-snug max-h-64 overflow-auto">{{ span.attributes.stderr }}</pre>
                  </div>
                </div>
              </div>

              <!-- Workflow phase band (dynamic-workflow runs only) -->
              <div
                v-else-if="span.name === 'workflow.phase'"
                class="flex items-center gap-2 my-3 select-none"
              >
                <div class="flex-1 border-t border-dashed border-emerald-300"></div>
                <span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full border border-emerald-300 bg-emerald-50 text-[10px] font-semibold uppercase tracking-wider text-emerald-700">
                  <span>Phase {{ span.attributes?.index ?? '' }}</span>
                  <template v-if="span.attributes?.title"><span class="text-emerald-400">·</span><span class="normal-case">{{ span.attributes.title }}</span></template>
                  <span class="text-emerald-400">·</span>
                  <span class="normal-case">{{ agentCountForPhase(span.span_id) }} agent<span v-if="agentCountForPhase(span.span_id) !== 1">s</span></span>
                </span>
                <div class="flex-1 border-t border-dashed border-emerald-300"></div>
              </div>

              <!-- Subagent launch: subagent.start with its tool.Agent
                   (description + prompt) folded in. Workflow agents have no
                   launch span, so description/prompt fall back to the
                   subagent.start span's own attributes (see agent* helpers). -->
              <div v-else-if="span.name === 'subagent.start'">
                <div
                  tabindex="0"
                  class="flex items-center gap-2 text-xs cursor-pointer rounded-md px-2.5 py-1.5 border border-slate-200 bg-slate-50 hover:bg-slate-100 hover:border-slate-300 transition-colors focus-visible:outline-2 focus-visible:outline-blue-500"
                  :class="selectedSpan && selectedSpan.span_id === span.span_id ? '!bg-blue-50 !border-blue-300 ring-1 ring-blue-200' : ''"
                  @click="onSelectSpan(span); maybeFetchContent(span)"
                >
                  <span class="inline-block w-1.5 h-1.5 rounded-full shrink-0" :class="toolRowDotClass(span)"></span>
                  <span class="font-mono text-[11px] text-slate-400 shrink-0">{{ fmtClock(span.start_time) }}</span>
                  <!-- Label: normal subagents read `subagent: <type> · <desc>`.
                       Workflow agents have no agent_type (the default workflow
                       subagent), so the `subagent:` prefix would render bare —
                       show just the agent's label instead. -->
                  <span class="break-all flex-1 min-w-0 whitespace-pre-line text-sm font-semibold text-slate-800">
                    <template v-if="span.attributes?.agent_type">{{ fullLabel(span) }}<template v-if="agentDescription(span)"><span class="text-slate-300"> · </span><span class="text-slate-600 font-normal">{{ agentDescription(span) }}</span></template></template>
                    <template v-else>{{ agentDescription(span) || 'agent' }}</template>
                  </span>
                  <span
                    v-if="span.attributes?.model"
                    class="font-mono text-[10px] text-slate-500 bg-slate-100 border border-slate-200 px-1 rounded shrink-0"
                    :title="span.attributes.model"
                  >{{ fmtModel(span.attributes.model) }}</span>
                  <span v-if="span.attributes?.tool_calls" class="font-mono text-[11px] text-slate-400 shrink-0">{{ span.attributes.tool_calls }} tool<span v-if="span.attributes.tool_calls !== 1">s</span></span>
                  <span v-if="span.attributes?.tokens" class="font-mono text-[11px] text-slate-400 shrink-0">{{ fmtTokens(span.attributes.tokens) }} tok</span>
                  <span v-if="span.duration_ms" class="font-mono text-[11px] text-slate-400 shrink-0">{{ fmtDuration(span.duration_ms) }}</span>
                </div>
                <!-- Task prompt card (collapsed by default) -->
                <div
                  v-if="agentPrompt(span)"
                  class="ml-6 mt-1 mb-1 rounded-md border border-slate-200 bg-slate-50 px-3 py-2 cursor-pointer hover:border-slate-300"
                  :class="selectedSpan && selectedSpan.span_id === agentPromptOwnerId(span) ? 'ring-1 ring-blue-300' : ''"
                  @click="selectAgentPrompt(span)"
                >
                  <div class="flex items-center justify-between gap-2 mb-1">
                    <span class="text-[10px] font-semibold uppercase tracking-wider text-slate-400">task prompt</span>
                    <button
                      v-if="agentPrompt(span).length > AGENT_PROMPT_PREVIEW_CHARS"
                      type="button"
                      class="text-[11px] font-medium text-blue-600 hover:text-blue-800 rounded focus-visible:outline-2 focus-visible:outline-blue-500"
                      @click.stop="toggleAgentPrompt(span.span_id)"
                    >{{ isAgentPromptExpanded(span.span_id) ? 'Collapse' : `Show full prompt · ${agentPrompt(span).length} chars` }}</button>
                  </div>
                  <div :class="isAgentPromptExpanded(span.span_id) ? 'max-h-[32rem] overflow-y-auto' : ''">
                    <MarkdownContent
                      v-if="isAgentPromptExpanded(span.span_id)"
                      :markdown="agentPrompt(span)"
                    />
                    <div
                      v-else
                      class="text-[12.5px] text-slate-700 whitespace-pre-wrap break-words leading-relaxed"
                    >{{ agentPromptPreview(agentPrompt(span)) }}</div>
                  </div>
                </div>
              </div>

              <!-- Deferred agent RESULT card (workflow runs): the span-tree
                   projection emits this AFTER the agent's turns, so each agent
                   reads prompt → work → result (the result used to render
                   bundled into the header block, before the work). `span` is a
                   synthetic marker carrying the agent's attributes. -->
              <div
                v-else-if="span.name === 'workflow.agent_result'"
                class="mt-1 mb-2 rounded-md border border-emerald-200 bg-emerald-50/50 px-3 py-2"
              >
                <div class="flex items-center justify-between gap-2 mb-1">
                  <span class="text-[10px] font-semibold uppercase tracking-wider text-emerald-700">result</span>
                  <button
                    v-if="agentResultText(span).length > AGENT_RESULT_PREVIEW_CHARS"
                    type="button"
                    class="text-[11px] font-medium text-emerald-700 hover:text-emerald-900 rounded focus-visible:outline-2 focus-visible:outline-emerald-500"
                    @click.stop="toggleAgentResult(span.span_id)"
                  >{{ isAgentResultExpanded(span.span_id) ? 'Collapse' : `Show full · ${agentResultText(span).length} chars` }}</button>
                </div>
                <div
                  class="text-[12.5px] text-slate-700 whitespace-pre-wrap break-words leading-relaxed font-mono"
                  :class="isAgentResultExpanded(span.span_id) ? 'max-h-[32rem] overflow-y-auto' : ''"
                >{{ isAgentResultExpanded(span.span_id) ? agentResultText(span) : agentResultPreview(agentResultText(span)) }}</div>
              </div>

              <!-- Dynamic-workflow launch: the Workflow tool call as a
                   first-class row with an inline jump to the captured run.
                   `workflow_run_id` + `workflow_name` are stamped on this
                   span at ingest by matching the script, so the "view run →"
                   link appears once the run has been captured. -->
              <div
                v-else-if="span.name === 'tool.Workflow'"
                tabindex="0"
                class="flex items-center gap-2 text-xs pl-3 cursor-pointer rounded px-2 py-1 -mx-2 hover:bg-emerald-50/60 focus-visible:outline-2 focus-visible:outline-emerald-500"
                :class="selectedSpan && selectedSpan.span_id === span.span_id ? 'bg-emerald-50' : ''"
                @click="onSelectSpan(span); maybeFetchContent(span)"
              >
                <span class="inline-block w-1.5 h-1.5 rounded-full shrink-0 bg-emerald-500"></span>
                <span class="font-mono text-[11px] text-slate-400 shrink-0">{{ fmtClock(span.start_time) }}</span>
                <span class="font-medium text-emerald-700 shrink-0">⚙ Workflow</span>
                <span
                  v-if="span.attributes?.workflow_name"
                  class="font-mono text-[11px] text-slate-600 truncate flex-1 min-w-0"
                >{{ span.attributes.workflow_name }}</span>
                <span v-else class="flex-1"></span>
                <router-link
                  v-if="span.attributes?.workflow_run_id"
                  :to="`/trace/sessions/${span.attributes.workflow_run_id}`"
                  class="shrink-0 inline-flex items-center gap-1 px-1.5 py-0.5 rounded border border-emerald-300 bg-emerald-50 text-[11px] font-medium text-emerald-700 hover:bg-emerald-100 no-underline focus-visible:outline-2 focus-visible:outline-emerald-500"
                  title="Open the captured trace for this workflow run"
                  @click.stop
                >view run →</router-link>
                <span v-if="span.duration_ms" class="font-mono text-[11px] text-slate-400 shrink-0">{{ fmtDuration(span.duration_ms) }}</span>
              </div>

              <!-- Inline tool / skill / edit rows -->
              <div
                v-else-if="
                  span.name.startsWith('tool.')
                  || span.name === 'skill.read'
                  || span.name === 'skill.invoke'
                  || span.name === 'file.edit'
                  || span.name === 'plan.edit'
                  || span.name.startsWith('subagent.')
                "
                tabindex="0"
                class="flex items-center gap-2 text-xs pl-3 cursor-pointer rounded px-2 py-1 -mx-2 hover:bg-slate-50 focus-visible:outline-2"
                :class="[
                  span.attributes?.denied ? 'focus-visible:outline-amber-500' : (span.attributes?.rejected ? 'focus-visible:outline-red-500' : 'focus-visible:outline-blue-500'),
                  selectedSpan && selectedSpan.span_id === span.span_id
                    ? (span.attributes?.denied ? 'bg-amber-50' : (span.attributes?.rejected ? 'bg-red-50' : 'bg-blue-50'))
                    : '',
                ]"
                @click="onSelectSpan(span); maybeFetchContent(span)"
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
                <!-- Interrupt badge for any non-AskUserQuestion permission-deny
                     synth span (`tooldeny-*` from turn_trace). AskUserQuestion
                     gets its own richer card above; this row covers every
                     other tool (browser_evaluate, Bash, Edit, …) the user
                     stopped at the permission prompt. "Interrupted" matches
                     Claude Code's own terminal label for the same event. -->
                <span
                  v-if="span.attributes?.denied"
                  class="font-sans uppercase tracking-wider text-[10px] bg-amber-100 border border-amber-200 text-amber-800 px-1 rounded shrink-0"
                >{{ span.attributes.deny_kind === 'chat' ? 'chat instead' : 'Interrupted' }}</span>
                <span
                  v-else-if="span.attributes?.rejected"
                  class="font-sans uppercase tracking-wider text-[10px] bg-red-100 border border-red-200 text-red-800 px-1 rounded shrink-0"
                >Rejected</span>
                <span v-if="span.duration_ms" class="font-mono text-[11px] text-slate-400 shrink-0">{{ fmtDuration(span.duration_ms) }}</span>
              </div>
              </div>
            </template>

            <!-- Collapse trigger when expanded -->
            <button
              type="button"
              class="text-[11px] text-slate-400 hover:text-slate-700 pl-2 cursor-pointer rounded px-1 focus-visible:outline-2 focus-visible:outline-blue-500"
              @click="togglePromptExpanded(entry.prompt.span_id)"
            >hide details ▴</button>
          </template>

          <!-- Turn metadata footer -->
          <div
            v-if="turnItems[entryIdx]?.turn"
            class="text-[11px] text-slate-400 flex items-center gap-2 pl-2"
          >
            <span>Turn #{{ turnItems[entryIdx].idx + 1 }}</span>
            <span class="text-slate-300">·</span>
            <span v-if="turnItems[entryIdx].turn.input_tokens != null">↑{{ fmtTokens((turnItems[entryIdx].turn.input_tokens || 0) + (turnItems[entryIdx].turn.cache_creation_tokens || 0)) }}</span>
            <span v-if="turnItems[entryIdx].turn.output_tokens != null">↓{{ fmtTokens(turnItems[entryIdx].turn.output_tokens) }}</span>
            <span
              v-if="turnCtxPct(turnItems[entryIdx].turn) != null"
              class="inline-flex items-center px-1 rounded text-[10px] text-white"
              :class="turnCtxPct(turnItems[entryIdx].turn) >= 80
                ? 'bg-red-500'
                : turnCtxPct(turnItems[entryIdx].turn) >= 50
                  ? 'bg-amber-500'
                  : 'bg-green-500'"
            >{{ Math.round(turnCtxPct(turnItems[entryIdx].turn)) }}%</span>
            <span
              v-if="turnItems[entryIdx].turn.effort_level"
              class="inline-flex items-center px-1 rounded text-[10px] bg-violet-100 text-violet-700"
              :title="'reasoning effort level for this turn: ' + turnItems[entryIdx].turn.effort_level"
            >{{ turnItems[entryIdx].turn.effort_level }}</span>
          </div>
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
          class="flex items-center gap-2 text-xs text-slate-500 px-2 py-1 rounded cursor-pointer hover:bg-slate-50"
          @click="onSelectSpan(entry.span)"
        >
          <span class="inline-block w-1.5 h-1.5 rounded-full shrink-0" :class="dotColor(entry.span.name)"></span>
          <span class="font-mono text-[11px]">{{ fmtClock(entry.span.start_time) }}</span>
          <span class="break-words">{{ fullLabel(entry.span) }}</span>
        </div>
      </div>

      <!-- Empty state -->
      <div v-if="!entries.length" class="text-slate-400 text-center py-8">
        No events recorded for this session.
      </div>
    </div>

  </div>
</template>
