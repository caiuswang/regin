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
import { dropRetiredSpans, fmtTokens, fmtCost, fmtDuration } from '../utils/traceFormatters.js'
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
import Icon from '../components/ui/Icon.vue'
import SessionTraceHeader from '../components/SessionTraceHeader.vue'
import { useFoldTransition } from '../composables/useFoldTransition.js'
import TraceVitalsStrip from '../components/TraceVitalsStrip.vue'
import TraceSpendPanel from '../components/TraceSpendPanel.vue'
import TraceOverviewStrip from '../components/TraceOverviewStrip.vue'
import SpanDetailPanel from '../components/SpanDetailPanel.vue'
import { findNodeBySpanId, findNodePath, findNodeKey } from '../utils/spanTree.js'
import SessionTurnsSidebar from '../components/SessionTurnsSidebar.vue'
import SessionTimelineSpine from '../components/SessionTimelineSpine.vue'

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

// Header collapse: once the reader has scrolled PAST the full header's own
// height, it folds into a single compact row — status dot, title, a mono
// digest, the view switcher, Reload — so the spans get the space exactly
// when they're being read. Scrolling back to the top (within 24px) always
// re-expands and clears any manual pin; the Details button / H key toggle
// pins the choice until that return.
const headerCollapsed = ref(false)
const headerPinned = ref(false)
// The fold otherwise swaps ~200px of header in one frame — dazzling in
// both directions. Tween the wrapper's height across the state flip —
// except the scroll-driven collapse, which fires mid-gesture where a
// layout tween would kill the reader's own scroll; it stays instant (the
// compact row's compositor fade is its softening). The auto-expand only
// fires once the scroll has settled at the top, so it glides; manual
// toggles arm their glide explicitly in toggleHeaderDetails.
const headerFold = useFoldTransition(stickyHeaderEl, headerCollapsed, {
  glideWhen: (collapsed) => !collapsed,
})

// A pin created while already inside the 24px band would be cleared by its own
// unlock condition on the very next scroll nudge (a live poll's layout shift is
// enough), making a fold near the top a no-op. It holds until the reader
// actually leaves the band.
let pinnedInsideTopBand = false

function toggleHeaderDetails() {
  // A click/keypress means the scroll is settled, so even the collapse
  // direction can glide safely here.
  headerFold.glideNext()
  headerCollapsed.value = !headerCollapsed.value
  headerPinned.value = true
  if (expandTimer) { clearTimeout(expandTimer); expandTimer = null }
  pinnedInsideTopBand = (getScroller()?.scrollTop ?? 0) < 24
}

// The collapse threshold is the EXPANDED header's measured height (floored
// at 72px), not a flat 72px. Collapsing shrinks the header above the
// viewport, and the browser's scroll anchoring answers by pulling the scroll
// back by roughly the shrink; expanding at the top pushes it forward again
// by the same amount. A flat 72px threshold leaves an overlap band
// (72 < top < shrink) where the anchor pullback lands under the expand
// threshold and the pushback lands over the collapse threshold — the two
// fire each other forever (seen under parallel test load as a header that
// never settles). Threshold ≥ shrink + 6 makes that band empty: the header
// only folds once it would be fully scrolled through anyway, which is also
// the earliest point folding can't yank content out from under the reader.
const expandedHeaderHeight = ref(0)
// The !animating guard keeps mid-tween heights out: a threshold read off a
// half-grown header drops under the expand pushback and the two transitions
// fire each other again — the same oscillation, reintroduced by the glide.
watch(stickyHeaderHeight, (h) => {
  if (!headerCollapsed.value && h && !headerFold.animating.value) {
    expandedHeaderHeight.value = h
  }
}, { immediate: true })

// The expand at the top is additionally DWELLED 150ms — far longer than an
// anchor-adjustment frame pair, far shorter than a deliberate pause at the
// top — so a transient anchor scroll can't flip it.
let expandTimer = null

