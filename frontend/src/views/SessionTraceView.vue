<script setup>
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import api from '../api'
import Card from '../components/Card.vue'
import Button from '../components/ui/Button.vue'
import SessionMessagesView from '../components/SessionMessagesView.vue'
import SessionTerminalLog from '../components/SessionTerminalLog.vue'
import TraceConversationRegion from '../components/TraceConversationRegion.vue'
import SuppressButton from '../components/triggers/SuppressButton.vue'
import { dropRetiredSpans } from '../utils/traceFormatters.js'
import { useTraceScroll } from '../composables/useTraceScroll.js'
import { useStickyHeader, useStickyChromeHeight } from '../composables/useStickyHeader.js'
import { useViewMode } from '../composables/useViewMode.js'
import { useFilterState } from '../composables/useFilterState.js'
import { useRuleTriggers } from '../composables/useRuleTriggers.js'
import { useTraceTimeline } from '../composables/useTraceTimeline.js'
import { useCompactWatch } from '../composables/useCompactWatch.js'
import { useSpanContentCache } from '../composables/useSpanContentCache.js'
import { useSpanSheet } from '../composables/useSpanSheet.js'
import { useToolRollup } from '../composables/useToolRollup.js'
import { useWorkflowMeta } from '../composables/useWorkflowMeta.js'
import { useTraceData } from '../composables/useTraceData.js'
import { useTurns } from '../composables/useTurns.js'
import { useLiveAgents } from '../composables/useLiveAgents.js'
import { useTraceScope } from '../composables/useTraceScope.js'
import { useBreakpoint } from '../composables/useBreakpoint.js'
import TraceScopeBar from '../components/TraceScopeBar.vue'
import TraceAgentPane from '../components/TraceAgentPane.vue'
import TraceAgentsPopover from '../components/TraceAgentsPopover.vue'
import { scrollSpanRowIntoView } from '../utils/scrollSpanRow.js'
import ToolTokenRollup from '../components/ToolTokenRollup.vue'
import Icon from '../components/ui/Icon.vue'
import SessionTraceHeader from '../components/SessionTraceHeader.vue'
import TraceOverviewStrip from '../components/TraceOverviewStrip.vue'
import SpanDetailPanel from '../components/SpanDetailPanel.vue'
import { findNodeBySpanId, findNodePath, findNodeKey } from '../utils/spanTree.js'
import SessionTurnsSidebar from '../components/SessionTurnsSidebar.vue'
import SessionTimelineTree from '../components/SessionTimelineTree.vue'

const route = useRoute()
const router = useRouter()
const session = ref(null)
const loading = ref(true)
const reloading = ref(false)
// True while auto-reload (the live poll AND the scroll/wheel pull-to-refresh)
// is still wanted. Flips false the moment live-sync self-terminates — an
// already-closed session after its bounded catch-up, or a live session that
// ends mid-view once its tail converges. The scroll/wheel affordances in
// useTraceScroll read this so scrolling to the end of a closed session stops
// firing reloadLiveTail() (and its backend transcript rescan). The explicit
// header reload button and scroll-up load-older deliberately ignore it.
const liveSyncActive = ref(true)
const lastReloadedAt = ref(null)
const selectedSpan = ref(null)
// Trigger map for the currently-selected rule.check span, plus the role gate
// for the suppress UI. Refetches on selection change (watch lives inside the
// composable) and after every suppress/unsuppress (call loadTriggersForSelectedSpan).
const { ruleTriggersByRuleId, canSuppressRule, loadTriggersForSelectedSpan } =
  useRuleTriggers(selectedSpan)

const expandedKeys = ref({})
const selectedKeys = ref({})   // PrimeVue TreeTable v-model:selection-keys

// Breakpoint flags drive structural (component-level) switches: the agent
// scope's companion pane vs takeover (≥xl), and the span-detail rail vs
// mobile bottom sheet (lg). See useBreakpoint / the redesign artifact.
const { isLgUp, isXl, is2xl } = useBreakpoint()

// Sticky page header: everything that frames the trace (title row, tokens
// rollup, mini-timeline, more-history banner) pins to the top of the scroll
// container so the user keeps navigation context while scrolling a long
// span list. Sidebar's sticky offset must match this height, so we measure
// the rendered header with a ResizeObserver and expose it as a CSS var.
// Re-measures on mount + whenever `loading` flips falsy (the v-else branch
// renders the sticky element only after session data lands).
//
// Below lg the full header is too tall to pin (it ate over half a phone
// viewport), so it scrolls away and only a compact strip — title line +
// view-mode switcher — stays sticky. Each sticky element gets its own
// measured height; `stickyChromeHeight` is whichever one is pinned at the
// current breakpoint, and drives every dependent offset (thead, sidebar,
// conversation rails).
const { stickyHeaderEl, stickyHeaderHeight } = useStickyHeader(loading)
const { stickyHeaderEl: compactBarEl, stickyHeaderHeight: compactBarHeight } =
  useStickyHeader(loading)
const stickyChromeHeight = useStickyChromeHeight(isLgUp, stickyHeaderHeight, compactBarHeight)

const MODE_OPTIONS = [
  { id: 'conversation', label: 'Conversation' },
  { id: 'timeline', label: 'Timeline' },
  { id: 'terminal', label: 'Terminal' },
  { id: 'messages', label: 'Messages' },
]

