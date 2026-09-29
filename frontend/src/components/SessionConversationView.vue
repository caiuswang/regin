<script setup>
import { ref, computed, nextTick, watch, provide, toRef, onMounted, onBeforeUnmount } from 'vue'
import ConversationToc from './conversation/ConversationToc.vue'
import ConversationSpineCard from './conversation/ConversationSpineCard.vue'
import ConversationPromptCard from './conversation/ConversationPromptCard.vue'
import TurnToolChips from './conversation/TurnToolChips.vue'
import CompactionDivider from './conversation/CompactionDivider.vue'
import SpineLiveNode from './conversation/SpineLiveNode.vue'
import BackgroundShellsBlock from './conversation/BackgroundShellsBlock.vue'
import { runStateOf } from '../utils/liveRows.js'
import TurnUsageFooter from './conversation/TurnUsageFooter.vue'
import RewindCard from './conversation/RewindCard.vue'
import ConversationFooter from './conversation/ConversationFooter.vue'
import FoldStrip from './conversation/cards/FoldStrip.vue'
import CarriedSpan from './conversation/CarriedSpan.vue'
import SpineFoldToggle from './conversation/SpineFoldToggle.vue'
import { rendersInConversation, foreignAgentIds, tierOf, keepsOwnCard, chipSpans, TIER_CONDENSED, TIER_FOLDED } from './conversation/dispatch.js'
import { taskSnapshotsBySpan } from '../utils/taskSnapshots.js'
import { useSpanTree } from '../composables/useSpanTree.js'
import { useConversationPins } from '../composables/useConversationPins.js'
import { useConversationFolding } from '../composables/useConversationFolding.js'
import { useFocusMode } from '../composables/useFocusMode.js'
import { useDetailSpine } from '../composables/useDetailSpine.js'
import { useSendReveal } from '../composables/useSendReveal.js'
import { useAgentLaunchMerge } from '../composables/useAgentLaunchMerge.js'
import { useBoundedTrace } from '../composables/useBoundedTrace.js'
import { getTraceScroller } from '../utils/traceScroller.js'
import {
  fmtClock, fullLabel, dotColor, isSupersededSegment,
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
  // External follow-tail mirror (the ≥xl companion pane tracks the main
  // feed's follow state): null = own the choice locally; true/false = driven.
  // A local user scroll-up still cancels follow here — the mirror only moves
  // when the main feed's state flips.
  followActive: { type: Boolean, default: null },
  // False while the current selection was raised by the OTHER feed instance
  // (main feed ⇄ companion pane share one `selectedSpan`). A foreign selection
  // must never rearrange this feed: no prompt/agent unfolding, no scroll to a
  // row the reader hasn't opened. See the cross-highlight watcher.
  followSelection: { type: Boolean, default: true },
  // Scroll container for the pin/follow-tail machinery. Defaults to the
  // page-level `.content-scroll`; the ≥xl companion pane passes its own
  // scrollable element so its embedded instance never attaches listeners to —
  // or writes scrollTop / overflow-anchor on — the page scroller.
  scrollerGetter: { type: Function, default: null },
  // True while the companion pane is open on a viewport too narrow for three
  // columns (xl but not 2xl): the TOC rail (user-resizable, shrink-0) yields
  // so the feed column keeps a readable width beside the pane.
  hideToc: { type: Boolean, default: false },
  // `session.task_list.events` — the whole-session task-write log. Computed
  // server-side over ALL spans (a client-side scan would miss tasks whose
  // owning prompt is still collapsed) and replayed here into per-span
  // snapshots for the task-list cards.
  taskEvents: { type: Array, default: () => [] },
  // The session's follow-up context, as ONE object:
  // { canFollowUp, model, ctxPct, liveScreen, bridgeSession }. Taken whole
  // rather than unpacked so this feed and the span reader's docked composer
  // cannot be handed different subsets of it — which is exactly how the two
  // drifted apart.
  followUp: { type: Object, default: () => ({}) },
  // Bumped by a send from ANOTHER surface addressed at this session (the span
  // reader's Reply). A send is a send: it should clear the deck here too.
  sendSeq: { type: Number, default: 0 },
  // `session.background_shells` — the whole-session shell roster. Server-owned
  // for the same reason `agents` is: a shell running for half an hour has
  // scrolled out of the loaded page, and that is the one worth surfacing.
  backgroundShells: { type: Array, default: () => [] },
  // The session's whole agent roster (useLiveAgents entries). The server owns
  // each agent's status, so a subagent card's liveness — and whether its rows
  // may appear here at all — is read off this, never off the marker span.
  agents: { type: Array, default: () => [] },
  // Server clock pair for elapsed readouts (see useAgentElapsed).
  serverNow: { type: String, default: '' },
  serverNowAt: { type: Number, default: 0 },
})

const emit = defineEmits(['select-span', 'fetch-content', 'load-subtree', 'load-subtrees', 'jump-live', 'enter-scope', 'present', 'follow-latest', 'resumed'])

// Active-scope highlight signal for the descendant SubagentCards (≥xl split):
// provided rather than prop-threaded through the ConversationSpanCard
// dispatcher. The pane's embedded scoped instance passes '', so only the main
// feed's originating card lights up.
provide('traceScopedAgentId', toRef(props, 'scopedAgentId'))