// Both transitions only react to scrolls backed by a recent USER INPUT
// (wheel / touch / scrollbar drag / scroll keys). Layout-driven scrolls —
// the browser's scroll anchoring after the header folds or the spend panel
// opens, Vue re-renders, programmatic scrollTop writes — carry no input, so
// the anchor pullback/pushback cycle can never feed the state machine and
// oscillate. 1200ms covers the smooth-scroll tail of one wheel notch.
// Starts at -1e9: initializing to 0 would leave the gate OPEN for the first
// 1.2s of page life (performance.now() is navigation-relative).
let lastScrollInputAt = -1e9
const SCROLL_INPUT_KEYS = new Set(['PageUp', 'PageDown', 'Home', 'End', 'ArrowUp', 'ArrowDown', ' '])
function markScrollInput() { lastScrollInputAt = performance.now() }
function isTextEntry(t) {
  return !!t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)
}
function onScrollInputKey(e) {
  // Space/arrows typed into a filter field are text entry, not scrolling. The
  // shorter list that filter produces clamps scrollTop, and that layout scroll
  // would ride the armed gate and fold the header mid-keystroke.
  if (!isTextEntry(e.target) && SCROLL_INPUT_KEYS.has(e.key)) markScrollInput()
}
function onScrollbarMouseDown(e) {
  // Only a press on the scroller's scrollbar track arms the gate. Any other
  // click must not: the layout shift it can trigger (e.g. the spend panel
  // opening pushes the page down via scroll anchoring) would otherwise
  // inherit the click's input and read as a deliberate user scroll.
  const r = getScroller()?.getBoundingClientRect?.()
  if (r && e.clientX >= r.right - 20) markScrollInput()
}

function onCollapseScroll(e) {
  const scroller = getScroller()
  const el = e?.target
  if (!el || (el !== scroller && el !== document && el !== document.scrollingElement)) return
  const top = scroller.scrollTop ?? 0
  if (top < 24) {
    // The expand is DWELLED and re-checked at fire time. The 24px band (not
    // 0) and the re-check both matter: a live poll's layout shift can nudge
    // the scroll a few px off the top, and a dead timer would never re-arm —
    // the header would stay folded while the user is effectively at the top.
    // No input gate here: expanding can't cycle, because the collapse side
    // only fires on user input.
    if (!expandTimer) {
      expandTimer = setTimeout(() => {
        expandTimer = null
        const nowTop = getScroller()?.scrollTop ?? 0
        if (nowTop >= 24) return
        if (pinnedInsideTopBand) return
        if (headerCollapsed.value || headerPinned.value) {
          headerCollapsed.value = false
          headerPinned.value = false
        }
      }, 150)
    }
    return
  }
  pinnedInsideTopBand = false
  if (performance.now() - lastScrollInputAt > 1200) return
  if (headerPinned.value) return
  // Auto-collapse is a ≥lg feature: there the header pins and collapsing buys
  // back reading space. Below lg the full header scrolls away on its own (the
  // compact bar is the pin), and folding it would only unmount the spend
  // panel / strips out from under a reader who scrolled to them.
  if (!isLgUp.value) return
  if (headerCollapsed.value) return
  if (top <= Math.max(72, expandedHeaderHeight.value)) return
  // Only collapse once the header has actually reached its sticky pin. The
  // pin offset is measured from the scroller's CONTENT box (top: -24px vs
  // the 24px padding), so a stuck header's top edge lands exactly on the
  // scrollport's top.
  const header = stickyHeaderEl.value
  if (!header) return
  const scrollerTop = scroller.getBoundingClientRect?.().top ?? 0
  if (header.getBoundingClientRect().top > scrollerTop + 1) return
  headerCollapsed.value = true
}

// Below lg the header isn't sticky, so a fold that survives the resize takes
// its own Details button off-screen. Pinned folds are the user's call at any
// width; only the auto one is undone. The crossing SNAPS, never glides: a
// tween mid-resize leaves the content transiently shorter, and the clamp
// scroll that answers can land inside the collapse machine's user-input
// window and fold the header nobody asked to fold.
watch(isLgUp, (up) => {
  headerFold.snap()
  if (!up && headerCollapsed.value && !headerPinned.value) headerCollapsed.value = false
})

function onHeaderKey(e) {
  if (isTextEntry(e.target)) return
  if (e.metaKey || e.ctrlKey || e.altKey) return
  if (e.key === 'h' || e.key === 'H') {
    e.preventDefault()
    toggleHeaderDetails()
  }
}

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
  taskEvents: session.value?.task_list?.events || [],
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
  traceStart: traceStart.value,
}))

// send_to_user messages (Messages tab). Null until first load so the tab
// can distinguish "not fetched yet" from "fetched, empty". Refreshed by
// reload() while the tab is active, so the live poll keeps it current.
const agentMessages = ref(null)
const sessionGoal = ref(null)

// Row counts on the segmented switcher, so the reader knows whether a tab is
// worth opening. Only tabs with a countable, always-known population get one:
// Conversation/Timeline render the same spans the vitals strip already counts,
// and a duplicate there would be noise. Messages stays absent until its first
// fetch — a `0` pill would claim "no messages" when the truth is "not loaded".
// "Turns" in the vitals strip means user prompts, counted off the loaded root
// spans — NOT `turns.length`. The turn_usage table is per-API-response billing
// data that is frequently still empty on a live session, which rendered the
// cell as an em-dash next to a feed visibly showing several prompts.
const promptCount = computed(() => {
  const n = treeNodes.value.filter(t => t?.data?.name === 'prompt').length
  // Workflow runs: turn_usage rows are the subagents' API responses, not
  // user prompts — falling back to them read as hundreds of "turns".
  if (session.value?.is_workflow) return n || null
  return n || (turns.value ? turns.value.length : null)
})