// On-demand span content cache; `allSpans` overlays it onto session.spans so
// every consumer reads one merged list (see useSpanContentCache).
const { spanContentCache, allSpans, fetchSpanContent } =
  useSpanContentCache(session, route)

// Trace data core: owns treeNodes + pagination + every fetch/merge/reconcile
// primitive, mutating the SFC-owned `session` and `selectedSpan` refs threaded
// in. See useTraceData (the live-tail reconcile is kept atomic there).
const {
  treeNodes,
  hasMoreOlder, loadingOlder,
  // newestLoadedId is the convergence anchor for the self-terminating poll
  // (`maybeStopOnConverge` / `syncClosedSessionTail`). Without it those read an
  // undefined binding and throw, aborting onMounted before the poll/sync (and
  // liveSyncActive) is ever set up.
  newestLoadedId,
  loadSession, reloadLiveTail, loadOlder,
  subtreeLoaded,
  ensureNodeChildrenLoaded, ensureSpanSubtreeLoaded, refreshSpanSubtree,
  ensureTerminalSpansLoaded, ensureWorkflowSpansLoaded,
} = useTraceData(route, { session, allSpans, selectedSpan })

// ≥xl the user chooses how a scope is presented: the 'split' companion pane
// (beside the main feed) or the 'full' only-subagent takeover (the same
// full-width scoped feed the <xl view always uses — one scoped-feed
// implementation, not two). Persisted, so re-entering a scope and deep links
// honor the last choice. Below xl the takeover is the only mode; the toggle is
// inert there.
const scopeMode = useFilterState('regin.traceScope.mode', 'split',
  v => v === 'split' || v === 'full')

// Whole-session subagent roster (server-classified, window-independent) +
// the per-agent Conversation scope. The header keeps showing MAIN-session
// truth; the scope only re-projects the conversation spine. `isTakeover`
// tells the scope's scroll save/restore whether entering/exiting actually
// replaces the feed — in split mode the main feed never moves, so exit must
// not touch page scroll.
const liveAgents = useLiveAgents(() => allSpans.value, () => session.value?.agent_roster)
const traceScope = useTraceScope(route, router, {
  getAgents: () => liveAgents.agents,
  getRoster: () => session.value?.agent_roster,
  ensureSpanSubtreeLoaded,
  ensureTerminalSpansLoaded,
  isTakeover: () => !isXl.value || scopeMode.value === 'full',
})

// View mode: 'conversation' | 'timeline' | 'terminal' | 'messages'.
// Resolution order: `?view=` query param > localStorage > default (see useViewMode).
const { viewMode, setViewMode } = useViewMode(route)

// The conversation tab defaults to the clean centered feed: the right rail
// (span details + turns) stays hidden until explicitly opened, so selecting
// a span doesn't squeeze the feed. Timeline/terminal keep the rail whenever
// a span is selected. Persisted so the choice survives navigation.
const detailRailOpen = useFilterState('regin.trace.detailRail', false,
  v => typeof v === 'boolean')

// Render the scoped feed full-width (takeover) when a scope is active AND
// either the viewport is below the split floor OR the user picked 'full'. Not
// while the roster picker is open (that always fills the pane; the roster can
// only open in split mode — full mode keeps the popover picker, see the
// TraceAgentsPopover pane-mode bind).
const scopeTakeover = computed(() => viewMode.value === 'conversation'
  && !!traceScope.scopeId
  && !traceScope.rosterOpen
  && (!isXl.value || scopeMode.value === 'full'))

// The span-detail rail (opt-in) and the agent pane both want the right edge.
// ≥2xl: feed + pane + rail coexist as three columns. At xl the right slot is
// shared and the rail wins when invoked — the pane yields (restored by
// closing the rail, since the scope state is untouched).
const detailRailShown = computed(() => !!selectedSpan.value
  && viewMode.value !== 'messages'
  && (viewMode.value !== 'conversation' || detailRailOpen.value))
const paneVisible = computed(() => {
  if (viewMode.value !== 'conversation' || !isXl.value) return false
  if (!traceScope.active) return false
  if (detailRailShown.value && !is2xl.value) return false
  // The roster picker always fills the pane; a scope only does so in split mode.
  if (traceScope.rosterOpen) return true
  return scopeMode.value === 'split'
})

// Switching to 'full' remembers the main-feed scroll; collapsing back to
// 'split' restores it, so maximizing and returning doesn't lose the reader's
// place in the main thread.
let savedMainScroll = null
function getScroller() {
  return document.querySelector('.content-scroll')
    || document.scrollingElement || document.documentElement
}
function setScopeMode(mode) {
  const scroller = getScroller()
  if (mode === 'full') {
    savedMainScroll = scroller ? scroller.scrollTop : null
  }
  scopeMode.value = mode
  if (mode === 'split' && savedMainScroll != null) {
    const top = savedMainScroll
    savedMainScroll = null
    nextTick(() => { if (scroller) scroller.scrollTop = top })
  }
}
// The saved offset is only meaningful for the scope it was captured under —
// exiting must drop it, or a later scope that lands directly in 'full' (the
// persisted mode) would "restore" a stale offset on collapse.
watch(() => traceScope.scopeId, (v) => { if (!v) savedMainScroll = null })

