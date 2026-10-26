<script setup>
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import api from '../api'
import Card from '../components/Card.vue'
import Button from '../components/ui/Button.vue'
import NewSessionButton from '../components/live/NewSessionButton.vue'
import { terminalLaunchRoute } from '../utils/launchTarget.js'
import SessionMessagesView from '../components/SessionMessagesView.vue'
import SessionTerminalLog from '../components/SessionTerminalLog.vue'
import SessionTerminalPane from '../components/trace/SessionTerminalPane.vue'
import TraceConversationRegion from '../components/TraceConversationRegion.vue'
import SuppressButton from '../components/triggers/SuppressButton.vue'
import { dropRetiredSpans, fmtTokens, fmtCost, fmtDuration } from '../utils/traceFormatters.js'
import { useTraceScroll } from '../composables/useTraceScroll.js'
import { useStickyHeader, useStickyChromeHeight } from '../composables/useStickyHeader.js'
import { MODE_OPTIONS, useViewMode } from '../composables/useViewMode.js'
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
import { useAgentTrace } from '../composables/useAgentTrace.js'
import { useBreakpoint } from '../composables/useBreakpoint.js'
import TraceScopeBar from '../components/TraceScopeBar.vue'
import TraceAgentPane from '../components/TraceAgentPane.vue'
import TraceAgentsPopover from '../components/TraceAgentsPopover.vue'
import { scrollSpanRowIntoView } from '../utils/scrollSpanRow.js'
import { getTraceScroller } from '../utils/traceScroller.js'
import { useHeaderRevealPull } from '../composables/useHeaderRevealPull.js'
import { useBoundedTrace } from '../composables/useBoundedTrace.js'
import Icon from '../components/ui/Icon.vue'
import SessionTraceHeader from '../components/SessionTraceHeader.vue'
import { useFocusMode } from '../composables/useFocusMode.js'
import { useFoldTransition } from '../composables/useFoldTransition.js'
import TraceVitalsStrip from '../components/TraceVitalsStrip.vue'
import TraceSpendPanel from '../components/TraceSpendPanel.vue'
import TraceOverviewStrip from '../components/TraceOverviewStrip.vue'
import SpanDetailPanel from '../components/SpanDetailPanel.vue'
import { findNodeBySpanId, findNodePath, findNodeKey } from '../utils/spanTree.js'
import SessionTurnsSidebar from '../components/SessionTurnsSidebar.vue'
import SessionTimelineSpine from '../components/SessionTimelineSpine.vue'
import SpanPresentReader from '../components/present/SpanPresentReader.vue'
import { usePresentReader } from '../composables/usePresentReader.js'
import { useSpanTree } from '../composables/useSpanTree.js'
import { registerTraceSearchSource } from '../composables/useTraceSearchSource.js'
import { onTraceSpans, setTraceInterest } from '../composables/useTraceSpanEvents.js'
import { useSuccessorFollow } from '../composables/useSuccessorFollow'
import { useQueuedPrompts } from '../composables/useQueuedPrompts.js'
import TraceQueuedPrompts from '../components/TraceQueuedPrompts.vue'
import { restoreToComposer, cancelComposerRestore } from '../composables/useComposerRestore.js'

const route = useRoute()
const router = useRouter()
const session = ref(null)
const loading = ref(true)
// Queued / steer prompts (server-authoritative), plus the edit/remove round
// trips for the rows that carry a server id. Same rows and same routes the
// /live card drives — `_queued_prompts` serves both surfaces — so the write
// surface is the composable, not a second copy of the logic.
const queued = useQueuedPrompts(
  () => session.value?.queued_prompts, () => session.value?.trace_id || '',
  { offerSendNow: true })

async function onQueueEdit({ id, text, done }) {
  if (await queued.edit(id, text)) done?.()
}

// Once the composer holds the text the chip is dismissed — left up, it would
// sit beside the draft as a second copy of the same words.
function onQueueRestore({ id, text }) {
  if (viewMode.value !== 'conversation') setViewMode('conversation')
  restoreToComposer(session.value?.trace_id || '', text, () => queued.remove(id))
}
// Detail of a failed INITIAL load. "Loading session…" is the view's only
// loading affordance, so without an explicit failure exit a rejected first
// /map leaves it on screen forever.
const loadErrorDetail = ref('')
const loadFailed = ref(false)
const reloading = ref(false)
// Detail of a failed reload AFTER a good initial load. The loaded spans are
// still worth reading, so this never replaces the pane the way `loadFailed`
// does — it only marks the header's reload control as showing stale data.
// Without it the unawaited callers (the live poll, useTraceScroll's
// pull-to-refresh) turn every tick against a dead /map into an unhandled
// rejection with nothing on screen to say the view has frozen.
const reloadFailed = ref(false)
const reloadErrorDetail = ref('')
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

// Above the shell's `fixedViewport` freeze this view stops being a long page
// and becomes a bounded column: header on top, one internally-scrolling region
// per pane below it. Drives the layout classes; the panes' own offsets change
// with it (see `headerOffset`).
const boundedLayout = useBoundedTrace()

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

// Clearance the rails and the feed's scroll-margins reserve under the pinned
// header. Bounded, the header sits OUTSIDE every scroll region, so reserving
// its height would push each pane down by a header nothing scrolls under.
const headerOffset = computed(() => (
  boundedLayout.value || !stickyChromeHeight.value ? '0px' : `${stickyChromeHeight.value}px`
))

// Header collapse: once the reader has scrolled PAST the full header's own
// height, it folds into a single compact row — status dot, title, a mono
// digest, the view switcher, Reload — so the spans get the space exactly
// when they're being read. The Details button / H key toggle pins the choice.
//
// Losing the header and getting it back are deliberately ASYMMETRIC. Hiding is
// one threshold, no gesture: it costs the reader nothing, and H brings it back.
// Revealing is a sustained pull past the top (useHeaderRevealPull below),
// because the old rule — any upward scroll within 24px of the top re-expands —
// threw the details bar in the face of anyone reading back a few lines.
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

// Only the pull's own commit and the explicit toggles below ever expand the
// header, and all three carry user input by construction. That is what keeps a
// layout-driven scroll — anchoring answering the fold's own shrink, a live
// poll's shift, a programmatic scrollTop write — from popping the header open
// under a reader who folded it by hand. The expand side therefore needs no
// input gate of its own; only auto-collapse still consults one.
const headerPull = useHeaderRevealPull({
  getScroller: getTraceScroller,
  canReveal: () => headerCollapsed.value && !headerPinned.value && isLgUp.value,
  onReveal: () => {
    // The single reactive write of the whole gesture. `glideWhen` already
    // tweens the expand direction, so the fold softens itself from here.
    headerCollapsed.value = false
    headerPinned.value = false
  },
})

function toggleHeaderDetails() {
  // A click/keypress means the scroll is settled, so even the collapse
  // direction can glide safely here.
  headerFold.glideNext()
  headerCollapsed.value = !headerCollapsed.value
  headerPinned.value = true
  // An explicit toggle settles the question the meter was asking; a half-built
  // pull must not survive to commit a second reveal a moment later.
  headerPull.reset()
  // Folding shrinks the header, and scroll anchoring answers with a scroll
  // that can land in the top band. Consuming the input gate keeps that anchor
  // scroll — still riding the wheel flick that preceded the click — from
  // reading as a deliberate scroll and re-folding the header the same click
  // just expanded.
  lastScrollInputAt = -1e9
}

// The <lg twin of `toggleHeaderDetails`, from the pinned compact strip. It
// carries the scroll as well as the state, because at this width the header is
// NOT sticky: expanding it in place only inserts content far above the reading
// position, and scroll anchoring then holds the view still — the reader taps
// "Details" and nothing visibly happens. The details live at the top, so
// asking for them means going there.
function onCompactDetailsToggle() {
  const wasCollapsed = headerCollapsed.value
  toggleHeaderDetails()
  if (wasCollapsed) scrollToTop()
}