const modeCounts = computed(() => ({
  terminal: session.value?.span_count_total ?? allSpans.value.length,
  messages: agentMessages.value?.length ?? 0,
}))
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
// session is closed (`ended_at` set — the ingest clears it again on a
// genuine resume) AND the tail has converged — see `maybeStopOnConverge`.
// An already-ended session never starts the recurring poll at all; it runs
// one bounded catch-up instead (`syncClosedSessionTail`), which is also the
// crash-recovery path (reopening the view re-runs it).
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
  if (!session.value?.ended_at) {
    convergeAnchorId = null
    // Live (again): the ingest clears `ended_at` when a genuine resume lands,
    // so a poll that retired on the old "ended" read — and the scroll/wheel
    // pull-to-refresh retired with it — re-arms here, on whichever reload
    // first observes the cleared marker.
    if (!livePollTimer) startLivePoll()
    liveSyncActive.value = true
    return
  }
  if (!livePollTimer) return
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

async function ensureTurnsLoaded() {
  if (turns.value != null || turnsLoading.value) return
  turnsLoading.value = true
  try { await fetchTurns() } finally { turnsLoading.value = false }
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
  // The watcher below only fires on a CHANGE, so a load that lands directly on
  // the conversation tab (its localStorage default) never fetched turn_usage —
  // leaving the Turns rail without cost/token figures and every turn without
  // its usage footer.
  if (viewMode.value === 'conversation') ensureTurnsLoaded()
  // Scroll/wheel/touch auto-reload listeners are attached by useTraceScroll();
  // the sticky-header ResizeObserver is owned by useStickyHeader.
  // Capture phase: scroll events don't bubble, and the scroller is the
  // app shell's `.content-scroll`, an ancestor of this view.
  document.addEventListener('scroll', onCollapseScroll, { capture: true, passive: true })
  document.addEventListener('keydown', onHeaderKey)
  document.addEventListener('wheel', markScrollInput, { capture: true, passive: true })
  document.addEventListener('touchmove', markScrollInput, { capture: true, passive: true })
  document.addEventListener('mousedown', onScrollbarMouseDown, { capture: true, passive: true })
  document.addEventListener('keydown', onScrollInputKey)
})