// agent_id → roster entry, for the subagent cards' liveness and progress line
// and for `runStateFor` below. Provided rather than prop-threaded through the
// card dispatcher, like the scope signal above.
const agentById = computed(() => new Map(props.agents.map((a) => [a.agentId, a])))
provide('traceAgentById', agentById)
// Which roster ids mark a row as belonging to some OTHER feed than this one.
const foreignAgents = computed(() => foreignAgentIds(props.agents, props.scopeAgent))
provide('traceServerClock', computed(() => ({ now: props.serverNow, at: props.serverNowAt })))

// One reading of a span's liveness for the whole spine. A subagent card is
// live when its AGENT is: the marker span is never PENDING, and the
// `tool.Agent` placeholder that is (`runStateOf`) gets folded into the card
// and lost — so the roster's status is what answers, with the folded launch
// as the fallback for an agent the roster does not know yet. `queued` from
// the merge is the runner's stream-time placeholder for a call the harness
// has not started, and passes through as itself.
const AGENT_RUN_STATES = { running: 'running', waiting: 'waiting' }
function runStateFor(span) {
  if (span?.name !== 'subagent.start') return runStateOf(span)
  if (isSupersededSegment(span.attributes)) return ''
  const entry = agentById.value.get(span.attributes?.agent_id)
  if (entry) return AGENT_RUN_STATES[entry.status] || ''
  return runStateOf(agentMerge.launchForSubagent(span))
}

// span_id → task-list snapshot as of that task-write span, for the cards the
// dispatcher renders. Provided (not prop-threaded) for the same reason as the
// scope signal above: it would otherwise cross every card boundary.
const taskSnapshots = computed(() => taskSnapshotsBySpan(props.taskEvents))
provide('traceTaskSnapshots', taskSnapshots)
// `TaskListCard` is the second self-contained collapsed card in the chain, and
// the condensed row has nowhere to put the checklist it replays — so Focus
// swapping it in threw the whole task list away for no density gain.
const keepsCard = (span) =>
  keepsOwnCard(span, !!taskSnapshots.value.get(span?.span_id)?.tasks?.length)

// Only the instance nobody handed a scroller to owns the view's scroll region:
// the companion pane's embedded feed scrolls the PANE, so marking its column
// too would leave `getTraceScroller()` picking whichever matched first.
const bounded = useBoundedTrace()
const ownsScroller = computed(() => bounded.value && !props.scrollerGetter)

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
      // The marker stays in the PARENT conversation while `spans` here is the
      // agent's own trace, so the run facts a workflow marker carries inline
      // (task prompt, result) reach the scope through the roster entry.
      marker: props.scopeAgent.startSpan?.attributes || {},
    }
  : null))

// Workflow-run projections emit a synthetic workflow.agent_result AFTER each
// agent's turns (_pushAgent), so SubagentCard must not render its own result
// card there — provided so the flag skips the dispatcher's prop chain.
provide('traceIsWorkflowRun', isWorkflow)