// Following the tail is a request for transcript space: the reader is watching
// new spans land, not reading the header. The scroll `enableFollow` fires to
// reach the bottom is programmatic, so canAutoCollapse's input gate refuses it
// — the fold has to be asked for here or a fresh page keeps ~200px of header
// pinned above a feed nobody is reading the top of. Turning follow off leaves
// the fold alone; re-expanding would move the page under a reader who only
// wanted the spans to stop moving.
//
// The pin is CLEARED, not left standing. It is the follow gesture's fold now,
// not the H press that came before it, and a stale pin would strand it: the
// reveal pull refuses to fire while pinned (canReveal), and the isLgUp watch
// refuses to undo a pinned fold — so a reader who had used H once would be
// left, after a resize below lg, staring at a folded header that isn't even
// sticky there.
function collapseHeaderForFollow() {
  if (!isLgUp.value || headerCollapsed.value) return
  // A click means the scroll is settled, so the collapse can glide.
  headerFold.glideNext()
  headerCollapsed.value = true
  headerPinned.value = false
  headerPull.reset()
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
// The compact strip's Details toggle exists only because the full header is not
// sticky below lg and scrolls away carrying its own copy. While both are on
// screen that is two buttons for one job, a step apart. Measured against the
// header's actual position rather than a scroll threshold: its height varies
// with the title, the feature row and the fold state, so a constant would be
// wrong for most sessions.
const headerOffscreen = ref(false)
function syncHeaderOffscreen() {
  const scroller = getTraceScroller()
  const header = stickyHeaderEl.value
  if (!scroller || !header) { headerOffscreen.value = false; return }
  const scrollerTop = scroller.getBoundingClientRect?.().top ?? 0
  headerOffscreen.value = header.getBoundingClientRect().bottom <= scrollerTop + 1
}

watch(stickyHeaderHeight, (h) => {
  if (!headerCollapsed.value && h && !headerFold.animating.value) {
    expandedHeaderHeight.value = h
  }
  // A fold changes whether the header still covers the strip without any
  // scroll happening, so the scroll handler alone would leave the strip's
  // Details toggle a fold behind.
  syncHeaderOffscreen()
}, { immediate: true })

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
function hasRecentScrollInput() { return performance.now() - lastScrollInputAt <= 1200 }
function isTextEntry(t) {
  // SELECT counts: its own type-ahead consumes bare letters (and `\`), so a
  // global single-key shortcut fired from a focused select silently changes
  // both the option and the page. `role=combobox` is the same thing wearing a
  // <button> — that is what every Reka Select trigger in this app renders as,
  // and it type-aheads too.
  if (!t) return false
  if (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT') return true
  if (t.isContentEditable) return true
  return !!t.closest?.('[role="combobox"], [role="listbox"], [role="option"]')
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
  const r = getTraceScroller()?.getBoundingClientRect?.()
  if (r && e.clientX >= r.right - 20) markScrollInput()
}

// A session trace runs to thousands of spans, and the app shell's navigation
// lives outside `.content-scroll` — so getting back to the page header (title,
// switcher, scope bar, Reload) meant hand-scrolling the whole transcript. The
// threshold is well past the fold so the control never crowds the top of a
// short trace.
const BACK_TO_TOP_AFTER_PX = 800
const scrolledDeep = ref(false)
// Switching to a shorter tab clamps the scroll without necessarily leaving the
// control's state correct — re-read the scroller rather than waiting for a
// scroll event that may never come.
function syncScrolledDeep() {
  scrolledDeep.value = (getTraceScroller()?.scrollTop ?? 0) > BACK_TO_TOP_AFTER_PX
  syncHeaderOffscreen()
}

function scrollToTop() {
  const scroller = getTraceScroller()
  if (!scroller) return
  // Deliberately does NOT clear the pin. Arriving at the top is no longer what
  // reveals the header, so treating it as one would silently re-arm
  // auto-collapse against a reader who pinned the header open by hand.
  if (typeof scroller.scrollTo === 'function') scroller.scrollTo({ top: 0, behavior: 'smooth' })
  else scroller.scrollTop = 0
}

function canAutoCollapse(scroller, top) {
  if (!hasRecentScrollInput()) return false
  if (headerPinned.value) return false
  // Auto-collapse is a ≥lg feature: there the header pins and collapsing buys
  // back reading space. Below lg the full header scrolls away on its own (the
  // compact bar is the pin), and folding it would only unmount the spend
  // panel / strips out from under a reader who scrolled to them.
  if (!isLgUp.value) return false
  if (headerCollapsed.value) return false
  if (top <= Math.max(72, expandedHeaderHeight.value)) return false
  // Only collapse once the header has actually reached its sticky pin. The
  // pin offset is measured from the scroller's CONTENT box (top: -24px vs
  // the 24px padding), so a stuck header's top edge lands exactly on the
  // scrollport's top.
  const header = stickyHeaderEl.value
  if (!header) return false
  const scrollerTop = scroller.getBoundingClientRect?.().top ?? 0
  return header.getBoundingClientRect().top <= scrollerTop + 1
}

function onCollapseScroll(e) {
  const scroller = getTraceScroller()
  const el = e?.target
  if (!el || (el !== scroller && el !== document && el !== document.scrollingElement)) return
  const top = scroller.scrollTop ?? 0
  syncScrolledDeep()
  // Scroll GATES the gesture (is the reader resting at the top?) and hides the
  // header. It never reveals — that takes the pull, which rides wheel/touch.
  headerPull.noteScroll(top)
  if (canAutoCollapse(scroller, top)) {
    headerCollapsed.value = true
    // Stamping the hide starts the cooldown, so the momentum of the very
    // scroll that folded the header cannot carry through into a reveal.
    headerPull.noteHidden()
  }
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
  headerPull.reset()
})

// The effect a single-letter trace shortcut asks for, or null when this key
// does nothing here. Returning the action rather than running it keeps the
// preventDefault in one place: a key that resolves to nothing must fall
// through untouched.
function headerShortcut(e) {
  const key = e.key
  if (key === 'h' || key === 'H') return toggleHeaderDetails
  // The detail modes govern the conversation feed only; firing one from
  // Timeline or Terminal would flip a mode with nothing on screen to show for
  // it. Each key toggles against Full, so the same keystroke undoes itself.
  if (viewMode.value === 'conversation') {
    if (key === 'f' || key === 'F') return toggleLatest
    if (key === 'c' || key === 'C') return toggleCondensed
  }
  // Presentation mode on the current selection. No-op with nothing selected —
  // the reader is keyed to one span, and guessing which one is worse than
  // doing nothing.
  //
  // Enter additionally stands down whenever a control has focus: Enter is that
  // control's OWN activation key, so binding it globally hijacked every
  // keyboard press of Copy, 📌, a mode tab, or the ⤢ button itself.
  const onControl = !!e.target?.closest?.('button, a, summary, [role="button"], [role="tab"]')
  const wantsReader = key === 'p' || key === 'P' || (key === 'Enter' && !onControl)
  if (wantsReader && selectedSpan.value) return () => openPresent(selectedSpan.value)
  return null
}

function onHeaderKey(e) {
  if (isTextEntry(e.target)) return
  if (e.metaKey || e.ctrlKey || e.altKey) return
  const action = headerShortcut(e)
  if (!action) return
  e.preventDefault()
  action()
}

// On-demand span content cache; `allSpans` overlays it onto session.spans so
// every consumer reads one merged list (see useSpanContentCache).
const { spanContentCache, allSpans, withContent, fetchSpanContent } =
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
  ensureNodeChildrenLoaded, ensureSpanSubtreeLoaded, ensureSpanSubtreesLoaded,
  noteSpanFrame,
  ensureTerminalSpansLoaded, ensureWorkflowSpansLoaded,
} = useTraceData(route, { session, allSpans, selectedSpan, router })

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
  isTakeover: () => !isXl.value || scopeMode.value === 'full',
})
// The scoped agent's spans come from its own trace — this conversation holds
// only its markers.
const agentTrace = useAgentTrace(() => traceScope.scopeId)
// Through the same content cache as the main feed: the child's map ships the
// structure, the bodies are still fetched (and cached) per span.
const agentSpans = computed(() => withContent(agentTrace.spans))
// A `?span=` inside delegated work: useTraceData resolves it to `?agent=` (it
// has no such row to select), and the row itself only exists once the child
// fetch lands — so the selection is made here, against those spans.
watch(agentSpans, (spans) => {
  const target = route.query.span
  if (!target || selectedSpan.value?.span_id === target) return
  const hit = spans.find(s => s.span_id === target)
  if (hit) selectedSpan.value = hit
})

// View mode: 'conversation' | 'timeline' | 'log' | 'pane' | 'messages'.
// Resolution order: `?view=` query param > localStorage > default (see useViewMode).
const { viewMode, setViewMode } = useViewMode(route)
const { toggleCondensed, toggleLatest, resetForTrace } = useFocusMode()
watch(viewMode, () => nextTick(syncScrolledDeep))

// The conversation tab defaults to the clean centered feed: the right rail
// (span details + turns) stays hidden until explicitly opened, so selecting
// a span doesn't squeeze the feed. Timeline/log keep the rail whenever
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
// 'pane' joins 'messages' in never showing it: a terminal has no span to
// detail, and the rail's close control is rendered only on the conversation
// tab — so on the terminal it appeared with no way to dismiss it and simply
// ate the width.
const RAILLESS_MODES = ['messages', 'pane']
const detailRailShown = computed(() => !!selectedSpan.value
  && !RAILLESS_MODES.includes(viewMode.value)
  && (viewMode.value !== 'conversation' || detailRailOpen.value))