// Agents button → pane roster (≥xl split). Below 2xl the rail and the pane
// share the right slot, and the rail normally wins — but an explicit ask for
// the roster is user intent to SEE it, so it closes the rail rather than
// arming an invisible rosterOpen that would pop up whenever the rail is
// later dismissed.
function openAgentsRoster() {
  if (!is2xl.value) detailRailOpen.value = false
  traceScope.openRoster()
}

// Shared span/turn inputs the conversation feed + companion pane both consume,
// bundled so the region tag doesn't re-spell a dozen binds (keeps this host's
// template-directive budget in check).
const feedProps = computed(() => ({
  spans: allSpans.value,
  turns: turns.value,
  selectedSpan: selectedSpan.value,
  traceId: session.value?.trace_id,
  contextWindowTokens: session.value?.context_window_tokens,
  workflowRunsById: workflowRunsById.value,
  loadedSubtrees: subtreeLoaded.value,
  serverNow: session.value?.server_now || '',
  serverNowAt: session.value?.server_now_at || 0,
}))

const { sheetOpen, selectSpan } = useSpanSheet(selectedSpan, isLgUp, route.query.span)

// Prop bundle shared by the desktop rail and the mobile-sheet renderings of
// the span detail panel (keeps the template's directive budget in check).
const spanDetailProps = computed(() => ({
  selectedSpan: selectedSpan.value,
  ruleTriggersByRuleId: ruleTriggersByRuleId.value,
  canSuppressRule: canSuppressRule.value,
  workflowRunsById: workflowRunsById.value,
}))

// send_to_user messages (Messages tab). Null until first load so the tab
// can distinguish "not fetched yet" from "fetched, empty". Refreshed by
// reload() while the tab is active, so the live poll keeps it current.
const agentMessages = ref(null)
const sessionGoal = ref(null)
async function ensureAgentMessagesLoaded() {
  const data = await api.get(`/sessions/${route.params.id}/agent-messages`)
  agentMessages.value = data.messages
  sessionGoal.value = data.session_goal
}

// Jump from a send_to_user span (right rail) to its rendered card in the
// Messages tab. The span and the agent message share a span_id, which anchors
// each <li>. Briefly highlight the target so it's findable after the scroll.
const highlightedMessageSpan = ref(null)
async function goToMessage(span) {
  if (!span?.span_id) return
  setViewMode('messages')
  await ensureAgentMessagesLoaded()
  await nextTick()
  const el = document.getElementById(`msg-${span.span_id}`)
  if (!el) return
  el.scrollIntoView({ behavior: 'smooth', block: 'center' })
  highlightedMessageSpan.value = span.span_id
  setTimeout(() => {
    if (highlightedMessageSpan.value === span.span_id) highlightedMessageSpan.value = null
  }, 2400)
}

// Header pivot metadata: plans this session authored, workflow runs it
// launched, and (when this session IS a run) its stale-snapshot marker +
// launching-session backlink. See useWorkflowMeta.
const {
  plans, workflowRuns, workflowRunsById,
  snapshotStaleAt, workflowParentTo,
  fetchPlans, fetchWorkflowRuns,
} = useWorkflowMeta(route, allSpans)

// Per-session tool/token rollup (server-side aggregate). See useToolRollup.
const { toolRollupData, fetchToolRollup } = useToolRollup(route)

// Scroll/wheel/touch-driven auto-reload (pull-to-refresh at the bottom,
// pull-older at the top). The composable owns the DOM mechanics + latches and
// attaches its own document listeners on mount; we hand it the loader
// callbacks and the gating refs it reads.
useTraceScroll({ reloading, loading, loadingOlder, hasMoreOlder, liveSyncActive, reload, loadOlder })

// General live poll. The trace view is a live dashboard but `reload()`
// otherwise only fires on scroll/wheel — so a user parked at the bottom
// watching their session never sees updates (and any transient duplicate
// from a placeholder→anchor handoff never gets reconciled away) until they
// scroll. A lightweight visibility-gated tick keeps the reconcile
// (`reloadLiveTail`) converging the tail to the DB every few seconds.
//
// The poll is self-terminating: it consumes resources (a /map fetch + a
// backend transcript rescan) every tick, which is pure waste once a session
// has ended and its tail has stopped growing. So we stop polling once the
// session is closed (`ended_at` set) AND the tail has converged — see
// `maybeStopOnConverge`. An already-ended session never starts the recurring
// poll at all; it runs one bounded catch-up instead (`syncClosedSessionTail`),
// which is also the crash-recovery path (reopening the view re-runs it).
let livePollTimer = null
const LIVE_POLL_MS = 4000
// Max reconciles the bounded catch-up will run before giving up on an
// ever-advancing tail (a still-live session mislabelled ended, say).
const CLOSED_SYNC_MAX_TICKS = 3
// newest DB id observed at the previous reconcile, for the convergence test.
let convergeAnchorId = null
function startLivePoll() {
  if (livePollTimer) return
  livePollTimer = setInterval(() => {
    if (document.hidden) return
    if (reloading.value || loading.value || loadingOlder.value) return
    reload()
  }, LIVE_POLL_MS)
}
function stopLivePoll() {
  if (livePollTimer) { clearInterval(livePollTimer); livePollTimer = null }
}