const sessionEnded = computed(() => props.spans.some(s => s.name === 'session.end'))

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
  return getTraceScroller()
}
// `block: 'nearest'` semantics against THIS instance's own scroller.
//
// Deliberately not `el.scrollIntoView()`: that walks every scrollable
// ancestor, so the companion pane's reveal also scrolls the page scroller to
// bring the pane into view. With both feeds reacting to one `selectedSpan`
// that meant two smooth animations queued on `.content-scroll` in the same
// tick — they don't merge, the later one simply wins, so the main feed's
// reveal silently never landed. One instance, one scroller, no fight.
function revealRow(el) {
  const s = getConversationScroller()
  if (!el || !s || typeof s.scrollTo !== 'function') return
  const page = s === document.scrollingElement || s === document.documentElement
  const portTop = page ? 0 : s.getBoundingClientRect().top
  const portBottom = page ? window.innerHeight : s.getBoundingClientRect().bottom
  // scroll-margin is what gives a prompt card its sticky-header clearance;
  // scrollIntoView honored it for free, a manual scroll has to read it.
  const cs = window.getComputedStyle(el)
  const rect = el.getBoundingClientRect()
  const top = rect.top - (parseFloat(cs.scrollMarginTop) || 0)
  const bottom = rect.bottom + (parseFloat(cs.scrollMarginBottom) || 0)
  const above = top < portTop
  const below = bottom > portBottom
  // Already in view, or taller than the port and straddling it — 'nearest'
  // leaves both alone.
  if ((!above && !below) || (above && below)) return
  s.scrollTo({ top: s.scrollTop + (above ? top - portTop : bottom - portBottom),
    behavior: 'smooth' })
}
// Force-expand the prompt/agent that owns a freshly-pinned span so the
// auto-fold watcher can't collapse the pinned row out of the DOM.
//
// A row the reader could click the pin on is by definition already mounted, so
// unfolding anything there is pure collateral: it popped open the enclosing
// subagent's whole subtree on every pin. Only a pin raised on a row that ISN'T
// rendered (programmatic) needs the unfold.
function ensurePinVisible(spanId) {
  if (resolvePinEl(spanId)) return
  const owner = promptGroups.value.find(g =>
    g.prompt.span_id === spanId || g.descendants.some(d => d.span.span_id === spanId))
  if (owner && !isPromptExpanded(owner.prompt.span_id)) togglePromptExpanded(owner.prompt.span_id, true)
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

// The main feed exposes its followTail so the companion pane can mirror it
// (trace-conversation-region wires the two); the pane never shows its own
// pill, it rides this prop. flush:'post' so the immediate fire (pane opening
// while the main feed is already following) scrolls a mounted scroller.
defineExpose({ followTail })

// Only the pill's own click announces follow-latest. The mirror watcher below
// drives the companion pane off the main feed's state, and letting that path
// emit would report one reader gesture twice.
function onFollowPill() {
  if (followTail.value) { disableFollow(); return }
  enableFollow()
  emit('follow-latest')
}
watch(() => props.followActive, (on) => {
  if (on == null) return
  if (on) enableFollow()
  else disableFollow()
}, { immediate: true, flush: 'post' })

// Subagent launch merge + the agent-metadata helpers the subagent / phase /
// result cards consume (passed down as the `agentMerge` object).
const agentMerge = useAgentLaunchMerge(() => props.spans, childrenOf)

// All expand/collapse state. Two callbacks keep it decoupled from this
// component's emit + content-fetch concerns.
const folding = useConversationFolding({
  childrenOf,
  onLoadSubtree: (id) => emit('load-subtree', id),
  onExpandPrompt: (id) => { emit('load-subtree', id); fetchMissingContentForPrompt(id) },
})
const {
  expandedPromptIds, collapsedPromptIds,
  isPromptExpanded, isPromptCollapsed, isPromptBodyExpanded,
  setPromptExpanded, togglePromptExpanded, togglePromptBodyExpanded,
  isWorkflowExpanded,
  isRewindExpanded, toggleRewindExpanded,
} = folding

// Everything one spine row's card chain needs, as ONE prop. Taken whole for
// the same reason `rewindCtx` below is: threading eight of these through the
// row would let this feed and its rows disagree about any of them.
const spineCtx = computed(() => ({
  selectedSpan: props.selectedSpan,
  folding,
  agentMerge,
  workflowRunsById: props.workflowRunsById,
  pinnedSpanId: pinnedSpanId.value,
  pinnable: isPinnable,
  keepsCard,
  bodyOpen: isManuallyOpen,
}))

// Context bundle the discarded-branch span cards (rendered inside RewindCard)
// need — passed as one prop so the marker's template footprint stays small.
const rewindCtx = computed(() => ({
  selectedSpan: props.selectedSpan,
  folding,
  agentMerge,
  workflowRunsById: props.workflowRunsById,
}))

// Fold collapsed workflows out of an expanded prompt's descendant list.
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
  // Drop spans ConversationSpanCard has no branch for. They used to reach the
  // spine and render an empty row — a node punched onto the wire with nothing
  // attached — and they inflated the turn header's event count well past what
  // the reader could see (259 claimed vs 61 shown on one turn).
  const dispatched = live.filter(({ span }) => rendersInConversation(span))
  const launchless = merged.size
    ? dispatched.filter(({ span }) => !merged.has(span.span_id))
    : dispatched
  // A subagent's work is served by its own trace and stands here as its
  // card alone: a row tagged with a roster agent's id is the child's, never
  // the parent's, whichever read window delivered it. A scoped feed's OWN
  // agent is not foreign to it (see `foreignAgentIds`). `workflow.agent_result`
  // is the agent card's own closing row, copied off the marker so it keeps
  // the agent's id — the card's, not the child's.
  const base = foreignAgents.value.size
    ? launchless.filter(({ span }) => span.name.startsWith('subagent.')
      || span.name === 'workflow.agent_result'
      || !foreignAgents.value.has(span.attributes?.agent_id))
    : launchless
  const visible = []
  let collapsingWorkflow = false
  for (const d of base) {
    // A collapsed `tool.Workflow` hides its entire subtree behind the single
    // workflow card. Keep the card row.
    if (d.span.name === 'tool.Workflow') {
      collapsingWorkflow = !isWorkflowExpanded(d.span.span_id)
      visible.push(d)
      continue
    }
    if (d.inWorkflow) {
      if (collapsingWorkflow) continue
    } else {
      collapsingWorkflow = false
    }
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
const { arm: armSendReveal, applyTo: applySendReveal } =
  useSendReveal({ expandedPromptIds, isExempt: promptOwnsPinned, traceId: toRef(props, 'traceId') })
watch(() => props.sendSeq, () => armSendReveal())
watch(promptGroups, (groups) => {
  if (!groups.length) return
  const latest = groups[groups.length - 1].prompt
  if (lastAutoExpandedId.value === latest.span_id) return
  // Don't auto-fold the turn that owns a pinned span — the pinned row must
  // stay mounted for the scroll anchor to track it.
  if (lastAutoExpandedId.value && !promptOwnsPinned(lastAutoExpandedId.value)) {
    expandedPromptIds.value.delete(lastAutoExpandedId.value)
  }
  // Clear any stale collapse on this id first, or the two sets disagree and
  // the arriving turn lands closed.
  collapsedPromptIds.value.delete(latest.span_id)
  expandedPromptIds.value.add(latest.span_id)
  lastAutoExpandedId.value = latest.span_id
  applySendReveal(latest.span_id)
  emit('load-subtree', latest.span_id)
  fetchMissingContentForPrompt(latest.span_id)
}, { immediate: true })

// ── Selection + scroll (targets live in this component) ───────
function onSelectSpan(span) { emit('select-span', span) }
// User clicked a card body → select + lazily fetch its content.
function onActivate(span) { onSelectSpan(span); maybeFetchContent(span) }
// Reaching a span's full content must never require the detail rail, so every
// row carries three routes to it: the ⤢ button, a double-click, and `P` on the
// selection. The reader selects for itself (usePresentReader's `onSelect`), so
// closing it still leaves the feed on the span that was just read — routing
// this through `onActivate` instead emitted the "explicit selection" intent
// that opens the mobile bottom sheet, stacking span details under the reader.
function onPresent(span) {
  maybeFetchContent(span)
  emit('present', span)
}
// Double-click-to-read is DELEGATED from the feed root rather than bound per
// row: every span row already carries `data-span-id`, and a `@dblclick` on the
// row wrapper would make the whole row — spine gutter included — a clickable
// container owing a pointer cursor and a hover wash that belong to the card
// inside it, not to the rail.
//
// No drag-selection guard here, unlike the single-click handlers: a
// double-click ALWAYS leaves a word selected, so that guard would reject every
// double-click there is.
const feedRootEl = ref(null)
function onFeedDblClick(e) {
  const host = e.target.closest?.('[data-span-id]')
  const span = host && spanById.value.get(host.getAttribute('data-span-id'))
  if (span) onPresent(span)
}
onMounted(() => feedRootEl.value?.addEventListener('dblclick', onFeedDblClick))
onBeforeUnmount(() => feedRootEl.value?.removeEventListener('dblclick', onFeedDblClick))

function onPromptClick(prompt) {
  // Skip the toggle when the click ended a drag-selection — otherwise
  // selecting prompt text to copy would also fold the turn.
  if (typeof window !== 'undefined') {
    const sel = window.getSelection?.()
    if (sel && sel.toString().length > 0) return
  }
  emit('select-span', prompt)
  maybeFetchContent(prompt)
  toggleTurnExpanded(prompt.span_id)
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

function promptOwnerOf(spanId) {
  return promptGroups.value.find(g =>
    g.descendants.some(d => d.span.span_id === spanId)) || null
}
// Unfold whatever hides the selected row — its turn — so there is a row to
// reveal at all.
async function unfoldFor(spanId, owner) {
  if (owner && !isPromptExpanded(owner.prompt.span_id)) {
    togglePromptExpanded(owner.prompt.span_id, true)
    await nextTick()
  }
  // Focus adds a second way for a row to be out of sight, so the reveal path
  // has to clear tier as well as fold: a `?span=` deep link into a condensed
  // or folded span otherwise lands on nothing at all.
  //
  // The two tiers hide differently and need different keys. A condensed row is
  // already on the spine, so seeding `manuallyOpen` opens its body. A folded
  // one is behind its TURN's strip, so only `openFold` can surface it —
  // seeding `manuallyOpen` for it reveals nothing, because the Tier 3 render
  // path never reads that set. With no owning turn there is no strip to open,
  // and the selection still reaches the detail rail, so this is a real no-op
  // rather than a missing case. A condensed span that kept its own card reads
  // neither set — its body is `folding`'s to open, and Focus-off does not
  // auto-open it either, so the deep link lands on it closed and selected.
  if (latestOn.value) {
    // Focus sets aside everything outside its keep-set, so the only way back to
    // one of those spans is that turn's own strip. A span already ON the spine
    // must not open it — that would make a plain click on a card the mode
    // chose to show spill the whole turn back out.
    if (owner && !detail.isKeptRow(owner.prompt.span_id, spanId)) {
      openFold(owner.prompt.span_id, 'latest')
      await nextTick()
    }
  } else if (condensedOn.value && spanById.value.has(spanId)) {
    const span = spanById.value.get(spanId)
    const tier = tierOf(span)
    if (tier === TIER_FOLDED) { if (owner) openFold(owner.prompt.span_id) }
    else if (tier === TIER_CONDENSED && !keepsCard(span)) revealSpan(spanId)
    await nextTick()
  }
}

// Cross-highlight from external selection (e.g. clicking a colored bar in the
// parent's mini-timeline strip): scroll the matching prompt — or the prompt
// that owns the selected descendant — into view. `flush: 'post'` so the
// fold-toggle reflow (re-registering spanRefs) lands before we read the ref.
//
// A selection raised by the sibling feed instance (`followSelection` false)
// gets the passive half only: it may reveal a row the reader already unfolded,
// but it must not unfold anything, and it must not fall back to yanking the
// feed to an ancestor the reader can't see.
//
// The watcher also re-fires on every live poll (the spans.length dep, so a
// selection whose subtree hadn't loaded yet still resolves once it lands).
// Unfolding must NOT re-fire then: it re-expanded the selected span's
// subagent on every poll, undoing the reader's manual collapse. Unfold only
// for a NEW selection id, or while the selected span still isn't in the
// loaded set (its subtree may arrive on a later poll).
//
// Revealing has the same constraint, and it is the sharper one: a live session
// polls every ~4s, and re-revealing an OLD selection on each tick dragged the
// scroller back to it however far the reader had scrolled away — the page
// visibly jumped up and down while spans arrived. Following the newest activity
// is the Follow-latest pill's job alone (useConversationPins), so reveal only
// for a selection this instance hasn't landed on yet.
let lastUnfoldedSpanId = null
let lastRevealedSpanId = null
watch([() => props.selectedSpan?.span_id, () => props.spans.length], async ([id]) => {
  if (!id) return
  const owner = promptOwnerOf(id)
  const follow = props.followSelection
  if (follow && (id !== lastUnfoldedSpanId || !spanById.value.has(id))) {
    lastUnfoldedSpanId = id
    await unfoldFor(id, owner)
  }
  if (id === lastRevealedSpanId) return
  await nextTick()
  const fallback = () => promptRefs.value.get(id)
    || standaloneRefs.value.get(id)
    || (owner && promptRefs.value.get(owner.prompt.span_id))
  const target = spanRefs.value.get(id) || (follow ? fallback() : null)
  // Only latch once a row actually existed to scroll to: a selection whose
  // subtree hasn't loaded yet must still get its reveal when a later poll
  // lands it, which is what the spans.length dependency is for.
  if (!target) return
  lastRevealedSpanId = id
  revealRow(target)
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

// ── Turn identity + size (prompt card header) ─────────────────
// The prompt card reads `PROMPT · TURN n` and `N events`, both derived from
// the tree we already have. `turnItems` is indexed by PROMPT GROUP, not by
// entry, so the usage footer is looked up by prompt span id rather than by
// entry index — otherwise a standalone root or a rewind divider ahead of a
// group shifts every turn's usage onto the wrong prompt.
const turnMetaById = computed(() => {
  const byId = new Map()
  promptGroups.value.forEach((g, i) => {
    byId.set(g.prompt.span_id, { number: i + 1, item: turnItems.value[i] || null })
  })
  return byId
})
function turnNumber(prompt) {
  return turnMetaById.value.get(prompt.span_id)?.number ?? null
}
function turnItemFor(prompt) {
  return turnMetaById.value.get(prompt.span_id)?.item || null
}
// Literally the list the spine renders — not a parallel filter that drifts
// from it. The previous hand-rolled version counted undispatched spans and
// folded-agent rows, so a turn showing 61 rows advertised "259 events".
// `conversation-event-count.spec.js` holds the two together.
//
// Memoized per prompt because the template asks for the same list five times
// per turn (the count in three places, the spine's `v-if`, then its `v-for`),
// and each call re-walks and re-allocates the whole descendant list on every
// live-poll re-render. The computed still reads the fold predicates inside
// `renderableDescendants`, so collapsing an agent invalidates it as before.
const renderableByPrompt = computed(() => {
  const byId = new Map()
  for (const entry of entries.value) {
    if (entry.prompt) byId.set(entry.prompt.span_id, renderableDescendants(entry))
  }
  return byId
})
function renderableFor(entry) {
  return renderableByPrompt.value.get(entry.prompt.span_id) || []
}
function eventCount(entry) {
  return renderableFor(entry).length
}

// ── Detail modes ──────────────────────────────────────────────
// Tier and rows come from the SAME list the spine renders, so what the turn
// header says about a turn cannot drift from the DOM the way `259 events` over
// 61 rows did. `tierOf` lives beside `rendersInConversation` for that reason.
const { condensedOn, latestOn, isManuallyOpen, toggleManuallyOpen, revealSpan, openFold }
  = useFocusMode()
const detail = useDetailSpine({
  getEntries: () => entries.value,
  getRowsFor: (turnId) => renderableByPrompt.value.get(turnId) || [],
  runStateFor,
  isPromptExpanded,
  isPromptCollapsed,
})

// The spine model lives in useDetailSpine: which rows a mode renders, what a
// strip sets aside, which turns the mode forces open, and what each turn
// carries down from the one above. What stays here is only the part that
// touches the DOM — element refs, scroll anchoring, and the fetch emits.
function focusRowsFor(entry) {
  return detail.rowsFor(entry.prompt.span_id)
}
// Reads the RENDERED state rather than flipping the expanded set: a turn the
// mode or a running span opened is not in that set, so a membership flip would
// re-open what the reader just asked to close.
function toggleTurnExpanded(turnId) {
  setPromptExpanded(turnId, !detail.isTurnExpanded(turnId))
}
// A folded turn's own useful tail, shown under its chips.
const ownTailFor = (entry) => detail.ownTailByTurn.value.get(entry.prompt.span_id) || null

// Opening strips only — the closing one is wherever the reader happens to be,
// and it is the OPENING one they need brought back into view.
const stripRefs = ref(new Map())
function registerStrip(row, el) {
  const key = detail.stripKey(row.turnId, row.kind)
  if (el) stripRefs.value.set(key, el); else stripRefs.value.delete(key)
}

// Folding from the gutter deletes every row between the reader and whatever
// opened them, so the scroller clamps and leaves them wherever the shortened
// document happens to end. Put them back on the row that now stands for
// everything that disappeared — the strip, or the turn's own prompt card.
// Where the reader lands after a gutter fold. Only the FOLDING actions have an
// entry: opening grows the page below the control, so there is nothing to
// re-anchor. Folding deletes every row between the reader and whatever opened
// them, and the scroller then clamps and leaves them wherever the shortened
// document happens to end — so put them on the row that now stands for what
// disappeared.
const GUTTER_ANCHOR = {
  turn: (refs, turnId) => refs.prompts.get(turnId),
}

// The button's `v-if` is this same `gutterAction`, but a click already in
// flight outlives it: a poll that turns the turn live, or a merge that drops
// the entry, recomputes the action to ''. Bail rather than fold a turn the
// reader never asked to fold.
async function onGutterFold(entry) {
  const turnId = entry.prompt.span_id
  const what = detail.gutterAction(turnId)
  if (!what) return
  toggleTurnExpanded(turnId)
  const anchor = GUTTER_ANCHOR[what]
  if (!anchor) return
  await nextTick()
  revealRow(anchor({ prompts: promptRefs.value }, turnId))
}

// ── Hydrating what the mode put on screen ─────────────────────
// A turn the MODE opened never went through `togglePromptExpanded`, so nothing
// asked the server for its subtree or for the bodies the shallow `/map`
// projection serves lazily — and Focus promises the newest span "with its full
// body intact". Without this, every turn but the latest renders an empty card
// (or, with no subtree at all, no spine).
//
// Both re-run on every span change on purpose: the subtree lands a poll AFTER
// the request, and the bodies inside it can only be asked for once it has.
// The repeat is free — `requestedContent` here and `subtreeLoaded` in
// useTraceData both short-circuit an id that was already asked for.
watch([detail.forcedOpenTurnIds, () => props.spans], ([ids]) => {
  // ONE request for the whole page: asked per turn this was a deep fetch each,
  // 50 of them on a full page, and each one re-merged the entire session.
  emit('load-subtrees', ids)
  for (const id of ids) fetchMissingContentForPrompt(id)
}, { immediate: true })

// The carried span is on screen while its OWN turn is closed, so nothing else
// will ever fetch its body: an `assistant_response` tail — the common case —
// would render an empty card, and C2 is "rendered, not summarised".
watch([detail.ownTailByTurn, () => props.spans], () => {
  for (const span of detail.ownTailByTurn.value.values()) maybeFetchContent(span)
}, { immediate: true })

const chipDescendants = (entry) => chipSpans(entry.descendants, detail.mode.value)

// Same reason as `liveNode` below: a carried span is rendered twice — above a
// prompt, and under the LAST turn's chips — and four separate prop bindings per
// site put this template over its directive budget.
const carriedCtx = computed(() => ({
  selectedSpan: props.selectedSpan,
  folding,
  agentMerge,
  workflowRunsById: props.workflowRunsById,
}))

// Bundled so each consumer costs ONE binding: this template already sits a
// few directives under its budget.
const liveNode = computed(() => ({
  bridgeSession: props.followUp.bridgeSession,
  screen: props.followUp.liveScreen,
}))

const liveShells = computed(() => ({
  traceId: props.traceId,
  shells: props.backgroundShells,
}))

// Everything the prompt card shows that only the span tree knows. No numeral
// here describes spans the mode set aside: an exact tally reads as an error
// budget and invites counting instead of reading. Whether anything IS set
// aside is a different question, and the reader needs the answer to it.
function promptMeta(entry) {
  return {
    ...detail.turnMeta(entry.prompt.span_id),
    turnNumber: turnNumber(entry.prompt),
    eventCount: eventCount(entry),
    pinnable: isPinnable(entry.prompt),
  }
}

// The header's folded count is a button: it opens every fold in the turn.
// Tier 2 bodies stay closed — this reveals what was set aside, it does not
// expand everything that could be expanded. It TOGGLES, because the label
// already does: once open the card relabels itself "N revealed", and a
// non-toggling handler left that as a focusable, hoverable, underlined no-op
// whose only undo was scrolling down to the strip.
function revealFolds(entry) {
  const turnId = entry.prompt.span_id
  const kind = latestOn.value ? 'latest' : 'fold'
  if (detail.isFoldOpen(turnId, kind)) { detail.toggleFold(turnId, kind); return }
  togglePromptExpanded(turnId, true)
  openFold(turnId, kind)
}
</script>

<template>
  <div ref="feedRootEl" class="flex gap-4 items-start" :class="{ 'conv-bounded': ownsScroller }">
    <!-- ──────── LEFT RAIL: TURNS / PHASES TOC ──────── -->
    <ConversationToc
      v-if="!scopeAgent && !hideToc"
      :is-workflow="isWorkflow"
      :has-phase-spans="hasPhaseSpans"
      :phase-items="phaseItems"
      :phase-plan="phasePlan"
      :turn-items="turnItems"
      :selected-span="selectedSpan"
      :context-window-tokens="contextWindowTokens"
      :expanded-prompt-ids="expandedPromptIds"
      @select-turn="selectTurn"
      @select-workflow-row="selectWorkflowRow"
      @jump-live="$emit('jump-live')"
    />

    <!-- ──────── CENTER COLUMN: CHAT DOCUMENT ──────── -->
    <div class="conversation-feed flex-1 min-w-0 font-sans text-sm leading-relaxed">
      <div
        class="conversation-scroll space-y-[26px]"
        :data-trace-scroller="ownsScroller ? '' : undefined"
      >
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
              <ConversationPromptCard
                :prompt="entry.prompt"
                :meta="promptMeta(entry)"
                :folding="folding"
                :trace-id="traceId"
                :selected="!!selectedSpan && selectedSpan.span_id === entry.prompt.span_id"
                :pinned="pinnedSpanId === entry.prompt.span_id"
                :session-ended="sessionEnded"
                @click="onPromptClick(entry.prompt)"
                @toggle-pin="togglePin(entry.prompt.span_id)"
                @present="onPresent(entry.prompt)"
                @reveal-folds="revealFolds(entry)"
              />

              <!-- Tool/skill chips for this turn (collapsed preview), with
                   the gutter's other half: the same round control that folds a
                   turn opens it again, from the same column. -->
              <div v-if="!detail.isTurnExpanded(entry.prompt.span_id)">
                <SpineFoldToggle
                  v-if="renderableFor(entry).length"
                  action="expand"
                  @toggle="toggleTurnExpanded(entry.prompt.span_id)"
                />
                <TurnToolChips
                  :descendants="chipDescendants(entry)"
                  @expand="toggleTurnExpanded(entry.prompt.span_id)"
                />
                <!-- What this turn produced, kept visible while it is folded.
                     Keyed by the turn that produced it, so it does not depend
                     on there being a next turn to hang it above. -->
                <CarriedSpan
                  v-if="ownTailFor(entry)"
                  :span="ownTailFor(entry)"
                  v-bind="carriedCtx"
                  @activate="onActivate"
                  @enter-scope="$emit('enter-scope', $event)"
                />
              </div>

              <!-- Expanded turn body: one spine, every event a node punched onto
                   it. The `:ref` wrapper + pin button stay here;
                   ConversationSpanCard renders only the card body. -->
              <div
                v-if="detail.isTurnExpanded(entry.prompt.span_id) && renderableFor(entry).length"
                class="event-spine"
              >
                <!-- Rides the turn's rail so folding it back up is reachable
                     from anywhere in the run, not only from its top.
                     Zero-height and in the gutter: it displaces no row and
                     covers no card. -->
                <SpineFoldToggle
                  v-if="detail.gutterAction(entry.prompt.span_id)"
                  :action="detail.gutterAction(entry.prompt.span_id)"
                  @toggle="onGutterFold(entry)"
                />
                <template v-for="row in focusRowsFor(entry)" :key="row.strip ? `${row.kind}-${row.turnId}` : row.span.span_id">
                  <!-- Tier 3 anchor: sits where the turn's first folded span
                       occurred, so expanding restores session order. -->
                  <div
                    v-if="row.strip"
                    :ref="(el) => registerStrip(row, el)"
                    class="event-spine-row mb-2"
                  >
                    <FoldStrip
                      :spans="row.foldedSpans"
                      :variant="row.kind"
                      :expanded="detail.isFoldOpen(row.turnId, row.kind)"
                      @toggle="detail.toggleFold(row.turnId, row.kind)"
                    />
                  </div>
                  <div
                    v-else
                    :ref="(el) => { if (el) spanRefs.set(row.span.span_id, el); else spanRefs.delete(row.span.span_id) }"
                    :data-span-id="row.span.span_id"
                    :data-testid="row.inAgent ? 'in-agent-row' : undefined"
                    :data-tier="row.tier"
                    class="event-spine-row group/pin"
                    :data-run-state="row.runState || undefined"
                    :class="[
                      row.inAgent ? 'spine-nested border-l-2 border-violet-200 bg-violet-50/20' : '',
                      row.agentSep ? 'border-t-2 border-slate-200 mt-6 pt-5' : '',
                      pinnedSpanId === row.span.span_id ? 'rounded-md ring-2 ring-amber-400' : '',
                      row.runState === 'running' || row.runState === 'waiting' ? 'spine-row-run' : '',
                    ]"
                  >
                    <ConversationSpineCard
                      :row="row"
                      :ctx="spineCtx"
                      @activate="onActivate"
                      @present="onPresent"
                      @toggle-body="toggleManuallyOpen"
                      @toggle-pin="togglePin"
                      @enter-scope="$emit('enter-scope', $event)"
                    />
                  </div>
                </template>
              </div>

              <!-- Turn footer: usage rollup + the collapse trigger, one filled bar -->
              <TurnUsageFooter
                v-if="turnItemFor(entry.prompt)?.turnAgg || detail.isTurnExpanded(entry.prompt.span_id)"
                :key="entry.prompt.span_id"
                :item="turnItemFor(entry.prompt)"
                :context-window-tokens="contextWindowTokens"
                :detail-open="detail.isTurnExpanded(entry.prompt.span_id)"
                @toggle-detail="toggleTurnExpanded(entry.prompt.span_id)"
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
            >
              <CompactionDivider
                :span="entry.span"
                :folding="folding"
                :selected="!!selectedSpan && selectedSpan.span_id === entry.span.span_id"
                @select="onSelectSpan(entry.span)"
              />
            </div>

            <!-- Standalone root (non-prompt) -->
            <div
              v-else
              :ref="(el) => { if (el) standaloneRefs.set(entry.span.span_id, el); else standaloneRefs.delete(entry.span.span_id) }"
              class="relative group/pin flex items-center gap-2 text-xs text-slate-500 px-2.5 py-1 rounded-lg border border-transparent cursor-pointer hover:bg-slate-50"
              :class="[
                selectedSpan && selectedSpan.span_id === entry.span.span_id ? 'event-selected' : '',
                pinnedSpanId === entry.span.span_id ? 'ring-2 ring-amber-400' : '',
              ]"
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

          <!-- Anchored to the SESSION, after every turn: a last prompt with no
               children renders no spine, and the way to the live terminal must
               not go down with it. -->
          <!-- Above the status row: a running shell is work in progress, and
               the row below it is the session's own state. -->
          <BackgroundShellsBlock
            :live="liveShells" :server-now="serverNow" :server-now-at="serverNowAt"
          />

          <SpineLiveNode :live="liveNode" />

          <!-- Terminator: the feed has a bottom, so a reader who reaches it knows
               there is nothing further to load rather than guessing. -->
          <div
            v-if="entries.length"
            class="text-center pt-3.5 pb-[30px] font-mono text-[11px] uppercase tracking-[0.1em] text-slate-400"
          >End of timeline</div>

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
           surfaces a count of spans that arrived since. Bounded, its rail is a
           zero-height row between the transcript and the composer, so the pill
           hangs over the transcript's last line without reserving space. -->
      <div class="follow-pill-rail">
        <button
          v-if="showFollowTail && (followTail || !atBottom)"
          type="button"
          class="follow-pill fixed bottom-[calc(1.5rem+max(var(--trace-rail-h,0px),var(--trace-dock-h,0px),var(--blocker-bar-h,0px)))] left-1/2 -translate-x-1/2 z-20 inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-[12px] font-medium shadow-lg transition-colors focus-visible:outline-2 focus-visible:outline-orange-400"
          :class="followTail
            ? 'bg-orange-500 border-orange-600 text-white hover:bg-orange-600'
            : 'bg-white border-orange-300 text-orange-700 hover:bg-orange-50'"
          :title="followTail ? 'Stop following the latest activity' : 'Jump to and follow the latest activity'"
          @click="onFollowPill"
        >
          <span aria-hidden="true">⤓</span>
          <span>{{ followTail ? 'Following' : 'Follow latest' }}</span>
          <span
            v-if="!followTail && newSinceScroll > 0"
            class="inline-flex items-center justify-center min-w-[1rem] h-4 px-1 rounded-full bg-orange-500 text-white text-[10px] tabular-nums"
          >{{ newSinceScroll > 99 ? '99+' : newSinceScroll }}</span>
        </button>
      </div>

      <!-- Below the transcript's scroll region, never inside it: at any scroll
           position there is no row for the composer to cover. -->
      <ConversationFooter
        :session-id="traceId"
        :turn-number="turnItems.length + 1"
        :context="followUp"
        @sent="armSendReveal"
        @resumed="emit('resumed')"
      />
    </div>
  </div>
</template>

<style scoped>
/* Floor for the chat column so the rail + detail rail can't squeeze it into a
   one-word-per-line strip. Below 1120px the rail is in its mini form, which
   buys the column back the difference. */
.conversation-feed { min-width: 300px; }

@media (max-width: 1119px) {
  .conversation-feed { min-width: 236px; }
}

/* Bounded layout: the column is handed a height, the transcript scrolls inside
   `.conversation-scroll`, and the composer is the flex sibling under it. Off
   this path every rule here is inert and the app shell scrolls the page.
   `min-height: 0` on each link or the scroll box just grows to its content. */
.conv-bounded { align-items: stretch; min-height: 0; }
.conv-bounded .conversation-feed { display: flex; flex-direction: column; min-height: 0; }
.conv-bounded .conversation-scroll {
  flex: 1;
  min-height: 6rem;
  overflow-y: auto;
  overscroll-behavior: contain;
  scrollbar-gutter: stable;
  /* The pin buttons hang 1rem into the gutter left of the cards; a scroll box
     ending at the column edge would clip them away. */
  margin-left: -1rem;
  padding-left: 1rem;
}

/* Anchored to the transcript's bottom edge rather than the viewport, so it
   rides above the composer without either measuring the other. */
.conv-bounded .follow-pill-rail { position: relative; height: 0; }
.conv-bounded .follow-pill { position: absolute; bottom: 0.75rem; }

/* The dock's own `flex-shrink: 0` (its default context has no bounded
   ancestor to shrink against) plus its content's automatic min-size meant a
   plain flex sibling here could never give up height — so a short viewport
   with the full header expanded handed the whole deficit to
   `.conversation-scroll` above instead, collapsing the transcript to 0px
   (defect #212). Letting it shrink, with `overflow-y: auto` (the standard
   flex trick that drops an item's automatic min-size to 0 once its overflow
   isn't `visible`), means that once the transcript has claimed its floor the
   composer is what gives — scrolling its own chrome internally rather than
   pushing the transcript out or overlapping whatever renders after it. */
.conv-bounded .conversation-feed :deep(.followup-dock) {
  flex-shrink: 1;
  min-height: 3rem;
  overflow-y: auto;
}
</style>