onUnmounted(() => {
  // Scroll/wheel/touch listeners are detached by useTraceScroll(); the
  // sticky-header observer + compact poll are torn down by their composables.
  stopLivePoll()
  if (expandTimer) { clearTimeout(expandTimer); expandTimer = null }
  document.removeEventListener('scroll', onCollapseScroll, { capture: true })
  document.removeEventListener('keydown', onHeaderKey)
  document.removeEventListener('wheel', markScrollInput, { capture: true })
  document.removeEventListener('touchmove', markScrollInput, { capture: true })
  document.removeEventListener('mousedown', onScrollbarMouseDown, { capture: true })
  document.removeEventListener('keydown', onScrollInputKey)
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
    await ensureTurnsLoaded()
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

// Compact-header digest: the five vitals that survive the collapse — spans,
// duration, tokens, cost, ctx%. Reads the same sources as TraceVitalsStrip
// (tool rollup first, session fields as fallback) so the two never disagree.
const headerDigest = computed(() => {
  const s = session.value
  if (!s) return []
  const d = toolRollupData.value
  const spendUsd = d ? (d.total_spend_usd ?? d.session_cost_usd) : null
  const rollTokens = d?.total_spend_tokens ?? d?.session_total_tokens
  const totalTok = (Number.isFinite(rollTokens) && rollTokens > 0)
    ? rollTokens : (s.total_tokens ?? null)
  const ctx = Number.isFinite(s.context_pct) ? s.context_pct : null
  const ctxTone = ctx == null ? 'text-slate-400'
    : ctx >= 80 ? 'text-red-600' : ctx >= 50 ? 'text-amber-600' : 'text-emerald-600'
  return [
    { key: 'spans', value: String(s.span_count_total ?? allSpans.value.length), label: 'spans', tone: 'text-slate-700' },
    { key: 'duration', value: fmtDuration(Math.round(traceDuration.value)) || '—', label: '', tone: 'text-slate-700' },
    { key: 'tokens', value: totalTok != null ? fmtTokens(totalTok) : '—', label: 'tok', tone: 'text-slate-700' },
    { key: 'cost', value: Number.isFinite(spendUsd) ? fmtCost(spendUsd) : '—', label: '', tone: 'text-emerald-600' },
    { key: 'ctx', value: ctx != null ? `${ctx}%` : '—', label: 'ctx', tone: ctxTone },
  ]
})

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
  if (!span) return
  selectSpan(span)
  // These jumps come from chrome ABOVE the feed (a task row, a spend
  // drill-down target), where the answer the user asked for lives in the span
  // detail. On the Conversation tab that rail is opt-in and defaults closed, so
  // without this the click selected a span and produced no visible result.
  if (viewMode.value === 'conversation') detailRailOpen.value = true
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
      data-testid="trace-sticky-header"
      class="lg:sticky lg:-top-6 z-20 bg-white -mx-4 -mt-4 px-4 pt-4 lg:-mx-8 lg:-mt-6 lg:px-8 lg:pt-6 mb-4 border-b border-slate-200 shadow-[0_2px_4px_-2px_rgba(15,23,42,0.06)]"
      :class="headerCollapsed ? 'pb-3' : 'pb-4'"
    >
    <SessionTraceHeader
      :key="session?.trace_id"
      :session="session"
      :plans="plans"
      :workflow-runs="workflowRuns"
      :view-mode="viewMode"
      :mode-counts="modeCounts"
      :reloading="reloading"
      :loading="loading"
      :last-reloaded-at="lastReloadedAt"
      :has-turns="turns != null"
      :snapshot-stale-at="snapshotStaleAt"
      :workflow-parent-to="workflowParentTo"
      :collapsed="headerCollapsed"
      :digest="headerDigest"
      @update:view-mode="setViewMode"
      @reload="reload"
      @jump-to-task="jumpToTaskSpan"
      @toggle-collapse="toggleHeaderDetails"
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

    <!-- Everything below the header's own row folds away in the collapsed
         state: vitals strip, overview mini-timeline, spend disclosure. The
         scope bar and the more-history banner stay — they're navigation,
         not details. -->
    <TraceVitalsStrip
      v-if="!headerCollapsed"
      :session="session"
      :trace-duration="traceDuration"
      :active-work-ms="activeWorkMs"
      :rollup-data="toolRollupData"
      :turn-count="promptCount"
      :agent-count="liveAgents.agents.length"
    />

    <TraceOverviewStrip
      v-if="!headerCollapsed"
      :tree-nodes="treeNodes"
      :selected-span="selectedSpan"
      :selected-turn-uuid="selectedTurnUuid"
      :span-ids-in-selected-turn="spanIdsInSelectedTurn"
      :turns="turns"
      :is-workflow="session?.is_workflow === true"
      :trace-start="traceStart"
      :trace-end="traceEnd"
      :trace-duration="traceDuration"
      @select-node="onOverviewSpanClick"
    />

    <!-- v-show, not v-if: the panel owns its open/closed state, and unmounting
         it on every collapse cycle silently re-closes a bill the reader had
         expanded. The vitals/overview strips above are stateless, so they fold
         with v-if. -->
    <TraceSpendPanel
      v-show="!headerCollapsed"
      :rollup-data="toolRollupData"
      @jump-span="selectSpanById"
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
          <!-- Timeline view: chronological event spine -->
          <template v-if="viewMode === 'timeline'">
            <SessionTimelineSpine
              :tree-nodes="treeNodes"
              :expanded-keys="expandedKeys"
              :selected-keys="selectedKeys"
              :trace-start="traceStart"
              :trace-duration="traceDuration"
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
        <!-- The conversation feed prints its own end-of-timeline marker at the
             bottom of the feed COLUMN (where the design puts it); printing this
             page-wide one too would show the reader two of them. The row itself
             stays so the reload spinner and the fixed footer height survive. -->
        {{ reloading ? 'Loading' : (viewMode === 'conversation' ? '' : 'End of timeline') }}
      </span>
    </div>
  </div>
</template>

<style scoped>
/* The Terminal/Messages tabs render inside `.trace-content-card`, which the
   Card wraps in an overflow-auto container; keep it `overflow: visible` so a
   sticky descendant resolves to `.content-scroll`, and let a wide flat log
   reach a LOCAL horizontal scroll below lg rather than clip on a phone. */
.trace-detail-root :deep(.trace-content-card.card) {
  overflow: visible !important;
}
@media (max-width: 1023px) {
  .trace-detail-root :deep(.trace-content-card.card) {
    overflow-x: auto;
  }
}
</style>