// Called after every reconcile. While the session is live, keep polling. Once
// it has ended, stop — but only after the tail stops advancing, so the final
// SessionEnd flush is captured first. A count/items divergence (the marker
// says ended while spans are still landing) must NOT stop us on the first
// `ended_at`; we require one unchanged-newest tick.
function maybeStopOnConverge() {
  if (!livePollTimer) return
  if (!session.value?.ended_at) { convergeAnchorId = null; return }
  if (convergeAnchorId !== null && newestLoadedId.value === convergeAnchorId) {
    stopLivePoll()
    // Tail has converged on a closed session: the scroll/wheel pull-to-refresh
    // would otherwise keep firing reloadLiveTail() (and a backend rescan) every
    // time the user scrolls to the end. Retire it alongside the timer poll.
    liveSyncActive.value = false
    return
  }
  convergeAnchorId = newestLoadedId.value
}

// Crash-recovery / one-shot sync for a session that is already closed when the
// view opens. The hook scan may have missed the last turns before a server
// crash; reconcile only while the tail keeps advancing, capped — then stop. No
// recurring poll: opening (or reloading) the view IS the trigger, so there is
// no button to press.
async function syncClosedSessionTail() {
  let anchor = newestLoadedId.value
  for (let i = 0; i < CLOSED_SYNC_MAX_TICKS; i++) {
    await reload()
    if (newestLoadedId.value === anchor) break
    anchor = newestLoadedId.value
  }
}

onMounted(async () => {
  const rollupP = fetchToolRollup()
  const plansP = fetchPlans()
  const wfRunsP = fetchWorkflowRuns()
  await loadSession()
  loading.value = false
  await Promise.all([rollupP, plansP, wfRunsP])
  // A session that is already closed never needs the perpetual poll: run one
  // bounded catch-up (crash recovery) and stop. Live sessions keep the poll.
  if (session.value?.ended_at) {
    await syncClosedSessionTail()
    // Bounded catch-up done — no recurring poll, and the scroll/wheel
    // pull-to-refresh should not resurrect the backend rescan for a session
    // that has already ended. Reopening the view re-runs the catch-up.
    liveSyncActive.value = false
  } else {
    startLivePoll()
  }
  if (viewMode.value === 'terminal') ensureTerminalSpansLoaded()
  if (viewMode.value === 'messages') ensureAgentMessagesLoaded()
  // Scroll/wheel/touch auto-reload listeners are attached by useTraceScroll();
  // the sticky-header ResizeObserver is owned by useStickyHeader.
})

onUnmounted(() => {
  // Scroll/wheel/touch listeners are detached by useTraceScroll(); the
  // sticky-header observer + compact poll are torn down by their composables.
  stopLivePoll()
})

// When the user enters the Terminal tab (or lands on it via localStorage
// restore), fetch every span — not the shallow root-only set the other
// tabs use. Conversation tab also needs turns to render the right-rail
// timeline, so trigger that load here too.
watch(viewMode, async (mode) => {
  if (mode === 'terminal') {
    await ensureTerminalSpansLoaded()
  } else if (mode === 'messages') {
    await ensureAgentMessagesLoaded()
  } else if (mode === 'conversation') {
    if (turns.value == null && !turnsLoading.value) {
      turnsLoading.value = true
      try { await fetchTurns() } finally { turnsLoading.value = false }
    }
  }
})

async function reload() {
  if (reloading.value) return
  reloading.value = true
  try {
    const tasks = [reloadLiveTail(), fetchToolRollup()]
    // A RUNNING scoped agent keeps growing spans under its start marker —
    // the trailing-roots refresh misses agents anchored under older prompts.
    if (traceScope.scopedAgent?.running && traceScope.startSpanId) {
      tasks.push(refreshSpanSubtree(traceScope.startSpanId))
    }
    // Messages tab rides the same live poll: cheap per-session query.
    if (viewMode.value === 'messages') tasks.push(ensureAgentMessagesLoaded())
    // Only refetch turns if they're loaded AND visible. While folded the
    // user isn't reading them, so defer the cost; mark stale and let the
    // unfold action pull the fresh copy in.
    if (turns.value != null && !turnsCollapsed.value) {
      tasks.push(fetchTurns())
    } else if (turns.value != null && turnsCollapsed.value) {
      turnsStale.value = true
    }
    await Promise.all(tasks)
    lastReloadedAt.value = new Date()
    maybeStopOnConverge()
  } finally {
    reloading.value = false
  }
}

function latestSpanByTime(spans) {
  if (!spans?.length) return null
  return [...spans].sort((a, b) => {
    const at = a.start_time ? new Date(a.start_time).getTime() : 0
    const bt = b.start_time ? new Date(b.start_time).getTime() : 0
    if (at !== bt) return at - bt
    const aid = a.id || 0
    const bid = b.id || 0
    return aid - bid
  })[spans.length - 1] || null
}

async function jumpToLatestSpan() {
  setViewMode('conversation')
  await nextTick()
  await reloadLiveTail()
  const latest = latestSpanByTime(allSpans.value)
  if (latest) {
    if (selectedSpan.value?.span_id === latest.span_id) {
      selectedSpan.value = null
      await nextTick()
    }
    selectedSpan.value = latest
    if (!spanContentCache.value.has(latest.span_id) && latest.attributes && !Object.keys(latest.attributes).length) {
      fetchSpanContent(latest.span_id)
    }
  }
}

// Drive `compact.pre → compact.post` polling off the live span set.
useCompactWatch(allSpans, reload, { reloading, loading })