// Whether the rail is actually on screen — the span detail panel AND the turns
// sidebar ride inside it. Named once so the template's gate and the turn-detail
// fetch below cannot drift apart: the sidebar being unrendered is exactly what
// makes a turn's `span_refs`/`tool_summary` not worth fetching.
const turnsSidebarShown = computed(() => isLgUp.value && detailRailShown.value)
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
function setScopeMode(mode) {
  const scroller = getTraceScroller()
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
// A zero-span session is still a real session — one opened before its first
// prompt fired any hook — and must render the full chrome (header, tabs, the
// follow-up composer). Only an id the server has no session row for falls to
// the empty state: _session_summary returns {} there, so canonical_trace_id
// is the discriminator between "no row" and "row, no spans yet".
const sessionKnown = computed(() =>
  !!session.value
  && (!!session.value.canonical_trace_id || allSpans.value.length > 0))

// The other exit from a conversation with no composer: not steerable, but the
// CLI can still reopen it. `resumable` is served derived rather than inferred
// here — whether an id can be handed to `--resume` turns on a transcript still
// on disk, which the client cannot see. Its own computed so the follow-up
// bundle below stays a flat projection of one row.
const resumeMeta = computed(() => {
  const s = session.value
  // `cli_held` is the third state, and it exists because the first two are
  // both an empty rail: unsteerable AND unresumable renders nothing, and only
  // this says the conversation is alive — the CLI is holding it somewhere
  // regin cannot type, so there is nothing to resume and nothing is wrong.
  const heldByCli = !!s && !s.bridge_reachable && !!s.cli_held
  const drivenBy = (s && !s.bridge_reachable && s.driven_by) || ''
  if (!s || !s.resumable || s.bridge_reachable) return { canResume: false, heldByCli, drivenBy }
  return { canResume: true, heldByCli: false, drivenBy: '',
           cwd: s.cwd || '', endedReason: s.ended_reason || '' }
})

// A session started from this page is a NEW conversation, not this one
// continued, so it gets its own page — and it stays on this surface rather
// than jumping to /live: an operator launching from the desktop trace view
// asked for a trace, and the run is readable here immediately because the
// launch posts its prompt span before the request returns (see
// `_announce_prompt` in lib/agent_sdk/supervisor.py).
function onSessionLaunched(launched) {
  const { traceId } = launched
  if (!traceId) return
  const terminal = terminalLaunchRoute(launched)
  if (terminal) router.push(terminal)
  else if (traceId !== route.params.id) router.push(`/trace/sessions/${traceId}`)
}

// …but the id it lands on is the run's own `sdk-…`, the only one that exists
// before the child names itself. Everything else keys the session on the
// child's id — the transcript on disk, the session list, and the endpoints
// that do NOT alias-resolve (`/tool-rollup`, `/turn-usage`, `/agent-messages`,
// `/workflow-runs` all answer 200 with a fraction of the rows) — so rewrite
// the URL onto it the moment the server reports it, exactly as `/live` does.
// `replace`, not `push`: the sdk- URL is a staging post, not somewhere Back
// should return to. The shell keys this component by `$route.path`, so the
// replace remounts and refetches under the canonical id.
watch(() => session.value?.canonical_trace_id, (canon) => {
  const from = route.params.id
  if (!canon || !from || canon === from) return
  if (!route.path.startsWith('/trace/sessions/')) return
  // Only rename the session the poll actually loaded — a canonical id left
  // over from the row we just navigated away from would redirect back to it.
  if (session.value?.trace_id !== from) return
  // A successor outranks a rename, but only when they name DIFFERENT sessions:
  // `successor_trace_id` is not clear-only (`register_alias` points
  // `_live_children` at the first child it sees), so a page on a run's own
  // `sdk-…` id carries it equal to the canonical id from the first poll, and
  // suppressing on that would strand the URL on the staging post forever.
  const heir = session.value?.successor_trace_id
  if (heir && heir !== canon) return
  router.replace({ path: `/trace/sessions/${canon}`, query: route.query })
})

// The follow-up composer exists only where a follow-up can actually land: a
// session still running that regin can steer.
const followUpMeta = computed(() => {
  const s = session.value
  return {
    // `successor_trace_id` is the /clear case: this conversation is over but
    // the run that held it is not, and a steer sent to this id still reaches
    // it. Gating on ended_at alone left the operator on a dead page with no
    // way to type while the agent worked on under an id nothing linked to.
    canFollowUp: !!s && !!s.bridge_reachable
      && (!s.ended_at || !!s.successor_trace_id),
    ...resumeMeta.value,
    model: s?.model || '',
    ctxPct: s?.context_pct ?? null,
    // Just the three fields the terminal peek reads, not the whole row: the
    // feed has no other business with the session record.
    liveScreen: s?.live_screen || null,
    // Gated on the PANE, not on `bridge_reachable`: the server sets that flag
    // for an SDK-owned run too (it means "regin can type into this"), but such
    // a run has no tmux pane at all — `capture_screen` would fail every time,
    // so the peek must not be offered.
    bridgeSession: s?.bridge_pane
      ? { trace_id: s.trace_id, bridge_reachable: true, bridge_pane: s.bridge_pane }
      : null,
  }
})

// A send from the span reader is the same send the feed's own composer makes,
// so it has to arm the feed's send-reveal too. Counted rather than passed as a
// boolean: the signal is the occurrence, and a flag would need resetting.
// Not counted under takeover: the feed is then one agent's subtree, and the
// turn this send produces belongs to the session, not to that subtree — the
// reveal would collapse prompts the reader opened to make room for a turn that
// never arrives there.
const sendSeq = ref(0)
function onReaderSent() {
  if (!scopeTakeover.value) sendSeq.value += 1
}

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
  followUp: followUpMeta.value,
  sendSeq: sendSeq.value,
  backgroundShells: session.value?.background_shells || [],
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

// ── Presentation mode ─────────────────────────────────────────
// A second, UNSCOPED span tree: the reader walks the whole trace in order
// (prompts, subagent children inline) regardless of what the feed has folded,
// what Focus has demoted, or which agent scope is active. Cheap — the same
// pure derivations the feed already runs, over the same span list.
const readerTree = useSpanTree(() => allSpans.value, () => turns.value, () => null)
// Selection ONLY — not `selectSpan`, whose contract is "an explicit selection
// opens the mobile sheet". The reader calls this on open, on every ↑↓ step, on
// a child click and on the live-poll heir rebind, so routing it through that
// intent stacked the bottom sheet under the reader on every phone-sized
// viewport and re-opened it faster than it could be dismissed.
const present = usePresentReader(readerTree.entries, {
  onSelect: (span) => { if (span) selectedSpan.value = span },
})
// Below lg the reader IS the full-content surface, so a sheet opened by an
// earlier card tap would sit under it as a second modal the user has to
// dismiss twice. The selection is kept either way.
watch(present.isOpen, (open) => { if (open) sheetOpen.value = false })

// The term the palette matched on, marked in the reader it hands off to.
// Cleared by every other entry route so a stale mark cannot outlive its hit.
const searchHighlight = ref('')

const presentSpan = computed(() => present.current.value?.span || null)

// Is the span in the reader still being WRITTEN?
//
// The two concessions the reader gets — a forced content re-read on every
// reconcile, and the live poll declining to suspend under it — both exist for
// one reason: a span's body grows with no identifier moving, so no tail
// fingerprint can see it. That reason expires the moment the span finishes.
// Held open on a settled span it just means a reader parked on a completed
// tool call polls the session for as long as the tab stays open.
//
// Unknown counts as growing: a detached span (one the walk cannot see) is
// rendered from a snapshot, and guessing "finished" there would freeze it.
const presentSpanGrowing = computed(() => {
  if (!present.isOpen.value) return false
  const span = presentSpan.value
  if (!span) return true
  return !(span.end_time && span.status_code !== 'PENDING')
})
// Subagent launches list their children as rows that re-key the reader. The
// trace loads SHALLOW, so an unopened agent has none until its subtree lands.
const presentChildren = computed(() => (
  presentSpan.value?.name === 'subagent.start'
    ? readerTree.childrenOf(presentSpan.value.span_id)
    : []
))
const presentTurnLabel = computed(() => {
  const id = present.presentId.value
  if (!id) return ''
  const idx = readerTree.promptGroups.value.findIndex(g =>
    g.prompt.span_id === id || g.descendants.some(d => d.span.span_id === id))
  return idx >= 0 ? `turn #${idx + 1}` : ''
})

// One bundle rather than ten binds, matching `feedProps` / `spanDetailProps`:
// the template's binding budget is a measured gate in this file.
const presentProps = computed(() => (!present.isOpen.value ? { span: null } : {
  span: presentSpan.value,
  prev: present.prev.value,
  next: present.next.value,
  position: present.readableIndex.value + 1,
  total: present.readable.value.length,
  onlyBody: present.onlyBody.value,
  skipped: present.skipped.value,
  offList: present.offList.value,
  turnLabel: presentTurnLabel.value,
  children: presentChildren.value,
  navSeq: present.navSeq.value,
  highlight: searchHighlight.value,
  followUp: followUpMeta.value,
  sessionId: session.value?.trace_id || '',
  // The turn the reply would BECOME — prompt groups, the same count the feed's
  // own composer shows; `turns` here is per-API-response billing, not turns.
  replyTurn: readerTree.promptGroups.value.length + 1,
}))

// Keyed off what the reader IS showing, not off the two moves that put it
// there: the live poll can re-bind a retired placeholder to its resolved span
// under the reader, and that arrival needs the same body as a click would.
watch(() => presentSpan.value?.span_id, (id) => {
  const span = presentSpan.value
  if (!id || !span) return
  // Fill the children list for an agent the reader opened cold.
  if (span.name === 'subagent.start') ensureSpanSubtreeLoaded(id)
  if (!spanContentCache.value.has(id)) fetchSpanContent(id)
}, { immediate: true })

function openPresent(spanOrId) { searchHighlight.value = ''; present.openPresent(spanOrId) }

// What "open" means for a span the search palette surfaced (spec §08): the
// palette closes, the span is selected, and presentation mode opens on it.
//
// Scoping comes FIRST — a span that ran inside a subagent belongs to that
// agent's feed, and entering the scope afterwards would re-render the feed
// out from under the reader. Everything after selection is already handled
// by machinery the `?span=` deep link uses: SessionConversationView's
// selection watcher unfolds the owning turn, clears the Focus tier that was
// hiding the row (`revealSpan` / `openFold`), and scrolls it into view — so
// a folded target is revealed rather than skipped, without a second opinion
// about tiers living here.
async function openSpanFromSearch(spanId, query = '') {
  if (!spanId) return
  // Hydrate FIRST. The palette's span group is answered by the server against
  // the whole session, while this view holds only the pages it has loaded — so
  // a hit is routinely a span the client has never seen, and reading
  // `agent_id` off `allSpans` before `selectSpanById` fetched its subtree read
  // `undefined` and silently skipped the scope for exactly the spans the
  // server-side search exists to reach.
  await selectSpanById(spanId)
  const span = allSpans.value.find(s => s.span_id === spanId)
  if (!span) return
  // Scoping after selection re-renders the feed under the reader, so open the
  // reader last: entering the scope is what decides WHICH feed the reader
  // steps through.
  if (span.agent_id && traceScope.scopeId !== span.agent_id) {
    await traceScope.enter(span.agent_id)
  }
  openPresent(span)
  searchHighlight.value = query
}
function movePresent(delta) { present.movePresent(delta) }

// `<router-view>` has no `:key`, so routing from one session detail page to
// another REUSES this component. Presentation and Focus both hold state keyed
// on span/turn ids of the trace that is going away: without this the reader
// kept displaying a span from the previous session over the new one — `held`
// stands forever, since a non-placeholder id resolves no heir — and the fold
// sets accumulated ids for the tab's lifetime.
watch(() => route.params.id, () => {
  present.closePresent()
  searchHighlight.value = ''
  resetForTrace()
  // Queue ids are per-runner, so a pending op from the outgoing session would
  // mask or hide an unrelated row in the incoming one.
  queued.reset()
  cancelComposerRestore()
})

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
// spans — NOT `turns.length`. A turn_usage row is one API response, so counting
// them here reported a tool-heavy turn as dozens; an em-dash while none are
// loaded is the honest reading, and the same rule governs the Activity strip's
// flags (see TraceOverviewStrip's turnBoundaries).
const promptCount = computed(() =>
  treeNodes.value.filter(t => t?.data?.name === 'prompt').length || null)

const modeCounts = computed(() => ({
  log: session.value?.span_count_total ?? allSpans.value.length,
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
useTraceScroll({
  reloading, loading, loadingOlder, hasMoreOlder, liveSyncActive,
  // The pull is a gesture, so it re-arms a suspended poll as well as
  // reloading — see `reloadFromUser`.
  reload: reloadFromUser,
  loadOlder: () => guarded(loadOlder),
  // The reader is a modal over the feed: while it owns the viewport, a scroll
  // is the operator reading a span, never a pull on a feed they cannot see.
  paused: () => present.isOpen.value,
})

// General live poll. The trace view is a live dashboard but `reload()`
// otherwise only fires on scroll/wheel — so a user parked at the bottom
// watching their session never sees updates (and any transient duplicate
// from a placeholder→anchor handoff never gets reconciled away) until they
// scroll. A lightweight visibility-gated tick keeps the reconcile
// (`reloadLiveTail`) converging the tail to the DB every few seconds.
//
// The poll is self-terminating, in two ways, because every tick costs a /map
// and a backend transcript rescan:
//
//   - A closed session (`ended_at` set — the ingest clears it again on a
//     genuine resume) stops for good once its tail has converged. An
//     already-ended session never starts the recurring poll at all; it runs one
//     bounded catch-up instead (`syncClosedSessionTail`), which is also the
//     crash-recovery path (reopening the view re-runs it).
//   - A session that is still marked live but has stopped changing SUSPENDS —
//     `ended_at` is written by the SessionEnd hook, so a session that never got
//     one would otherwise poll for as long as its tab stayed open. The way back
//     is the span ingest's own `trace_spans` frame, plus every deliberate
//     gesture. See `maybeStopOnConverge` and `wakeLivePoll`.

// Stamped when this instance is torn down. `onMounted` awaits
// `runInitialLoad()`, and the Trace shell keys the routed component by
// `$route.path` — so switching sessions while that load is still walking its
// awaits unmounts the instance mid-flight. The teardown then ran
// `stopLivePoll()` against a timer that did not exist yet, and the pending
// load armed one afterwards: an interval on a dead instance that no unmount
// can reach. `reload()` reads `route.params.id` off the live route, so each
// orphan went on polling whichever session was opened next, and they
// accumulated one per fast switch.
let disposed = false
let livePollTimer = null
const LIVE_POLL_MS = 4000
// Max reconciles the bounded catch-up will run before giving up on an
// ever-advancing tail (a still-live session mislabelled ended, say).
const CLOSED_SYNC_MAX_TICKS = 3
// newest DB id observed at the previous reconcile, for the convergence test.
let convergeAnchorId = null
// Consecutive reconciles whose tail fingerprint did not move.
let quietTicks = 0
// How many of those retire a LIVE session's poll. `ended_at` used to be the
// only thing that ever stopped it, and it is written by the SessionEnd hook —
// so a session killed, crashed or closed without one (106 of 2,489 rows in a
// year-old database) polled for as long as its tab stayed open. Counting
// unchanged reconciles rather than elapsed seconds keeps the rule on the
// tail's identifiers; the floor comes from the server, not from taste: the
// fingerprint's transcript half only moves when the live rescan runs, and that
// is gated at 10s between runs (`lib/trace/live_rescan.py`), so fewer than
// three ticks would read a working agent as quiet. Five leaves margin.
const QUIET_TICKS_BEFORE_SUSPEND = 5
function startLivePoll() {
  if (disposed || livePollTimer) return
  livePollTimer = setInterval(() => {
    if (document.hidden) return
    if (reloading.value || loading.value || loadingOlder.value) return
    reload()
  }, LIVE_POLL_MS)
}
function stopLivePoll() {
  if (livePollTimer) { clearInterval(livePollTimer); livePollTimer = null }
}

// Called after every reconcile, with whether the tail actually moved. A live
// session keeps polling while it is still changing and suspends when it is
// not. Once it has ended, stop for good — but only after the tail stops
// advancing, so the final SessionEnd flush is captured first. A count/items
// divergence (the marker says ended while spans are still landing) must NOT
// stop us on the first `ended_at`; we require one unchanged-newest tick.
function maybeStopOnConverge(tailChanged) {
  if (!session.value?.ended_at) {
    convergeAnchorId = null
    // Live (again): the ingest clears `ended_at` when a genuine resume lands,
    // so a poll that retired on the old "ended" read — and the scroll/wheel
    // pull-to-refresh retired with it — re-arms here, on whichever reload
    // first observes the cleared marker.
    liveSyncActive.value = true
    quietTicks = tailChanged ? 0 : quietTicks + 1
    // Suspending is a PAUSE, not the retirement an ended session gets: the
    // ingest's `trace_spans` frame re-arms it (`wakeLivePoll`), as does the
    // tab coming back, a pull-to-refresh or the header's Refresh. That is why
    // `liveSyncActive` stays true — the reader's own way back must keep
    // working. A reader open on a span STILL BEING WRITTEN is exempt: its
    // content grows in place, which no tail identifier can see. A finished
    // span cannot, so it earns no exemption.
    if (quietTicks >= QUIET_TICKS_BEFORE_SUSPEND && !presentSpanGrowing.value) {
      stopLivePoll()
    } else {
      startLivePoll()
    }
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

// The way back from a suspended poll.
//
// `traceId` null means the stream reconnected and cannot say which trace
// moved, or that a person asked for this (Refresh, pull-to-refresh, the tab
// coming back) — either way the answer is to look. An ingest names the
// canonical id, which is the one the header carries; a session whose spans
// land under an `sdk-` or child alias would otherwise never wake the view
// reading it.
// One pushed span frame for this session: record what it says about stream
// continuity BEFORE waking, because the wake is what triggers the refresh
// that acts on it. Frames for other sessions are dropped here rather than
// inside each consumer, so neither the continuity tally nor the poll sees
// another trace's changes.
function onSpanFrame(traceId, frame) {
  const mine = session.value?.canonical_trace_id || route.params.id
  if (traceId && traceId !== mine && traceId !== route.params.id) return
  noteSpanFrame(frame)
  wakeLivePoll(traceId)
}

// `/clear` retires this conversation and opens another in the same pane. The
// poll cannot find that heir — its spans land under a trace this view is not
// watching, and the poll is suspended anyway once the cleared session stops
// changing — so the server-pushed frame is the only signal that arrives.
useSuccessorFollow(() => session.value?.trace_id,
                   () => session.value?.successor_trace_id)

function wakeLivePoll(traceId) {
  if (disposed || loading.value || reloading.value) return
  const mine = session.value?.canonical_trace_id || route.params.id
  if (traceId && traceId !== mine && traceId !== route.params.id) return
  quietTicks = 0
  // A poll that is already running needs nothing else — its next tick is at
  // most LIVE_POLL_MS away, and resetting the count above is what keeps it
  // alive. Reloading here instead would be a loop with no cadence: every
  // /map kicks the server's transcript rescan, whose ingest raises the very
  // frame that brought us here.
  //
  // Suspended, one reload is the whole recovery: it re-arms through
  // `maybeStopOnConverge` on the way out, and on a session the ingest has
  // just un-ended it is also what observes the cleared `ended_at`.
  if (livePollTimer) return
  reload()
}

// The explicit half. A deliberate gesture — Refresh, a pull at the bottom
// edge, the tab coming back — always earns a fresh look, and buys the poll
// another full quiet budget whether or not the tail has moved.
function reloadFromUser() {
  if (disposed) return
  quietTicks = 0
  reload()
}

function onTraceVisible() {
  if (!document.hidden) reloadFromUser()
}

// A resume launched from the conversation's own Resume bar. The run continues
// under the id this page is already open on, so there is nothing to navigate
// to — what the page needs is to stop believing the session is over.
//
// The reload is the ordinary mechanism: the launch clears `ended_at`
// (`agent_sdk.store.revive_trace_session`) synchronously BEFORE the route
// answers, so it reads a live session and `maybeStopOnConverge` re-arms the
// poll on the way through.
//
// The poll is armed here as well because that reload is not guaranteed to
// happen: `reload()` no-ops while another is in flight (a pull-to-refresh, the
// tab regaining focus), and a closed session has only `syncClosedSessionTail`
// behind it, not a recurring poll — so a dropped refresh would have nothing to
// retry it and the page would sit on a Resume bar over a running session.
// I did not reproduce that window in a test; this is defensive, and free:
// `startLivePoll` is idempotent and its next tick re-derives liveness from the
// server like any other.
function onResumed() {
  liveSyncActive.value = true
  quietTicks = 0
  startLivePoll()
  reloadFromUser()
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

// Trim a raw transport error down to something renderable: api.get throws with
// the response BODY as its message, which for a 500 is a whole HTML traceback.
const ERROR_DETAIL_MAX = 200
function loadErrorText(err) {
  const raw = (err?.message || '').replace(/\s+/g, ' ').trim()
  if (!raw) return ''
  return raw.length > ERROR_DETAIL_MAX ? `${raw.slice(0, ERROR_DETAIL_MAX)}…` : raw
}

// Every /map-backed loader this view fires from an event handler — scroll,
// tab watcher, jump-to-live, poll tick — is invoked unawaited, so an escaping
// reject is an unhandled rejection and nothing else: no surface, no console
// entry, just spans that quietly stop moving. Route them all through here so
// one dead endpoint degrades the header instead.
// Only ever SETS the marker — clearing belongs to reload(), the one call that
// re-fetches the tail and can therefore vouch for the whole view being current.
// A terminal-tab fetch succeeding says nothing about the poll still failing.
async function guarded(fn) {
  try {
    await fn()
  } catch (e) {
    reloadFailed.value = true
    reloadErrorDetail.value = loadErrorText(e)
    // The tooltip carries the transport detail, but a genuine bug in the load
    // chain deserves a stack a developer can open.
    console.error('[trace] background load failed', e)
  }
}

// Stamped before the await, not after: Retry is a user-clickable control, and
// a guard set post-await lets a double-click run two initial loads at once.
let initialLoadInFlight = false

// Several screen readers do not announce a role="alert" that is inserted with
// its text already in place, so the failure pane also takes focus — which both
// announces it and puts Retry under the very next keypress.
const retryButton = ref(null)
watch(loadFailed, async (failed) => {
  if (!failed) return
  await nextTick()
  // Button renders through reka-ui's Primitive, so the instance exposes the
  // native element as $el rather than being one.
  const el = retryButton.value?.$el ?? retryButton.value
  el?.focus?.()
})

async function runInitialLoad() {
  if (initialLoadInFlight) return
  initialLoadInFlight = true
  loading.value = true
  loadFailed.value = false
  loadErrorDetail.value = ''
  // These three swallow their own rejections, so they never need awaiting on
  // the failure path.
  const rollupP = fetchToolRollup()
  const plansP = fetchPlans()
  const wfRunsP = fetchWorkflowRuns()
  try {
    await loadSession()
  } catch (e) {
    loadFailed.value = true
    loadErrorDetail.value = loadErrorText(e)
    // Nothing loaded, so there is no tail to reconcile: leaving the scroll
    // pull-to-refresh armed would just re-throw against the same dead endpoint.
    liveSyncActive.value = false
    return
  } finally {
    loading.value = false
    initialLoadInFlight = false
  }
  await Promise.all([rollupP, plansP, wfRunsP])
  if (disposed) return
  // A session that is already closed never needs the perpetual poll: run one
  // bounded catch-up (crash recovery) and stop. Live sessions keep the poll.
  if (session.value?.ended_at) {
    await syncClosedSessionTail()
    // Bounded catch-up done — no recurring poll, and the scroll/wheel
    // pull-to-refresh should not resurrect the backend rescan for a session
    // that has already ended. Reopening the view re-runs the catch-up.
    liveSyncActive.value = false
  } else {
    liveSyncActive.value = true
    startLivePoll()
  }
}

// The palette is mounted by AppLayout, a SIBLING of this view under
// <router-view>, so provide/inject cannot reach it — the registry is how the
// open trace hands over its spans and its open-a-span move. Unregistering on
// unmount matters: a stale source would let the palette offer to open spans
// of a session that is no longer on screen.
let unregisterSearchSource = null
let unregisterSpanEvents = null

// The ingest addresses its frame at the canonical id, which this view only
// learns once the session loads — so the route id is declared on mount and the
// pair once it is known, rather than leaving a window where the open session's
// own frames are addressed to nobody.
watch(() => [route.params.id, session.value?.canonical_trace_id],
      ([routeId, canonical]) =>
        setTraceInterest(onSpanFrame, [routeId, canonical]))

onMounted(async () => {
  unregisterSpanEvents = onTraceSpans(onSpanFrame)
  setTraceInterest(onSpanFrame,
                   [route.params.id, session.value?.canonical_trace_id])
  unregisterSearchSource = registerTraceSearchSource({
    traceId: route.params.id,
    spans: () => allSpans.value,
    openSpan: openSpanFromSearch,
    // The preview reads spans the same way `openSpan` does, minus the reader:
    // a server-side hit is routinely outside the pages this view has loaded.
    hydrate: loadSpanById,
  })
  await runInitialLoad()
  if (viewMode.value === 'log') guarded(ensureTerminalSpansLoaded)
  if (viewMode.value === 'messages') guarded(ensureAgentMessagesLoaded)
  // The watcher below only fires on a CHANGE, so a load that lands directly on
  // the conversation tab (its localStorage default) never fetched turn_usage —
  // leaving the Turns rail without cost/token figures and every turn without
  // its usage footer.
  if (viewMode.value === 'conversation') guarded(ensureTurnsLoaded)
  // Scroll/wheel/touch auto-reload listeners are attached by useTraceScroll();
  // the sticky-header ResizeObserver is owned by useStickyHeader.
  // Capture phase: scroll events don't bubble, and the scroller is the
  // app shell's `.content-scroll`, an ancestor of this view.
  document.addEventListener('scroll', onCollapseScroll, { capture: true, passive: true })
  document.addEventListener('keydown', onHeaderKey)
  await nextTick()
  syncScrolledDeep()
  document.addEventListener('wheel', markScrollInput, { capture: true, passive: true })
  document.addEventListener('touchmove', markScrollInput, { capture: true, passive: true })
  document.addEventListener('mousedown', onScrollbarMouseDown, { capture: true, passive: true })
  document.addEventListener('keydown', onScrollInputKey)
  document.addEventListener('visibilitychange', onTraceVisible)
})

onUnmounted(() => {
  cancelComposerRestore()
  // Scroll/wheel/touch listeners are detached by useTraceScroll(); the
  // sticky-header observer + compact poll are torn down by their composables.
  disposed = true
  unregisterSpanEvents?.()
  unregisterSpanEvents = null
  unregisterSearchSource?.()
  unregisterSearchSource = null
  stopLivePoll()
  document.removeEventListener('scroll', onCollapseScroll, { capture: true })
  document.removeEventListener('keydown', onHeaderKey)
  document.removeEventListener('wheel', markScrollInput, { capture: true })
  document.removeEventListener('touchmove', markScrollInput, { capture: true })
  document.removeEventListener('mousedown', onScrollbarMouseDown, { capture: true })
  document.removeEventListener('keydown', onScrollInputKey)
  document.removeEventListener('visibilitychange', onTraceVisible)
})

// When the user enters the Log tab (or lands on it via localStorage
// restore), fetch every span — not the shallow root-only set the other
// tabs use. Conversation tab also needs turns to render the right-rail
// timeline, so trigger that load here too.
watch(viewMode, async (mode) => {
  if (mode === 'log') {
    await guarded(ensureTerminalSpansLoaded)
  } else if (mode === 'messages') {
    await guarded(ensureAgentMessagesLoaded)
  } else if (mode === 'conversation') {
    await guarded(ensureTurnsLoaded)
  }
})

// The requests a reconcile owes on top of `/map`. Every one of them reads data
// derived from the tail, so a tick whose fingerprint did not move owes none —
// the single exception is the open reader, whose span grows in place and is
// therefore invisible to any tail identifier.
function reloadFollowUps(tailChanged) {
  const tasks = []
  // A span open in the reader may still be being written — a running Bash's
  // stdout, an assistant response mid-stream. Its content is cached
  // write-once and that cache is overlaid on top of every later /map, so
  // without an explicit re-read the reader shows the length the span had
  // when it was opened, forever, while the poll delivers the rest. Scoped to
  // the ONE span being read, and only while it can still GROW: a settled span
  // re-read every four seconds is a request that can never return anything.
  // `index >= 0` as well: a DETACHED span (one the walk cannot see, so the
  // reader is rendering a snapshot captured at open) is never re-read from
  // `allSpans`, so a forced fetch for it could not reach the screen.
  if (presentSpanGrowing.value && present.index.value >= 0) {
    tasks.push(fetchSpanContent(present.presentId.value, { force: true }))
  }
  if (!tailChanged) return tasks
  tasks.push(fetchToolRollup())
  // A scoped agent's spans live in ITS trace — nothing the parent's tail
  // reconcile can see. The roster rides every tick, so its entry is what
  // says whether the child moved (useAgentTrace.refresh keys on it).
  if (traceScope.scopedAgent) tasks.push(agentTrace.refresh(traceScope.scopedAgent))
  // Messages tab rides the same live poll: cheap per-session query.
  if (viewMode.value === 'messages') tasks.push(ensureAgentMessagesLoaded())
  // Only refetch turns if they're loaded AND visible. While folded the
  // user isn't reading them, so defer the cost; mark stale and let the
  // unfold action pull the fresh copy in.
  if (turns.value != null) {
    if (turnsCollapsed.value) turnsStale.value = true
    else tasks.push(fetchTurns())
  }
  return tasks
}

async function reload() {
  if (reloading.value) return
  reloading.value = true
  try {
    // Serialised, not raced with the rest: the tail's answer is what says
    // whether the rest is worth asking for. One extra round-trip on a tick
    // that changed, five requests saved on one that did not.
    const tailChanged = await reloadLiveTail()
    await Promise.all(reloadFollowUps(tailChanged))
    lastReloadedAt.value = new Date()
    reloadFailed.value = false
    reloadErrorDetail.value = ''
    maybeStopOnConverge(tailChanged)
  } catch (e) {
    // Swallowed on purpose: every caller but syncClosedSessionTail invokes
    // this unawaited, so a rethrow is an unhandled rejection and nothing more.
    // The poll keeps running — a /map that recovers clears the marker above.
    // Logged on the false→true edge only: this runs every 4s, so logging
    // unconditionally would bury the console during any outage — but staying
    // silent would hide a genuine bug in the reload chain behind an amber
    // tooltip that reads like a transport failure.
    if (!reloadFailed.value) console.error('[trace] reload failed', e)
    reloadFailed.value = true
    reloadErrorDetail.value = loadErrorText(e)
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
  // Jump to whatever IS loaded when the refresh fails, rather than throwing out
  // of a click handler: the marker says the tail is stale, the jump still works.
  await guarded(reloadLiveTail)
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
  // Timeline + log rows live inside data-span-id-marked tables, so
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

// Highest floor first — the find() below returns on the first one cleared.
const CTX_TONE_STEPS = [[80, 'text-red-600'], [50, 'text-amber-600']]

// Thresholds mirror TraceVitalsStrip's gauge, so a session reading "amber" in
// the strip reads amber in the collapsed header too.
function ctxTone(pct) {
  if (pct == null) return 'text-slate-400'
  return CTX_TONE_STEPS.find(([floor]) => pct >= floor)?.[1] ?? 'text-emerald-600'
}

const orDash = (value, format) => (value != null ? format(value) : '—')

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
  const spend = Number.isFinite(spendUsd) ? spendUsd : null
  return [
    { key: 'spans', value: String(s.span_count_total ?? allSpans.value.length), label: 'spans', tone: 'text-slate-700' },
    { key: 'duration', value: fmtDuration(Math.round(traceDuration.value)) || '—', label: '', tone: 'text-slate-700' },
    { key: 'tokens', value: orDash(totalTok, fmtTokens), label: 'tok', tone: 'text-slate-700' },
    { key: 'cost', value: orDash(spend, fmtCost), label: '', tone: 'text-emerald-600' },
    { key: 'ctx', value: orDash(ctx, p => `${p}%`), label: 'ctx', tone: ctxTone(ctx) },
  ]
})

// Select + scroll to a span by id, loading its (possibly collapsed)
// subtree first. Shared by the task-list jump and the tool-drill-down
// jump. If the span isn't in the loaded shallow set, walk the roots
// calling `ensureSpanSubtreeLoaded` until it materialises; the existing
// `selectedSpan` watcher then does the scroll-and-highlight.
// The fetch half of `selectSpanById`, with no view side effects at all. The
// search palette previews a span without navigating to it, so hydrating one
// must not switch the tab (which also persists), move the selection or open
// the rail — closing the palette has to leave the trace where it was.
async function loadSpanById(spanId) {
  if (!spanId) return null
  let span = allSpans.value.find(s => s.span_id === spanId)
  if (span) return span
  for (const node of treeNodes.value) {
    if (node?.data?.span_id) {
      // eslint-disable-next-line no-await-in-loop
      await ensureSpanSubtreeLoaded(node.data.span_id)
      span = allSpans.value.find(s => s.span_id === spanId)
      if (span) return span
    }
  }
  return null
}

async function selectSpanById(spanId) {
  if (!spanId) return
  setViewMode('conversation')
  const span = await loadSpanById(spanId)
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
  spanIdsInSelectedTurn, turnsDetailLoaded,
  fetchTurns, loadTurns, toggleTurnsCollapsed,
  selectTurn, toggleTurnExpanded, storeTurnRow, handleSpanRefClick,
} = useTurns(route, {
  allSpans, treeNodes, selectedSpan, selectedKeys, expandedKeys,
  ensureSpanSubtreeLoaded,
  // The turns sidebar is the only reader of a turn's `span_refs` and
  // `tool_summary`, and it renders inside the detail rail — closed by default
  // on the conversation tab, which is where turns are fetched eagerly.
  wantsDetail: () => turnsSidebarShown.value,
})

// Opening the rail is what makes those two fields worth their bytes, so the
// first open re-reads with them rather than leaving the sidebar's click-jump
// and tool line permanently missing from a lean first fetch.
watch(turnsSidebarShown, (shown) => {
  if (!shown || turns.value == null || turnsDetailLoaded.value) return
  guarded(fetchTurns)
})

</script>

<template>
  <div v-if="loading" class="empty-state">Loading session…</div>
  <div v-else-if="loadFailed" class="empty-state" data-testid="trace-load-error">
    <!-- Retry is what takes focus, so it must carry the message: on its own its
         accessible name is just "Retry", which announces the control and not
         the failure. -->
    <p id="trace-load-error-msg" class="alert alert-error" role="alert">
      Couldn’t load this session.
      <!-- break-all, not break-words: a 500 body is often one unbroken token
           (compact JSON, base64) with no break opportunity of its own. -->
      <span v-if="loadErrorDetail" class="block mt-1 text-[12px] opacity-80 break-all">{{ loadErrorDetail }}</span>
    </p>
    <Button
      ref="retryButton"
      variant="secondary"
      aria-describedby="trace-load-error-msg"
      @click="runInitialLoad"
    >Retry</Button>
  </div>
  <div v-else-if="!sessionKnown" class="empty-state">
    No spans found for this session.
  </div>
  <div
    v-else
    class="trace-detail-root"
    :class="{ 'trace-bounded': boundedLayout }"
    :style="{ '--regin-trace-header-h': headerOffset }"
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
      class="relative lg:sticky lg:-top-6 z-20 bg-white -mx-4 -mt-4 px-4 pt-4 lg:-mx-8 lg:-mt-6 lg:px-8 lg:pt-6 mb-4 border-b border-slate-200 shadow-[0_2px_4px_-2px_rgba(15,23,42,0.06)]"
      :class="headerCollapsed ? 'pb-3' : 'pb-4'"
    >
    <!-- Pull-to-reveal meter: how much of the gesture that brings the full
         header back has been paid. Deliberately OUT of flow — carried inside
         the compact row it took 132-436px of layout width away from the title
         at every width, permanently, to show something visible only during a
         gesture; measured, the title collapsed from 382px to 75px at 1920 and
         to zero at 1440. Absolute costs the row nothing.
         Both the fill's scaleX and this wrapper's opacity are written
         imperatively by the rAF loop in `useHeaderRevealPull`: routing
         per-frame progress through reactive state re-renders the whole trace
         and the page visibly shimmers. scaleX, never width — width relayouts
         every frame. The hint names the cheaper route in the same breath as
         the gesture, so the meter teaches H and then stops mattering. -->
    <div
      v-show="headerCollapsed"
      data-header-pull-meter
      data-testid="trace-header-pull-meter"
      class="pointer-events-none absolute bottom-1 left-4 z-10 hidden items-center gap-2 opacity-0 transition-opacity duration-[140ms] ease-out lg:left-8 lg:flex"
      aria-hidden="true"
    >
      <span class="inline-block h-[3px] w-[132px] overflow-hidden rounded-full bg-slate-200">
        <span
          data-header-pull-bar
          class="block h-full w-full origin-left rounded-full bg-blue-600 will-change-transform"
          style="transform: scaleX(0)"
        ></span>
      </span>
      <span class="whitespace-nowrap font-mono text-[10.5px] leading-none tracking-wide text-slate-400">
        keep pulling for session details · or press H
      </span>
    </div>
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
      :reload-failed="reloadFailed"
      :reload-error-detail="reloadErrorDetail"
      :has-turns="turns != null"
      :snapshot-stale-at="snapshotStaleAt"
      :workflow-parent-to="workflowParentTo"
      :collapsed="headerCollapsed"
      :digest="headerDigest"
      @update:view-mode="setViewMode"
      @reload="reloadFromUser"
      @jump-to-task="jumpToTaskSpan"
      @toggle-collapse="toggleHeaderDetails"
    >
      <template #actions>
        <!-- Seeded with this session's own cwd: the page already knows which
             repo the operator is looking at, and a menu of every registered
             repo would make them answer that again. -->
        <NewSessionButton
          variant="ghost"
          :initial-cwd="session?.cwd || ''"
          :scope="session?.trace_id || ''"
          @launched="onSessionLaunched"
        />
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
      data-testid="trace-compact-bar"
      class="lg:hidden sticky top-0 z-20 -mx-4 mb-3 border-b border-slate-200 bg-white px-4 py-2 shadow-[0_2px_4px_-2px_rgba(15,23,42,0.06)]"
    >
      <div class="flex items-center gap-2">
        <div class="min-w-0 flex-1 truncate text-[13px] font-semibold text-slate-800" :title="session.title || ''">
          {{ session.title || 'Session timeline' }}
        </div>
        <!-- The only way back to the session details below lg, and only once
             the full header has actually gone: it is not sticky at this width,
             so while it is still on screen its own Details button is right
             there and this would be a second one a step away. Once it scrolls
             off, both of the other routes back are gone too — the
             pull-to-reveal meter is `lg:flex`, and H is a keyboard the phone
             does not have. -->
        <Button
          v-if="headerOffscreen"
          variant="ghost"
          size="sm"
          class="h-auto shrink-0 gap-1 border border-slate-200 bg-slate-50 px-2 py-1 text-[11px] font-semibold text-slate-500"
          :title="headerCollapsed ? 'Show session details' : 'Collapse header for more transcript'"
          :aria-expanded="!headerCollapsed"
          data-testid="compact-details-toggle"
          @click="onCompactDetailsToggle"
        ><span
          aria-hidden="true"
          class="inline-block leading-none transition-transform"
          :class="headerCollapsed ? 'rotate-180' : ''"
        >⌃</span>{{ headerCollapsed ? 'Details' : 'Hide' }}</Button>
      </div>
      <!-- A scroller, not a wrapping row: a second line of tabs costs ~32px of
           transcript on every scrolled screen. The edge fade is what stops the
           clipped last tab from reading as a broken layout. -->
      <div data-testid="trace-compact-modes" class="trace-compact-modes mt-1.5 flex gap-1 overflow-x-auto">
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
         can't show as spans; served live and transient — they vanish from
         here the moment the agent dequeues them. Editable/removable per row,
         wherever the server gave the row an id. -->
    <TraceQueuedPrompts
      :items="queued.items"
      :busy-ids="queued.busyIds"
      :notice="queued.notice"
      :can-send-now="queued.modListener"
      :sending="queued.sending"
      :can-restore="followUpMeta.canFollowUp && !scopeTakeover"
      @edit="onQueueEdit"
      @restore="onQueueRestore"
      @send-now="queued.sendNow()"
      @remove="e => queued.remove(e.id)"
    />

    <div class="trace-mode-row flex flex-col lg:flex-row gap-4 lg:items-start">
      <!-- Conversation view: feed (+ ≥xl companion pane) rendered outside Card
           so the feed sidebar, the pane, and the detail rail can each be
           sticky. The region owns the responsive scope layout (takeover <xl,
           split ≥xl). -->
      <template v-if="viewMode === 'conversation'">
        <TraceConversationRegion
          :trace-scope="traceScope"
          :agent-spans="agentSpans"
          :agent-loading="agentTrace.loading"
          :live-agents="liveAgents"
          :is-xl="isXl"
          :takeover="scopeTakeover"
          :pane-visible="paneVisible"
          :hide-toc="paneVisible && !is2xl"
          :feed="feedProps"
          :sticky-top="stickyChromeHeight"
          :reader-open="present.isOpen.value"
          @present="openPresent"
          @select-span="selectSpan($event)"
          @fetch-content="fetchSpanContent"
          @load-subtree="ensureSpanSubtreeLoaded"
          @load-subtrees="ensureSpanSubtreesLoaded"
          @jump-live="jumpToLatestSpan"
          @enter-scope="traceScope.enter($event)"
          @exit="traceScope.exit()"
          @expand="setScopeMode('full')"
          @follow-latest="collapseHeaderForFollow"
          @resumed="onResumed"
        />
      </template>

      <template v-else>
        <!-- Timeline / log / pane / messages share one region: bounded, the card
             IS the scroller, so the same helper finds it as finds the feed. -->
        <Card
          :no-padding="true"
          class="trace-content-card flex-1 min-w-0 w-full"
          :data-trace-scroller="boundedLayout ? '' : undefined"
        >
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

          <!-- Log view: flat span log -->
          <template v-else-if="viewMode === 'log'">
            <SessionTerminalLog
              :spans="allSpans"
              :turns="turns"
              :selected-span="selectedSpan"
              @select-span="selectSpan($event)"
              @fetch-content="fetchSpanContent"
              @load-subtree="ensureSpanSubtreeLoaded"
            />
          </template>

          <!-- Terminal view: a real PTY on the session's tmux pane -->
          <template v-else-if="viewMode === 'pane'">
            <div class="h-full min-h-[28rem]">
              <SessionTerminalPane
                :trace-id="String(route.params.id)"
                :popout="true"
              />
            </div>
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
        class="sticky self-start shrink-0 z-10 h-7 w-7 p-0 rounded-md border border-slate-200 bg-white text-slate-500 hover:text-slate-700 hover:border-slate-300 transition-colors"
        :style="{ top: stickyHeaderHeight ? `calc(${stickyHeaderHeight}px - 1rem)` : '5rem' }"
        title="Show span details"
        aria-label="Show span details"
        @click="detailRailOpen = true"
      >
        <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <polyline points="15 18 9 12 15 6" />
        </svg>
      </Button>
      <!-- ≥lg only: below lg span details render as the bottom sheet at the
           end of this template instead of a stacked-below-the-feed aside
           the user would never see. -->
      <aside
        v-if="turnsSidebarShown"
        class="trace-detail-rail w-full lg:shrink-0 lg:sticky lg:self-start lg:overflow-y-auto z-10"
        :style="boundedLayout ? null : {
          /* Page header is sticky-pinned with top: -1.5rem (lg padding-top)
             so its background covers .content-scroll padding-top. The
             sidebar pins flush under it: `header_h - 1.5rem + small gap`. */
          top: stickyHeaderHeight ? `calc(${stickyHeaderHeight}px - 1rem)` : '5rem',
          maxHeight: stickyHeaderHeight ? `calc(100vh - ${stickyHeaderHeight}px - 2rem)` : 'calc(100vh - 6rem)',
        }"
      >
        <div v-if="viewMode === 'conversation'" class="flex justify-end gap-1.5 mb-2">
          <Button
            v-if="selectedSpan"
            variant="ghost"
            class="gap-1 px-2 py-1 h-auto rounded-md border border-slate-200 bg-white text-[11px] font-medium text-slate-500 hover:text-slate-700 hover:border-slate-300 transition-colors"
            aria-label="Open full content"
            title="Read this span in full (P)"
            @click="openPresent(selectedSpan)"
          >⤢ Present</Button>
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
      <!-- `bg-slate-900/40` INVERTS under the dark theme (the whole slate ramp
           does), so the dim scrim became a near-white veil over the page.
           `--scrim-rgb` is the token every other modal layer moved to; this
           teleported sheet — the one that stacks directly under the
           presentation reader — was the surface that sweep missed. -->
      <div
        v-if="!isLgUp && sheetOpen && selectedSpan && viewMode !== 'messages'"
        class="fixed inset-0 z-40 cursor-pointer transition-colors bg-[rgba(var(--scrim-rgb),0.4)] hover:bg-[rgba(var(--scrim-rgb),0.45)]"
        @click.self="sheetOpen = false"
      >
        <div class="absolute inset-x-0 bottom-0 flex max-h-[75vh] cursor-auto flex-col rounded-t-xl bg-surface shadow-2xl">
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
    <SpanPresentReader
      v-bind="presentProps"
      @close="present.closePresent()"
      @sent="onReaderSent"
      @move="movePresent"
      @open-child="openPresent"
      @toggle-only-body="present.toggleOnlyBody()"
    />

    <!-- Infinite-feed-style footer: a quiet end-of-timeline marker, nothing
         else. Reload progress is signalled only by the header's Reload button
         (spinning icon + disabled); a second indicator down here blinked in and
         out on every live-poll tick, which read as the page flickering.
         `pb-20` below lg keeps the last rows scrollable clear of the fixed
         "Follow latest" pill. -->
    <div class="trace-page-footer mt-8 mb-4 pb-20 lg:pb-0 flex items-center justify-center text-slate-400">
      <!-- Fixed-height row: parked at the bottom of a live session, any
           per-poll height change here clamped the scroll and the feed twitched
           up/down every few seconds. -->
      <span class="inline-flex items-center justify-center gap-2 h-4 text-[11px] tracking-wider uppercase">
        <!-- The conversation feed prints its own end-of-timeline marker at the
             bottom of the feed COLUMN (where the design puts it); printing this
             page-wide one too would show the reader two of them. The row itself
             stays so the fixed footer height survives. -->
        {{ viewMode === 'conversation' ? '' : 'End of timeline' }}
      </span>
    </div>

    <!-- Back to top takes whichever bottom corner is genuinely free, which is
         not the same one at every width:
           <lg  the sidebar is a drawer, so bottom-LEFT is empty — and the
                right column is full (the quick-capture bubble, with the
                follow-up composer's resting bubble stacked over it).
           ≥lg  the sidebar owns the left corner and the button lands on its
                nav rows, so it goes right, one tier above the quick-capture
                bubble. `--corner-fab-*` (style.css) is that bubble's own box
                and inset, so the clearance is by construction rather than by a
                constant to re-guess whenever either moves — which is how the
                two came to overlap at 375 and at 1440 alike. The composer's
                bubble is inset from the viewport edge at this width (the feed
                column is), so it does not join the stack.
         The centred "Follow latest" pill keeps the middle of the same band. -->
    <Button
      v-if="scrolledDeep"
      variant="ghost"
      class="fixed bottom-[calc(var(--corner-fab-inset)+max(var(--trace-rail-h,0px),var(--trace-dock-h,0px),var(--blocker-bar-h,0px)))] left-[var(--corner-fab-inset)] lg:left-auto lg:right-[var(--corner-fab-inset)] lg:bottom-[calc(var(--corner-fab-inset)+var(--corner-fab-size)+var(--corner-fab-gap)+max(var(--trace-rail-h,0px),var(--trace-dock-h,0px),var(--blocker-bar-h,0px)))] z-20 h-auto gap-1.5 rounded-full border border-slate-300 bg-white px-3 py-1.5 text-[12px] font-medium text-slate-600 shadow-lg hover:border-slate-400 hover:bg-slate-50 hover:text-slate-800 focus-visible:outline-2 focus-visible:outline-blue-500"
      data-testid="trace-back-to-top"
      title="Back to the top of this session"
      @click="scrollToTop"
    >
      <span aria-hidden="true">↑</span>
      <span>Top</span>
    </Button>
  </div>
</template>

<style scoped>
/* Bounded layout: the header keeps its place and everything below it fills the
   leftover height, each pane scrolling inside itself. `min-height: 0` on every
   link of the chain, or a pane grows to its content and there is nothing left
   to scroll. */
.trace-bounded {
  display: flex;
  flex-direction: column;
  min-height: 0;
}
.trace-bounded > * { flex-shrink: 0; }
.trace-bounded .trace-mode-row {
  flex: 1;
  min-height: 0;
  align-items: stretch;
}
/* Each pane ends at its own visible bottom edge now, so a page-wide end marker
   would just be a permanent bar under them. */
.trace-bounded .trace-page-footer { display: none; }
.trace-bounded .trace-detail-rail {
  position: static;
  align-self: stretch;
  max-height: none;
}

/* Narrower than the feed's own floor allows below 1120px, so the rail, the
   mini TOC and a readable chat column all fit without a sideways scroll. */
@media (min-width: 1024px) {
  .trace-detail-rail { width: 340px; }
}
@media (min-width: 1024px) and (max-width: 1119px) {
  .trace-detail-rail { width: 296px; }
}

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
/* Bounded, the card is the scroll region itself — a sticky descendant resolves
   to it, which is the box that moves. */
.trace-detail-root.trace-bounded :deep(.trace-content-card.card) {
  overflow: auto !important;
  min-height: 0;
}

/* The mode rail holds more tabs than a phone is wide. Scrolling is the right
   answer (a second row costs transcript on every screen), but a hard clip
   reads as a broken layout rather than as "there is more" — the fade is what
   says which. `scrollbar-width: none` because an overlay scrollbar on a strip
   this short covers the tab labels it is meant to describe. */
.trace-compact-modes {
  scrollbar-width: none;
  -webkit-mask-image: linear-gradient(to right, #000 calc(100% - 22px), transparent 100%);
  mask-image: linear-gradient(to right, #000 calc(100% - 22px), transparent 100%);
}
.trace-compact-modes::-webkit-scrollbar { display: none; }
</style>