async function onNodeExpand(event) {
  const spanId = event?.node?.data?.span_id
  if (!spanId) return
  const nodeKey = event?.node?.key
  if (nodeKey) {
    expandedKeys.value = { ...expandedKeys.value, [nodeKey]: true }
  }
  await ensureNodeChildrenLoaded(spanId)
  if (nodeKey) {
    expandedKeys.value = { ...expandedKeys.value, [nodeKey]: true }
  }
}

async function toggleTimelineNode(node) {
  if (!node?.key || !node?.data?.span_id || node.leaf) return
  if (expandedKeys.value[node.key]) {
    const next = { ...expandedKeys.value }
    delete next[node.key]
    expandedKeys.value = next
    return
  }
  expandedKeys.value = { ...expandedKeys.value, [node.key]: true }
  await ensureNodeChildrenLoaded(node.data.span_id)
}

async function onOverviewSpanClick(node) {
  if (!node?.data?.span_id) return
  const spanId = node.data.span_id
  selectSpan(allSpans.value.find(s => s.span_id === spanId) || node.data)
  if (!node.leaf) {
    expandedKeys.value = { ...expandedKeys.value, [node.key]: true }
    await ensureNodeChildrenLoaded(spanId)
  }
  // Timeline + terminal rows live inside data-span-id-marked tables, so
  // the existing poll-and-scroll helper finds them. Conversation view
  // tracks DOM refs via promptRefs and scrolls itself off its own
  // selectedSpan watcher — calling scrollSpanRowIntoView there would
  // just spin out 20 polling attempts that never match anything.
  if (viewMode.value !== 'conversation') {
    await nextTick()
    scrollSpanRowIntoView(spanId)
  }
}

// Session-level timeline bounds (DB-anchored with a live edge) + active-work
// aggregate — see useTraceTimeline.
const { traceStart, traceEnd, traceDuration, activeWorkMs } =
  useTraceTimeline(session, allSpans)

// Select + scroll to a span by id, loading its (possibly collapsed)
// subtree first. Shared by the task-list jump and the tool-drill-down
// jump. If the span isn't in the loaded shallow set, walk the roots
// calling `ensureSpanSubtreeLoaded` until it materialises; the existing
// `selectedSpan` watcher then does the scroll-and-highlight.
async function selectSpanById(spanId) {
  if (!spanId) return
  setViewMode('conversation')
  let span = allSpans.value.find(s => s.span_id === spanId)
  if (!span) {
    for (const node of treeNodes.value) {
      if (node?.data?.span_id) {
        // eslint-disable-next-line no-await-in-loop
        await ensureSpanSubtreeLoaded(node.data.span_id)
        span = allSpans.value.find(s => s.span_id === spanId)
        if (span) break
      }
    }
  }
  if (span) selectSpan(span)
}

// Jump from a row in the expanded task list to the most relevant span for
// that task's current state: pending → TaskCreate, in_progress / completed
// → the TaskUpdate that flipped it. Backend pre-computes `current_span_id`;
// fall back to `created_span_id` for pending.
function jumpToTaskSpan(task) {
  return selectSpanById(task?.current_span_id || task?.created_span_id)
}

async function onNodeSelect(event) {
  const nodeData = event?.node?.data || event?.data
  if (!nodeData?.span_id) return
  const full = allSpans.value.find(s => s.span_id === nodeData.span_id)
  selectSpan(full || nodeData)

  const selectedNode = event?.node || findNodeBySpanId(treeNodes.value, nodeData.span_id)
  if (selectedNode && !selectedNode.leaf) {
    expandedKeys.value = { ...expandedKeys.value, [selectedNode.key]: true }
    await ensureNodeChildrenLoaded(nodeData.span_id)
  }
}

// Recursive lookup: `treeNodes` is the client-built hierarchy of root
// plus lazily-loaded children — find the node key for a span_id so
// we can drive PrimeVue's selection/expansion from a raw span ref.
// Keep the PrimeVue TreeTable's internal selection state in sync
// with the Vue-side `selectedSpan`. Without this the `.p-highlight`
// row decoration only fires when the user clicks the tree directly
// — clicking a strip bar, a turn row, or a drill-down ref would
// otherwise never light up the corresponding row.
watch(selectedSpan, async (span) => {
  if (!span) {
    selectedKeys.value = {}
    return
  }
  // Fetch content on-demand if this span's attributes aren't cached yet.
  if (!spanContentCache.value.has(span.span_id)) {
    await fetchSpanContent(span.span_id)
    // Re-bind selectedSpan to the fresh object from allSpans so the
    // details panel sees the newly-loaded attributes.
    const fresh = allSpans.value.find(s => s.span_id === span.span_id)
    if (fresh && fresh !== span) {
      selectedSpan.value = fresh
      return // watcher will fire again with the fresh object
    }
  }
  const key = findNodeKey(treeNodes.value, span.span_id)
  selectedKeys.value = key ? { [key]: true } : {}
})

// Turn-usage sidebar + the bidirectional turn⇄span cross-highlight. Called
// AFTER the selection watcher above on purpose: useTurns registers the
// span→turn watcher, which must fire *after* the content-fetch + selectedKeys
// sync so the row highlight lands before the turn scroll.
const {
  turns, turnsLoading, turnsCollapsed, turnsStale,
  selectedTurnUuid, expandedTurnUuid, maxTurnConsumption,
  spanIdsInSelectedTurn,
  fetchTurns, loadTurns, toggleTurnsCollapsed,
  selectTurn, toggleTurnExpanded, storeTurnRow, handleSpanRefClick,
} = useTurns(route, {
  allSpans, treeNodes, selectedSpan, selectedKeys, expandedKeys,
  ensureSpanSubtreeLoaded,
})

</script>

<template>
  <div v-if="loading" class="empty-state">Loading session…</div>
  <div v-else-if="!session || !allSpans.length" class="empty-state">
    No spans found for this session.
  </div>
  <div
    v-else
    class="trace-detail-root"
    :style="{ '--regin-trace-header-h': stickyChromeHeight ? stickyChromeHeight + 'px' : '0px' }"
  >
    <!-- Sticky page header: title row, tokens rollup, mini-timeline and
         the "more history" affordance pin to the top of `.content-scroll`
         so the user keeps session context while scrolling a long span
         list. Negative margins match `.content-scroll`'s padding (mobile
         1rem, desktop 1.5rem top / 2rem sides) so the white background
         goes edge-to-edge of the content card without overshooting. The
         rendered height is measured by a ResizeObserver and propagated
         to the sidebar's sticky offset. -->
    <div
      ref="stickyHeaderEl"
      class="lg:sticky lg:-top-6 z-20 bg-white -mx-4 -mt-4 px-4 pt-4 lg:-mx-8 lg:-mt-6 lg:px-8 lg:pt-6 pb-4 mb-4 border-b border-slate-200 shadow-[0_2px_4px_-2px_rgba(15,23,42,0.06)]"
    >
    <SessionTraceHeader
      :key="session?.trace_id"
      :session="session"
      :plans="plans"
      :workflow-runs="workflowRuns"
      :view-mode="viewMode"
      :reloading="reloading"
      :loading="loading"
      :last-reloaded-at="lastReloadedAt"
      :has-turns="turns != null"
      :trace-duration="traceDuration"
      :active-work-ms="activeWorkMs"
      :snapshot-stale-at="snapshotStaleAt"
      :workflow-parent-to="workflowParentTo"
      @update:view-mode="setViewMode"
      @reload="reload"
      @jump-to-task="jumpToTaskSpan"
    >
      <template #actions>
        <!-- Pane roster mode only in the ≥xl SPLIT presentation. In 'full'
             mode the popover picker is kept (as below xl): opening the pane
             roster there would silently yank the takeover back to a split
             the user explicitly maximized away from. -->
        <TraceAgentsPopover
          :running-agents="liveAgents.runningAgents"
          :finished-agents="liveAgents.finishedAgents"
          :running-count="liveAgents.runningCount"
          :server-now="session?.server_now || ''"
          :server-now-at="session?.server_now_at || 0"
          :pane-mode="isXl && viewMode === 'conversation' && scopeMode === 'split'"
          @scope="traceScope.enter($event)"
          @open-roster="openAgentsRoster()"
        />
      </template>
    </SessionTraceHeader>

    <ToolTokenRollup :rollup-data="toolRollupData" @jump-span="selectSpanById" />

    <TraceOverviewStrip
      :tree-nodes="treeNodes"
      :selected-span="selectedSpan"
      :selected-turn-uuid="selectedTurnUuid"
      :span-ids-in-selected-turn="spanIdsInSelectedTurn"
      :turns="turns"
      :trace-start="traceStart"
      :trace-end="traceEnd"
      :trace-duration="traceDuration"
      @select-node="onOverviewSpanClick"
    />

    <!-- Scoped-view bar: pins with the page header while the Conversation
         tab shows one subagent's subtree. Other tabs are never scoped (the
         ?agent= param persists but only Conversation applies it). -->
    <TraceScopeBar
      v-if="scopeTakeover && (traceScope.scopedAgent || traceScope.notFound)"
      :agent="traceScope.scopedAgent"
      :not-found="traceScope.notFound"
      :server-now="session?.server_now || ''"
      :server-now-at="session?.server_now_at || 0"
      :can-collapse="isXl"
      @exit="traceScope.exit()"
      @collapse="setScopeMode('split')"
    />

    <!-- Top indicator: only render when older history is available
         (or actively loading). Mirrors the bottom footer's
         infinite-feed pattern. -->
    <div
      v-if="hasMoreOlder || loadingOlder"
      class="mb-4 pb-3 border-b border-slate-200 flex items-center justify-center text-slate-400"
    >
      <span v-if="loadingOlder" class="inline-flex items-center gap-2 text-[12px]">
        <svg class="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none">
          <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="2" stroke-opacity="0.25"/>
          <path d="M22 12a10 10 0 0 1-10 10" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
        </svg>
        <span>Loading older</span>
      </span>
      <span v-else class="text-[11px] tracking-wider uppercase">↑ More history above</span>
    </div>
    </div>
    <!-- /page header (sticky ≥lg only) -->

    <!-- Compact sticky strip, phones/tablets: the full header above is too
         tall to pin below lg, so it scrolls away and only this title line +
         view-mode switcher stays. Placed after the header so it takes over
         the pin as the header scrolls out. -->
    <div
      ref="compactBarEl"
      class="lg:hidden sticky -top-4 z-20 -mx-4 mb-3 border-b border-slate-200 bg-white px-4 py-2 shadow-[0_2px_4px_-2px_rgba(15,23,42,0.06)]"
    >
      <div class="truncate text-[13px] font-semibold text-slate-800" :title="session.title || ''">
        {{ session.title || 'Session timeline' }}
      </div>
      <div class="mt-1.5 flex gap-1 overflow-x-auto">
        <Button
          v-for="opt in MODE_OPTIONS"
          :key="opt.id"
          variant="ghost"
          size="sm"
          class="h-auto shrink-0 rounded-full border px-2.5 py-1 text-[11px]"
          :class="viewMode === opt.id
            ? 'bg-blue-50 border-blue-400 text-blue-700 font-medium'
            : 'bg-white border-slate-200 text-slate-600'"
          @click="setViewMode(opt.id)"
        >{{ opt.label }}</Button>
      </div>
    </div>

    <!-- Queued prompts: typed while the agent is busy fire no hook, so they
         can't show as spans; derived live from the transcript and transient —
         they vanish from here the moment the agent dequeues them. -->
    <div v-if="session?.queued_prompts?.length"
         class="mb-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2">
      <div class="text-[11px] font-semibold uppercase tracking-wider text-amber-700 mb-1">
        ⏳ {{ session.queued_prompts.length }} queued
      </div>
      <div v-for="(q, qi) in session.queued_prompts" :key="qi"
           class="text-sm text-amber-900 truncate" :title="q.content">
        {{ q.content }}
      </div>
    </div>

    <div class="flex flex-col lg:flex-row gap-4 lg:items-start">
      <!-- Conversation view: feed (+ ≥xl companion pane) rendered outside Card
           so the feed sidebar, the pane, and the detail rail can each be
           sticky. The region owns the responsive scope layout (takeover <xl,
           split ≥xl). -->
      <template v-if="viewMode === 'conversation'">
        <TraceConversationRegion
          :trace-scope="traceScope"
          :live-agents="liveAgents"
          :is-xl="isXl"
          :takeover="scopeTakeover"
          :pane-visible="paneVisible"
          :hide-toc="paneVisible && !is2xl"
          :feed="feedProps"
          :sticky-top="stickyChromeHeight"
          @select-span="selectSpan($event)"
          @fetch-content="fetchSpanContent"
          @load-subtree="ensureSpanSubtreeLoaded"
          @jump-live="jumpToLatestSpan"
          @enter-scope="traceScope.enter($event)"
          @exit="traceScope.exit()"
          @expand="setScopeMode('full')"
        />
      </template>

      <template v-else>
        <Card :no-padding="true" class="trace-content-card flex-1 min-w-0 w-full">
          <!-- Timeline view: TreeTable -->
          <template v-if="viewMode === 'timeline'">
            <SessionTimelineTree
              :tree-nodes="treeNodes"
              v-model:expanded-keys="expandedKeys"
              v-model:selection-keys="selectedKeys"
              @node-select="onNodeSelect"
              @toggle-node="toggleTimelineNode"
            />
          </template>

          <!-- Terminal view: flat log -->
          <template v-else-if="viewMode === 'terminal'">
            <SessionTerminalLog
              :spans="allSpans"
              :turns="turns"
              :selected-span="selectedSpan"
              @select-span="selectSpan($event)"
              @fetch-content="fetchSpanContent"
              @load-subtree="ensureSpanSubtreeLoaded"
            />
          </template>

          <!-- Messages view: send_to_user feed as a vertical timeline -->
          <template v-else-if="viewMode === 'messages'">
            <SessionMessagesView
              :messages="agentMessages"
              :session-goal="sessionGoal"
              :highlighted-span="highlightedMessageSpan"
            />
          </template>
        </Card>
      </template>

      <!-- Span detail rail is irrelevant on the Messages tab (no span
           selection there) and would squeeze the centered feed. On the
           Conversation tab it is opt-in (detailRailOpen) for the same
           density reason; the sticky tab below reopens it. -->
      <Button
        v-if="isLgUp && viewMode === 'conversation' && selectedSpan && !detailRailOpen"
        variant="ghost"
        class="sticky self-start shrink-0 z-10 gap-1 px-2 py-1.5 h-auto rounded-md border border-slate-200 bg-white text-[11px] font-medium text-slate-500 hover:text-slate-700 hover:border-slate-300 transition-colors"
        :style="{ top: stickyHeaderHeight ? `calc(${stickyHeaderHeight}px - 1rem)` : '5rem' }"
        aria-label="Show span details"
        @click="detailRailOpen = true"
      >
        <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <polyline points="15 18 9 12 15 6" />
        </svg>
        Details
      </Button>
      <!-- ≥lg only: below lg span details render as the bottom sheet at the
           end of this template instead of a stacked-below-the-feed aside
           the user would never see. -->
      <aside
        v-if="isLgUp && detailRailShown"
        class="w-full lg:w-96 lg:shrink-0 lg:sticky lg:self-start lg:overflow-y-auto z-10"
        :style="{
          /* Page header is sticky-pinned with top: -1.5rem (lg padding-top)
             so its background covers .content-scroll padding-top. The
             sidebar pins flush under it: `header_h - 1.5rem + small gap`. */
          top: stickyHeaderHeight ? `calc(${stickyHeaderHeight}px - 1rem)` : '5rem',
          maxHeight: stickyHeaderHeight ? `calc(100vh - ${stickyHeaderHeight}px - 2rem)` : 'calc(100vh - 6rem)',
        }"
      >
        <div v-if="viewMode === 'conversation'" class="flex justify-end mb-2">
          <Button
            variant="ghost"
            class="gap-1 px-2 py-1 h-auto rounded-md border border-slate-200 bg-white text-[11px] font-medium text-slate-500 hover:text-slate-700 hover:border-slate-300 transition-colors"
            aria-label="Hide span details"
            @click="detailRailOpen = false"
          >
            Hide
            <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <polyline points="9 18 15 12 9 6" />
            </svg>
          </Button>
        </div>
        <SpanDetailPanel
          :key="selectedSpan && selectedSpan.span_id"
          v-bind="spanDetailProps"
          @suppress-changed="loadTriggersForSelectedSpan"
          @view-message="goToMessage"
        />

        <SessionTurnsSidebar
          :turns="turns"
          :turns-collapsed="turnsCollapsed"
          :turns-stale="turnsStale"
          :turns-loading="turnsLoading"
          :selected-turn-uuid="selectedTurnUuid"
          :expanded-turn-uuid="expandedTurnUuid"
          :selected-span="selectedSpan"
          :max-turn-consumption="maxTurnConsumption"
          @load="loadTurns"
          @toggle-collapsed="toggleTurnsCollapsed"
          @toggle-expanded="toggleTurnExpanded"
          @select-turn="selectTurn"
          @select-span-ref="handleSpanRefClick"
          @store-row="storeTurnRow"
        />
      </aside>
    </div>
    <!-- Below-lg bottom sheet for the selected span: the aside above is
         desktop-only, and on a phone a selection must produce immediately
         visible feedback. Backdrop tap or the close button dismisses the
         sheet; the selection itself is kept (desktop parity). -->
    <Teleport to="body">
      <div
        v-if="!isLgUp && sheetOpen && selectedSpan && viewMode !== 'messages'"
        class="fixed inset-0 z-40 cursor-pointer bg-slate-900/40 hover:bg-slate-900/45"
        @click.self="sheetOpen = false"
      >
        <div class="absolute inset-x-0 bottom-0 flex max-h-[75vh] cursor-auto flex-col rounded-t-xl bg-white shadow-2xl">
          <div class="flex items-center justify-between border-b border-slate-200 px-4 py-1.5">
            <span class="text-[11px] font-semibold uppercase tracking-wider text-slate-500">Span details</span>
            <Button
              variant="ghost"
              size="icon"
              aria-label="Close span details"
              @click="sheetOpen = false"
            >
              <Icon name="x" />
            </Button>
          </div>
          <div class="min-h-0 flex-1 overflow-y-auto px-4 pb-6 pt-3">
            <SpanDetailPanel
              :key="selectedSpan.span_id"
              v-bind="spanDetailProps"
              @suppress-changed="loadTriggersForSelectedSpan"
              @view-message="goToMessage"
            />
          </div>
        </div>
      </div>
    </Teleport>
    <!-- Infinite-feed-style footer: spinner during reload, otherwise
         a quiet end-of-timeline marker. Same pattern as Twitter/IG,
         no instructional text. `pb-20` below lg keeps the last rows
         scrollable clear of the fixed "Follow latest" pill. -->
    <div class="mt-8 mb-4 pb-20 lg:pb-0 flex items-center justify-center text-slate-400">
      <!-- Fixed-height, same-font-size row so the reload↔idle swap can't change
           the footer height: "Loading" and "End of timeline" share text-[11px]
           and the spinner sits inside the h-4 line. Otherwise, parked at the
           bottom of a live session, the tiny per-poll height change clamped the
           scroll and the feed twitched up/down every few seconds. -->
      <span class="inline-flex items-center justify-center gap-2 h-4 text-[11px] tracking-wider uppercase">
        <svg v-if="reloading" class="animate-spin h-3.5 w-3.5" viewBox="0 0 24 24" fill="none">
          <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="2" stroke-opacity="0.25"/>
          <path d="M22 12a10 10 0 0 1-10 10" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
        </svg>
        {{ reloading ? 'Loading' : 'End of timeline' }}
      </span>
    </div>
  </div>
</template>

<style scoped>
/* Make the TreeTable column headers (Span / Time / Tokens) pin under
   the sticky page header so they stay visible while scrolling a long
   span list. Card and PrimeVue both wrap the table in an overflow-auto
   container that would otherwise trap `position: sticky` inside the
   card; we override those to `overflow: visible` so sticky resolves to
   `.content-scroll` instead. `--regin-trace-header-h` is set on the
   root by the ResizeObserver in <script>. */
.trace-detail-root :deep(.trace-content-card.card) {
  overflow: visible !important;
}
.trace-detail-root :deep(.p-treetable-table-container) {
  overflow: visible !important;
}
/* Below lg the timeline must stay reachable via a LOCAL horizontal
   scroll (Pattern M): even with Time/Tokens dropped below sm, deep tree
   indentation can exceed a phone width. That scroll container would trap
   sticky th, so the thead pins only at ≥lg where the wrappers stay
   `overflow: visible`. */
@media (max-width: 1023px) {
  .trace-detail-root :deep(.trace-content-card.card) {
    overflow-x: auto;
  }
  .trace-detail-root :deep(.p-treetable-table-container) {
    overflow-x: auto !important;
  }
}
@media (min-width: 1024px) {
  .trace-detail-root :deep(.p-treetable-thead > tr > th) {
    position: sticky;
    top: calc(var(--regin-trace-header-h, 0px) - 1.5rem);
    z-index: 5;
    background: var(--color-white);
  }
}
</style>
