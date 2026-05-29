<script setup>
import { ref, onMounted, onUnmounted, computed, watch, nextTick } from 'vue'
import { useRoute } from 'vue-router'
import api from '../api'
import Card from '../components/Card.vue'
import MarkdownContent from '../components/MarkdownContent.vue'
import SessionTerminalLog from '../components/SessionTerminalLog.vue'
import SessionConversationView from '../components/SessionConversationView.vue'
import SuppressButton from '../components/triggers/SuppressButton.vue'
import TreeIndent from '../components/TreeIndent.vue'
import TreeTable from 'primevue/treetable'
import Column from 'primevue/column'
import { fmtTokens, toolDisplayLabel, mcpParts } from '../utils/traceFormatters.js'

const route = useRoute()
const session = ref(null)
const loading = ref(true)
const reloading = ref(false)
const lastReloadedAt = ref(null)
const selectedSpan = ref(null)
// Trigger rows for the currently-selected rule.check span, keyed by
// rule_id. Lets the applicable_rules list bind a 🔇 button to each
// row (the span attributes don't carry trigger ids). Refetched on
// selection change and after every suppress/unsuppress.
const ruleTriggersByRuleId = ref({})
const currentUser = api.getStoredUser ? api.getStoredUser() : null
const canSuppressRule = computed(() => {
  const role = currentUser?.role
  return role === 'admin' || role === 'editor'
})

async function loadTriggersForSelectedSpan() {
  if (!selectedSpan.value || selectedSpan.value.name !== 'rule.check') {
    ruleTriggersByRuleId.value = {}
    return
  }
  const spanId = selectedSpan.value.span_id
  try {
    const data = await api.get(`/triggers/by-span/${encodeURIComponent(spanId)}`)
    const map = {}
    for (const t of data?.triggers || []) map[t.rule_id] = t
    ruleTriggersByRuleId.value = map
  } catch {
    ruleTriggersByRuleId.value = {}
  }
}

const promptExpanded = ref(false)
const sessionTitleExpanded = ref(false)
const expandedKeys = ref({})
const selectedKeys = ref({})   // PrimeVue TreeTable v-model:selection-keys
const treeNodes = ref([])
const loadingChildren = ref(new Set())

// Sticky page header: everything that frames the trace (title row, tokens
// rollup, mini-timeline, more-history banner) pins to the top of the scroll
// container so the user keeps navigation context while scrolling a long
// span list. Sidebar's sticky offset must match this height, so we measure
// the rendered header with a ResizeObserver and expose it as a CSS var.
const stickyHeaderEl = ref(null)
const stickyHeaderHeight = ref(0)
let stickyHeaderRO = null

// ── Pagination state ────────────────────────────────────────
// Initial load + load-older + scroll-to-bottom reload all walk a
// turn-anchor cursor (DB `id`). PAGE_SIZE matches the backend default
// clamp; bumping it on the client is free as long as the server
// allows it (clamped to 200 in trace.py).
const PAGE_SIZE = 50
const hasMoreOlder = ref(false)
const oldestLoadedId = ref(null)
const newestLoadedId = ref(null)
const loadingOlder = ref(false)

// Content cache: span_id -> attributes dict. Fetched on-demand via
// /api/sessions/:trace_id/spans/:span_id/content.
const spanContentCache = ref(new Map())

// View mode: 'conversation' | 'timeline' | 'terminal'
//
// Resolution order: `?view=<mode>` query param > localStorage > default.
// The query param is honored without writing to localStorage so deep
// links from /trace/triggers (which always force conversation view so
// the surrounding prompt is visible) don't clobber the user's chosen
// default for casually-opened sessions.
const VIEW_MODE_KEY = 'regin_session_view_mode'
const VALID_VIEW_MODES = ['conversation', 'timeline', 'terminal']
function _initialViewMode() {
  const fromQuery = route.query.view
  if (typeof fromQuery === 'string' && VALID_VIEW_MODES.includes(fromQuery)) {
    return fromQuery
  }
  return localStorage.getItem(VIEW_MODE_KEY) || 'conversation'
}
const viewMode = ref(_initialViewMode())
function setViewMode(mode) {
  viewMode.value = mode
  localStorage.setItem(VIEW_MODE_KEY, mode)
}
// Terminal tab needs the FULL span list (not shallow), so the flat-log
// view can render every event chronologically. Other tabs stay shallow
// to keep the initial fetch cheap on large sessions. We fetch the full
// map once per session and merge it into the shared `session.value.spans`.
const terminalSpansLoaded = ref(false)
const terminalLoading = ref(false)
async function ensureTerminalSpansLoaded() {
  if (terminalSpansLoaded.value || terminalLoading.value) return
  if (!session.value) return
  terminalLoading.value = true
  try {
    const data = await api.get(`/sessions/${route.params.id}/map`)
    mergeLoadedSpans(data.spans || [])
    terminalSpansLoaded.value = true
  } finally {
    terminalLoading.value = false
  }
}
// Dynamic-workflow runs nest their objective/phases/agents/turns under the
// `session.start` run root, which the shallow conversation load doesn't
// descend into. Deep-load the whole run subtree once (with attributes) so
// the Conversation tab can project it into phase-sectioned chat.
const workflowSpansLoaded = ref(false)
async function ensureWorkflowSpansLoaded() {
  if (workflowSpansLoaded.value) return
  const root = (session.value?.spans || []).find(
    (s) => s.name === 'session.start' && s.attributes?.agent_type === 'workflow',
  )
  if (!root) return
  workflowSpansLoaded.value = true
  try {
    const data = await api.get(
      `/sessions/${route.params.id}/spans/${root.span_id}/children?deep=1`,
    )
    mergeLoadedSpans(data.spans || [])
  } catch (_) {
    workflowSpansLoaded.value = false  // allow a later retry (e.g. reload)
  }
}
// Plans this session authored/edited — surfaced in the header as
// clickable chips so the reader can pivot from session → plan in one
// click. Populated from `plan_sessions` rows scoped to this trace_id.
// When N≥2 the chip collapses to `plans N` with click-to-expand
// (mirrors the tasks summary directly above); N=1 stays inline.
const plans = ref([])
const plansExpanded = ref(false)

// Dynamic-workflow runs this session launched (its `tool.Workflow` calls,
// each stamped with workflow_run_id + name at ingest). Surfaced as a
// header pivot chip mirroring `plans`: N=1 inlines as `⚙ <name>` linking
// to the run; N≥2 collapses to `workflows N` with click-to-expand.
const workflowRuns = ref([])
const workflowRunsExpanded = ref(false)

const turns = ref(null)  // lazy-loaded via /api/sessions/:id/turn-usage
const turnsLoading = ref(false)
const turnsCollapsed = ref(false)       // fold the loaded turns list back up
const turnsStale = ref(false)            // a reload happened while folded; refetch on unfold
const selectedTurnUuid = ref(null)      // which turn row is active
const expandedTurnUuid = ref(null)      // which turn row is drilled down
const turnRowRefs = {}                  // turn_uuid → <tr> element, for scroll-into-view
// Guard: when the user clicks a turn, we also set `selectedSpan` to
// the turn's owning prompt so the details panel + strip ring move.
// The span→turn watcher would otherwise map that prompt back to its
// *first* turn (prompts span multiple turns; `turnForSpan` picks the
// earliest) and silently overwrite the turn the user just picked.
// This flag pauses that watcher for one microtask window.
let suppressSpanToTurnSync = false

// Scroll-to-bottom auto-reload. The reload button at the top of the
// page is far away when the user is reading new spans at the bottom;
// instead, scrolling to within 64px of the bottom of any scrollable
// element on this page (or the window) triggers the same reload().
// We listen at the document level in capture phase so we catch scroll
// events from ANY scroll container — `.content-scroll`, the window,
// nested panels, all of them — without having to guess which ancestor
// is actually doing the scrolling. The `bottomLatch` keeps it
// edge-triggered: parking at the bottom with no new spans won't spam.
let bottomLatch = false
let topLatch = false
let lastWheelReloadAt = 0

// Trigger when within this many px of the bottom (or top, for
// load-older). Manual mouse-wheel or trackpad scrolling often stops
// short of the absolute edge; a generous threshold makes the
// affordance feel responsive instead of requiring users to scroll to
// the exact pixel.
const BOTTOM_THRESHOLD_PX = 240
const TOP_THRESHOLD_PX = 240
// Minimum ms between wheel-triggered reloads. Without this, sustained
// downward wheel motion while parked at the bottom would fire reload
// repeatedly back-to-back. Same cooldown applies to load-older
// wheel-up overscroll.
const WHEEL_RELOAD_COOLDOWN_MS = 1500

function onAnyScroll(e) {
  const t = e.target
  const el = (t === document || t === document.documentElement)
    ? (document.scrollingElement || document.documentElement)
    : t
  if (!el || typeof el.scrollHeight !== 'number') return
  const distBottom = el.scrollHeight - el.scrollTop - el.clientHeight
  if (distBottom <= BOTTOM_THRESHOLD_PX) {
    if (!bottomLatch && !reloading.value && !loading.value) {
      bottomLatch = true
      reload()
    }
  } else {
    bottomLatch = false
  }
  const distTop = el.scrollTop
  if (distTop <= TOP_THRESHOLD_PX && hasMoreOlder.value) {
    if (!topLatch && !loadingOlder.value && !loading.value) {
      topLatch = true
      loadOlder()
    }
  } else {
    topLatch = false
  }
}

// Companion to onAnyScroll for the "already parked at the bottom"
// case. When the user is at the absolute bottom, no scroll event
// fires no matter how much they wheel — the scrollTop has nowhere to
// go. We listen for `wheel` (and `touchmove`) events instead, and if
// the gesture is *downward* while we're already at the bottom of any
// scrollable container under the cursor, treat it as an overscroll
// "pull to refresh" gesture and fire reload.
function _findScrollerNearTarget(el) {
  while (el && el !== document.body && el !== document.documentElement) {
    if (el.scrollHeight && el.clientHeight && el.scrollHeight > el.clientHeight) {
      const style = getComputedStyle(el)
      if (/(auto|scroll)/.test(style.overflowY)) return el
    }
    el = el.parentElement
  }
  return document.scrollingElement || document.documentElement
}

function onAnyWheel(e) {
  const scroller = _findScrollerNearTarget(e.target)
  if (!scroller) return
  const now = Date.now()
  if (e.deltaY > 0) {
    // Wheel-down at the absolute bottom → reload (pull-to-refresh).
    const dist = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight
    if (dist > 4) return
    if (reloading.value || loading.value) return
    if (now - lastWheelReloadAt < WHEEL_RELOAD_COOLDOWN_MS) return
    lastWheelReloadAt = now
    reload()
  } else if (e.deltaY < 0) {
    // Wheel-up at the absolute top → load older.
    if (scroller.scrollTop > 4) return
    if (loadingOlder.value || loading.value) return
    if (!hasMoreOlder.value) return
    if (now - lastWheelReloadAt < WHEEL_RELOAD_COOLDOWN_MS) return
    lastWheelReloadAt = now
    loadOlder()
  }
}

let lastTouchY = null
function onTouchStart(e) {
  if (e.touches && e.touches.length) lastTouchY = e.touches[0].clientY
}
function onTouchMove(e) {
  if (!e.touches || !e.touches.length || lastTouchY == null) return
  const y = e.touches[0].clientY
  const deltaY = lastTouchY - y  // positive = swiping content up = scrolling down
  lastTouchY = y
  if (deltaY === 0) return
  onAnyWheel({ deltaY, target: e.target })
}

onMounted(async () => {
  const rollupP = fetchToolRollup()
  const plansP = fetchPlans()
  const wfRunsP = fetchWorkflowRuns()
  await loadSession()
  loading.value = false
  await Promise.all([rollupP, plansP, wfRunsP])
  if (viewMode.value === 'terminal') ensureTerminalSpansLoaded()
  // Capture phase so we see events from descendants like `.content-scroll`;
  // scroll events don't bubble, so a bubbling listener on document never
  // fires for nested scroll containers.
  document.addEventListener('scroll', onAnyScroll, { capture: true, passive: true })
  document.addEventListener('wheel', onAnyWheel, { capture: true, passive: true })
  document.addEventListener('touchstart', onTouchStart, { capture: true, passive: true })
  document.addEventListener('touchmove', onTouchMove, { capture: true, passive: true })
  // Wait for the v-else branch to render the sticky header element before
  // attaching the observer (the empty-state branches render different DOM).
  await nextTick()
  attachStickyHeaderObserver()
})

onUnmounted(() => {
  document.removeEventListener('scroll', onAnyScroll, { capture: true })
  document.removeEventListener('wheel', onAnyWheel, { capture: true })
  document.removeEventListener('touchstart', onTouchStart, { capture: true })
  document.removeEventListener('touchmove', onTouchMove, { capture: true })
  if (stickyHeaderRO) { stickyHeaderRO.disconnect(); stickyHeaderRO = null }
  stopCompactWatch()
})

// While a `compact.pre` exists without a matching later `compact.post`,
// the compaction is in flight in the terminal session. Without a poll,
// the boundary marker upgrade (COMPACTING → COMPACTED) only lands on
// manual refresh because the user is typically parked at the bottom of
// the trace view and no scroll fires the auto-reload latch.
let compactWatchTimer = null
let compactWatchStartedAt = 0
const COMPACT_POLL_MS = 3000
const COMPACT_POLL_MAX_MS = 5 * 60 * 1000

function awaitingCompactPost(spans) {
  if (!spans?.length) return false
  let latestPre = -Infinity
  let latestPost = -Infinity
  for (const s of spans) {
    if (s.name === 'compact.pre') {
      const t = new Date(s.start_time).getTime()
      if (t > latestPre) latestPre = t
    } else if (s.name === 'compact.post') {
      const t = new Date(s.start_time).getTime()
      if (t > latestPost) latestPost = t
    }
  }
  return latestPre > latestPost
}

function stopCompactWatch() {
  if (compactWatchTimer) {
    clearInterval(compactWatchTimer)
    compactWatchTimer = null
  }
}

function attachStickyHeaderObserver() {
  if (stickyHeaderRO || !stickyHeaderEl.value) return
  const measure = () => {
    if (stickyHeaderEl.value) {
      stickyHeaderHeight.value = stickyHeaderEl.value.getBoundingClientRect().height
    }
  }
  measure()
  stickyHeaderRO = new ResizeObserver(measure)
  stickyHeaderRO.observe(stickyHeaderEl.value)
}

// Re-attach when loading finishes and the v-else branch renders the
// sticky element (initial onMounted may fire before session data is in).
watch(loading, async (isLoading) => {
  if (!isLoading) {
    await nextTick()
    attachStickyHeaderObserver()
  }
})

// When the user enters the Terminal tab (or lands on it via localStorage
// restore), fetch every span — not the shallow root-only set the other
// tabs use. Conversation tab also needs turns to render the right-rail
// timeline, so trigger that load here too.
watch(viewMode, async (mode) => {
  if (mode === 'terminal') {
    await ensureTerminalSpansLoaded()
  } else if (mode === 'conversation') {
    if (turns.value == null && !turnsLoading.value) {
      turnsLoading.value = true
      try { await fetchTurns() } finally { turnsLoading.value = false }
    }
  }
})

// Load the initial page of the session.
// Paginated by turn anchor (latest PAGE_SIZE prompts). The
// scroll-to-bottom additive reload and scroll-to-top load-older
// extend this incrementally — see `reload()` and `loadOlder()`.
async function loadSession() {
  const prevSelectedId = selectedSpan.value?.span_id

  const data = await api.get(
    `/sessions/${route.params.id}/map?shallow=1&limit=${PAGE_SIZE}`,
  )
  session.value = { ...data, spans: data.spans || [] }
  treeNodes.value = data.tree || []
  hasMoreOlder.value = !!data.has_more_older
  oldestLoadedId.value = data.oldest_loaded_id ?? null
  newestLoadedId.value = data.newest_loaded_id ?? null
  workflowSpansLoaded.value = false
  await ensureWorkflowSpansLoaded()

  if (prevSelectedId) {
    const fresh = allSpans.value.find(s => s.span_id === prevSelectedId)
    // Default to the LATEST root (most recent prompt) — chat-style.
    selectedSpan.value = fresh
      || allSpans.value[allSpans.value.length - 1]
      || null
  } else if (allSpans.value.length) {
    selectedSpan.value = allSpans.value[allSpans.value.length - 1]
  }

  // `?span=<id>` deep-link wins over the chat-style default — used by
  // the /trace/triggers drawer to jump to the exact PostToolUse span
  // that recorded a rule trigger.
  await applyDeepLinkSpan()
}

async function applyDeepLinkSpan() {
  const target = route.query.span
  if (!target) return
  let hit = allSpans.value.find(s => s.span_id === target)
  // Shallow load returns root prompts only — rule.check and other
  // nested spans aren't in allSpans until their owning subtree is
  // expanded. Resolve the root via /ancestors, then load just that
  // subtree (cheaper than the full-map fetch on large sessions).
  if (!hit) {
    try {
      const a = await api.get(
        `/sessions/${route.params.id}/spans/${encodeURIComponent(target)}/ancestors`,
      )
      const rootId = a?.root_span_id
      if (rootId) {
        await ensureSpanSubtreeLoaded(rootId)
        hit = allSpans.value.find(s => s.span_id === target)
      }
    } catch (e) {
      // Span genuinely missing or transient error — fall through to
      // the no-op return below; the page still renders with the
      // chat-style default selection.
    }
  }
  if (!hit) return
  selectedSpan.value = hit
  nextTick(() => scrollSpanRowIntoView(hit.span_id))
}

// Live link from rule-trigger event rows: changing ?span= without
// changing /sessions/<id>/ keeps the user on the same session and
// just re-selects + scrolls.
watch(() => route.query.span, (v) => {
  if (v) applyDeepLinkSpan()
})

// When the user lands on (or away from) a rule.check span, refresh the
// trigger map so the applicable_rules list can bind 🔇 buttons.
watch(() => selectedSpan.value?.span_id, loadTriggersForSelectedSpan)

// Merge the session-summary fields the backend returns alongside the
// span page into `session.value`. The header (peak tokens, context %,
// active %, span count, title, ended_at, etc.) all read from these
// fields — without this merge the header stays frozen at initial-load
// values even though every paginated reload re-queries them server-side.
const SESSION_SUMMARY_KEYS = [
  'model',
  'input_tokens', 'output_tokens',
  'cache_read_tokens', 'cache_creation_tokens',
  'peak_context_tokens', 'peak_main_context_tokens',
  'context_window_tokens', 'context_pct', 'context_pct_all',
  'total_tokens',
  'active_work_ms',
  'started_at', 'ended_at', 'last_seen',
  'title', 'title_source',
  'span_count_total',
  'task_list',
]
function applySessionSummary(data) {
  if (!session.value || !data) return
  const patch = {}
  for (const k of SESSION_SUMMARY_KEYS) {
    if (k in data) patch[k] = data[k]
  }
  session.value = { ...session.value, ...patch }
}

// Refresh the deep subtrees of the most recent N roots. Just refreshing
// the single latest one misses spans that land under an *older* prompt
// after the user has moved on — e.g. an Agent the model spawned earlier
// finishes in the background and its tool.* / assistant_response spans
// arrive minutes later, owned by the previous prompt. Same race fires
// when a stop_hook_summary or delayed PostToolUse lands after the next
// UserPromptSubmit. Refreshing the trailing window catches both without
// re-fetching the whole tree every reload.
const RELOAD_DEEP_REFRESH_TAIL = 3

async function refreshRecentRootSubtrees(roots) {
  if (!roots.length) return
  const sorted = [...roots].sort((a, b) =>
    new Date(a.data.start_time) - new Date(b.data.start_time)
  )
  const tail = sorted.slice(-RELOAD_DEEP_REFRESH_TAIL)
  for (const node of tail) {
    if (!node?.data?.span_id) continue
    // Don't trust the cached `leaf` flag here. It's a snapshot from the
    // initial shallow load — a prompt that was childless then may have
    // accumulated tool calls / assistant_response spans since. Without
    // this fetch, a single-prompt session whose children arrive after
    // page load never picks them up (no new root → after_id query is
    // empty → and the leaf-guard skipped the only existing root).
    // withNodeChildren re-derives leaf from the response, so a genuinely
    // empty subtree just stays leaf=true.
    const spanId = node.data.span_id
    try {
      const data = await api.get(`/sessions/${route.params.id}/spans/${spanId}/children?deep=1`)
      mergeLoadedSpans(data.spans || [])
      treeNodes.value = withNodeChildren(treeNodes.value, spanId, data.children || [])
    } catch (_) {
      // One stale prompt shouldn't abort the whole reload — keep going.
    }
  }
}

// Additive reload: pull any roots that arrived after the current
// `newestLoadedId`, append them to the tree, then deep-refresh the
// latest root's subtree (the active prompt may still be mid-response).
// Earlier prompts are immutable so nothing else needs touching.
async function reloadAppendNew() {
  if (newestLoadedId.value == null) {
    // No cursor yet (initial load failed?) — fall back to a full
    // initial load so we never strand the view with stale state.
    await loadSession()
    return
  }
  const data = await api.get(
    `/sessions/${route.params.id}/map?shallow=1&after_id=${newestLoadedId.value}`,
  )
  const incoming = data.tree || []
  mergeLoadedSpans(data.spans || [])
  applySessionSummary(data)

  if (incoming.length) {
    // De-dupe by key, then append. New prompts arrive in ASC order
    // from the backend, so just push them on the end.
    const seen = new Set(treeNodes.value.map(n => n.key))
    const fresh = incoming.filter(n => !seen.has(n.key))
    if (fresh.length) {
      treeNodes.value = [...treeNodes.value, ...fresh]
    }
    if (data.newest_loaded_id != null) {
      newestLoadedId.value = data.newest_loaded_id
    }
  }

  // Deep-refresh the recent tail of prompts regardless of whether new
  // roots arrived — children may be streaming in under the active
  // prompt OR under a prior prompt whose subagent finished late.
  await refreshRecentRootSubtrees(treeNodes.value)
}

// Reverse-pagination: pull the next page of older roots and prepend
// them to the tree. Preserves the user's scroll position by
// capturing scrollTop + scrollHeight before the DOM mutation and
// restoring scrollTop after — otherwise the viewport jumps as new
// content is inserted above the current scrollTop.
async function loadOlder() {
  if (loadingOlder.value || !hasMoreOlder.value) return
  if (oldestLoadedId.value == null) return
  loadingOlder.value = true
  // Capture pre-mutation scroll state from the actual content viewport.
  const scroller = document.querySelector('.content-scroll')
    || document.scrollingElement
    || document.documentElement
  const prevScrollHeight = scroller.scrollHeight
  const prevScrollTop = scroller.scrollTop
  try {
    const data = await api.get(
      `/sessions/${route.params.id}/map?shallow=1&limit=${PAGE_SIZE}&before_id=${oldestLoadedId.value}`,
    )
    const incoming = data.tree || []
    mergeLoadedSpans(data.spans || [])
    applySessionSummary(data)
    if (incoming.length) {
      const seen = new Set(treeNodes.value.map(n => n.key))
      const fresh = incoming.filter(n => !seen.has(n.key))
      if (fresh.length) {
        treeNodes.value = [...fresh, ...treeNodes.value]
      }
    }
    hasMoreOlder.value = !!data.has_more_older
    if (data.oldest_loaded_id != null) {
      oldestLoadedId.value = data.oldest_loaded_id
    }
    // Wait for DOM update, then restore visible scroll position so
    // the user's viewport stays anchored to what they were reading.
    await nextTick()
    const delta = scroller.scrollHeight - prevScrollHeight
    if (delta > 0) {
      scroller.scrollTop = prevScrollTop + delta
    }
  } finally {
    loadingOlder.value = false
  }
}

async function reload() {
  if (reloading.value) return
  reloading.value = true
  try {
    const tasks = [reloadAppendNew(), fetchToolRollup()]
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
  await reloadAppendNew()
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

async function toggleTurnsCollapsed() {
  turnsCollapsed.value = !turnsCollapsed.value
  if (!turnsCollapsed.value && turnsStale.value && !turnsLoading.value) {
    turnsStale.value = false
    turnsLoading.value = true
    try {
      await fetchTurns()
    } finally {
      turnsLoading.value = false
    }
  }
}

const allSpans = computed(() => {
  if (!session.value) return []
  const spans = session.value.spans || []
  return spans.map(s => {
    const cached = spanContentCache.value.get(s.span_id)
    return { ...s, attributes: cached || s.attributes || {} }
  })
})

// The run-root session.start span for a dynamic-workflow run (null for
// ordinary sessions). Source of the parent backlink below; the run's name
// already shows as the session title, so it's not repeated as a chip.
const workflowRoot = computed(() => allSpans.value.find(
  s => s.name === 'session.start' && s.attributes?.agent_type === 'workflow',
) || null)

// Backlink target: the Claude Code session (and exact tool.Workflow span)
// this run was launched from. `parent_trace_id` is stamped on the run root
// at ingest; `parent_span_id` is added once the launching tool call is
// matched, so the chip deep-links straight to that call when available.
const workflowParent = computed(() => {
  const a = workflowRoot.value?.attributes
  if (!a?.parent_trace_id) return null
  return { traceId: a.parent_trace_id, spanId: a.parent_span_id || null }
})
const workflowParentTo = computed(() => {
  const p = workflowParent.value
  if (!p) return null
  return p.spanId
    ? { path: `/trace/sessions/${p.traceId}`, query: { span: p.spanId } }
    : { path: `/trace/sessions/${p.traceId}` }
})

// Drive `compact.pre → compact.post` polling off the live span set.
watch(allSpans, (spans) => {
  if (!awaitingCompactPost(spans)) {
    stopCompactWatch()
    return
  }
  if (compactWatchTimer) return
  compactWatchStartedAt = Date.now()
  compactWatchTimer = setInterval(() => {
    if (Date.now() - compactWatchStartedAt > COMPACT_POLL_MAX_MS) {
      stopCompactWatch()
      return
    }
    if (!awaitingCompactPost(allSpans.value)) {
      stopCompactWatch()
      return
    }
    if (reloading.value || loading.value) return
    reload()
  }, COMPACT_POLL_MS)
}, { immediate: true })

const nodeDepthByKey = computed(() => {
  const depth = new Map()
  function walk(nodes, level) {
    for (const n of nodes || []) {
      if (n?.key) depth.set(n.key, level)
      walk(n.children || [], level + 1)
    }
  }
  walk(treeNodes.value, 0)
  return depth
})

function depthForNode(node) {
  if (!node?.key) return 0
  return nodeDepthByKey.value.get(node.key) || 0
}

function mergeLoadedSpans(spans) {
  if (!session.value || !spans?.length) return
  const byId = new Map((session.value.spans || []).map(s => [s.span_id, s]))
  for (const s of spans) {
    const prev = byId.get(s.span_id) || {}
    // /map (non-shallow) omits the `attributes` field for descendants, so
    // they get cached as `attributes: {}`. A later /children?recursive=1
    // fetch carries the real attributes; we must take them over the
    // empty placeholder. `prev.attributes || s.attributes` is wrong
    // because `{}` is truthy — it keeps the placeholder and discards
    // the real attrs, leaving conversation rows as bare tool names.
    const prevAttrs = prev.attributes
    const newAttrs = s.attributes
    const attributes =
      newAttrs && Object.keys(newAttrs).length > 0
        ? newAttrs
        : (prevAttrs || newAttrs || {})
    byId.set(s.span_id, { ...prev, ...s, attributes })
  }
  session.value = { ...session.value, spans: Array.from(byId.values()) }
}

function findNodeBySpanId(nodes, spanId) {
  if (!nodes || !spanId) return null
  for (const n of nodes) {
    if (n.data?.span_id === spanId) return n
    const found = findNodeBySpanId(n.children || [], spanId)
    if (found) return found
  }
  return null
}

// Root → target chain. Caller uses every node except the last to know
// which ancestors must be expanded for the target row to render.
function findNodePath(nodes, spanId) {
  if (!nodes || !spanId) return null
  for (const n of nodes) {
    if (n.data?.span_id === spanId) return [n]
    const sub = findNodePath(n.children || [], spanId)
    if (sub) return [n, ...sub]
  }
  return null
}

function withNodeChildren(nodes, spanId, children) {
  let changed = false
  const next = (nodes || []).map((n) => {
    if (n?.data?.span_id === spanId) {
      changed = true
      return {
        ...n,
        children,
        leaf: children.length === 0,
      }
    }
    if (n.children?.length) {
      const updatedChildren = withNodeChildren(n.children, spanId, children)
      if (updatedChildren !== n.children) {
        changed = true
        return { ...n, children: updatedChildren }
      }
    }
    return n
  })
  return changed ? next : nodes
}

async function ensureNodeChildrenLoaded(spanId) {
  const node = findNodeBySpanId(treeNodes.value, spanId)
  if (!node || node.leaf || (node.children && node.children.length)) return
  if (loadingChildren.value.has(spanId)) return

  loadingChildren.value.add(spanId)
  try {
    const data = await api.get(`/sessions/${route.params.id}/spans/${spanId}/children`)
    mergeLoadedSpans(data.spans || [])
    treeNodes.value = withNodeChildren(treeNodes.value, spanId, data.children || [])
  } finally {
    loadingChildren.value.delete(spanId)
  }
}

async function ensureSpanSubtreeLoaded(rootSpanId) {
  if (!rootSpanId) return
  const queue = [rootSpanId]
  const visited = new Set()
  while (queue.length) {
    const spanId = queue.shift()
    if (!spanId || visited.has(spanId)) continue
    visited.add(spanId)

    await ensureNodeChildrenLoaded(spanId)
    const node = findNodeBySpanId(treeNodes.value, spanId)
    if (!node?.children?.length) continue
    for (const child of node.children) {
      if (child && !child.leaf && child.data?.span_id) {
        queue.push(child.data.span_id)
      }
    }
  }
}

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
  selectedSpan.value = allSpans.value.find(s => s.span_id === spanId) || node.data
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

// Hide `text` from the attributes table on assistant_response spans —
// it's already rendered as markdown in the panel above. Other keys
// (turn_uuid, truncated, response_chars, model, …) still show in the
// table so the metadata is visible alongside the prose.
const visibleAttributeKeys = computed(() => {
  if (!selectedSpan.value || !selectedSpan.value.attributes) return []
  const keys = Object.keys(selectedSpan.value.attributes)
  const attrs = selectedSpan.value.attributes
  // `estimated_start_time` renders as the structured "Est. start" row
  // (formatted via fmtTime); hide the raw ISO string here so the panel
  // doesn't show the same value in two different formats.
  if (selectedSpan.value.name === 'assistant_response' || selectedSpan.value.name === 'prompt') {
    return keys.filter(k => k !== 'text' && k !== 'estimated_start_time')
  }
  if (selectedSpan.value.name === 'assistant.thinking') {
    return keys.filter(k => k !== 'estimated_start_time')
  }
  if (selectedSpan.value.name === 'tool.AskUserQuestion') {
    return keys.filter(k => !['questions', 'answers', 'annotations',
                              'denied', 'denial_reason', 'denial_reason_truncated_bytes',
                              'deny_kind'].includes(k))
  }
  // Any other denied tool span (synth `tooldeny-*` from turn_trace) —
  // the deny panel below renders these keys explicitly, so hiding them
  // from the generic attributes table prevents a duplicate read.
  if (attrs.denied) {
    return keys.filter(k => !['denied', 'denial_reason',
                              'denial_reason_truncated_bytes', 'deny_kind'].includes(k))
  }
  if (selectedSpan.value.name === 'rule.check') {
    // `applicable_rules` renders as its own card above the attributes
    // table; the count fields and relative_path are already in that
    // card's header, so hide them from the raw table to avoid
    // duplication. `file_path` (absolute) stays — useful for the user
    // to copy when jumping to the file outside the browser.
    return keys.filter(k => ![
      'applicable_rules', 'engine_tags',
      'applicable_rule_count', 'violating_rule_count',
      'total_rules', 'status', 'relative_path',
    ].includes(k))
  }
  return keys
})

const selectedPromptText = computed(() => {
  if (selectedSpan.value?.name !== 'prompt') return ''
  return selectedSpan.value?.attributes?.text || ''
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
  if (!selectedSpan.value) return undefined
  const answers = selectedSpan.value.attributes?.answers || {}
  return answers[q?.question]
}

// Options used to be stored as bare strings (the label). New traces store
// the full `{label, description, preview?}` object so the detail panel can
// render the description the user saw in the terminal. Helpers normalise both.
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
  if (!selectedSpan.value) return ''
  const ann = selectedSpan.value.attributes?.annotations || {}
  return ann[q?.question]?.notes || ''
}

// Session-level boundaries from the DB — pre-computed at ingest time, so
// the timeline doesn't need to scan all loaded spans. Semantically more
// correct too: bounds don't reshape as the user lazy-expands subtrees.
// Fallback to span-scan only when the session row is missing (shouldn't
// happen for well-formed traces, but covers edge cases).
const traceStart = computed(() => {
  if (session.value?.started_at) return new Date(session.value.started_at).getTime()
  if (!allSpans.value.length) return null
  return Math.min(...allSpans.value.map(s => new Date(s.start_time).getTime()))
})

const traceEnd = computed(() => {
  // ended_at marks when the session formally ended; last_seen tracks
  // MAX(span.end_time). For well-formed sessions they agree, but if a
  // session is resumed after `ended_at` is set, later spans push
  // last_seen forward without resetting ended_at.
  //
  // We also fold in the latest timestamp across the loaded spans. This
  // is what keeps the header duration *live*: an in-progress span (the
  // assistant still responding) has a null end_time, so the server's
  // last_seen — which is MAX(end_time) — doesn't advance until that
  // span finishes. Without the span-scan the duration freezes at the
  // last completed span's end until the active turn completes and a
  // later reload bumps last_seen. Using start_time for unfinished spans
  // lets the timeline grow the moment new live spans stream in.
  //
  // Completed spans never exceed last_seen (it's their MAX(end_time)),
  // so lazy-expanding old subtrees can't reshape the range — only the
  // live edge moves it. Take the max of all three so late spans never
  // overflow the timeline panel (offsetPct past 100%).
  const endedAt = session.value?.ended_at ? new Date(session.value.ended_at).getTime() : null
  const lastSeen = session.value?.last_seen ? new Date(session.value.last_seen).getTime() : null
  // Single-pass max over loaded spans — runs once per span-set change,
  // not per render (the computed is cached). Avoids `Math.max(...spread)`
  // so a very large span count can't overflow the call stack.
  let spanMax = null
  for (const s of allSpans.value) {
    const t = s.end_time ? new Date(s.end_time).getTime() : new Date(s.start_time).getTime()
    if (spanMax === null || t > spanMax) spanMax = t
  }
  let end = null
  for (const v of [endedAt, lastSeen, spanMax]) {
    if (v != null && (end === null || v > end)) end = v
  }
  return end
})

const traceDuration = computed(() => {
  if (!traceStart.value || !traceEnd.value) return 0
  return Math.max(traceEnd.value - traceStart.value, 1)
})

// Long titles wrap the h1 — keep the visible string under control so the
// page header stays compact. Tooltip on the h1 shows the full text.
const SESSION_TITLE_MAX = 90
const SESSION_TITLE_PROMPT_MAX = 72
const sessionTitleRaw = computed(() => (
  (session.value?.title || '').replace(/\s+/g, ' ').trim()
))
const sessionTitleNeedsExpand = computed(() => {
  const t = sessionTitleRaw.value
  if (!t) return false
  const max = session.value?.title_source === 'first_prompt'
    ? SESSION_TITLE_PROMPT_MAX
    : SESSION_TITLE_MAX
  return t.length > max
})
const sessionTitle = computed(() => {
  const t = sessionTitleRaw.value
  if (!t) return 'Session timeline'
  if (sessionTitleExpanded.value || !sessionTitleNeedsExpand.value) return t
  const max = session.value?.title_source === 'first_prompt'
    ? SESSION_TITLE_PROMPT_MAX
    : SESSION_TITLE_MAX
  return t.slice(0, max) + '…'
})
// Jump from a row in the expanded task list to the most relevant
// span in the spine for that task's current state:
//   pending     → TaskCreate (only event so far)
//   in_progress → the TaskUpdate that flipped it to in_progress
//   completed   → the TaskUpdate that flipped it to completed
// Backend pre-computes this as `current_span_id`; we fall back to
// `created_span_id` for pending tasks. If the target span isn't in
// the loaded shallow set (its owning prompt is still collapsed),
// `ensureSpanSubtreeLoaded` fetches the subtree first, then the
// existing `selectedSpan` watcher does the scroll-and-highlight.
async function jumpToTaskSpan(task) {
  const targetSpanId = task?.current_span_id || task?.created_span_id
  if (!targetSpanId) return
  setViewMode('conversation')
  let span = allSpans.value.find(s => s.span_id === targetSpanId)
  if (!span) {
    for (const node of treeNodes.value) {
      if (node?.data?.span_id) {
        // eslint-disable-next-line no-await-in-loop
        await ensureSpanSubtreeLoaded(node.data.span_id)
        span = allSpans.value.find(s => s.span_id === targetSpanId)
        if (span) break
      }
    }
  }
  if (span) selectedSpan.value = span
}

// Tasks summary for the header badge: counts of every status across
// the session's final task-list snapshot. Backend ships
// `session.task_list.final` so this works even when the user hasn't
// expanded the prompts that contain task spans.
const tasksExpanded = ref(false)
const taskSummary = computed(() => {
  const tasks = session.value?.task_list?.final
  if (!Array.isArray(tasks) || !tasks.length) return null
  let completed = 0
  let inProgress = 0
  let pending = 0
  for (const t of tasks) {
    if (t.status === 'completed') completed++
    else if (t.status === 'in_progress') inProgress++
    else pending++
  }
  return { total: tasks.length, completed, inProgress, pending }
})

function titleSourceLabel(src) {
  if (src === 'claude_ai_title') return 'auto'
  if (src === 'user_rename') return 'renamed'
  if (src === 'first_prompt') return 'prompt'
  if (src === 'workflow_name') return 'workflow'
  if (src === 'user') return 'user'
  return src
}
function titleSourceTooltip(src) {
  if (src === 'claude_ai_title') return 'Auto-generated by Claude (the `ai-title` line in the transcript). Updated when the topic pivots.'
  if (src === 'user_rename') return 'You renamed this session in Claude (the `/rename` command writes a `custom-title` line). Sticky against Claude’s auto-titles.'
  if (src === 'first_prompt') return 'Derived from the first user prompt — Claude has not posted an ai-title yet.'
  if (src === 'workflow_name') return 'The workflow’s name (`meta.name` from its script) — the canonical identifier for a dynamic-workflow run. Its objective is shown as the opening bubble.'
  if (src === 'user') return 'Manually set via the regin API; not overwritten by Claude.'
  return src
}

function offsetPct(startTime) {
  const start = new Date(startTime).getTime()
  return ((start - traceStart.value) / traceDuration.value) * 100
}

function widthPct(startTime, endTime) {
  const start = new Date(startTime).getTime()
  const end = endTime ? new Date(endTime).getTime() : start
  const dur = Math.max(end - start, 50) // min 50ms visual width
  return (dur / traceDuration.value) * 100
}

// Server-side aggregate; see web/trace_projection._compute_active_work_ms
// for the gap-based definition. Always populated since migration 0004 +
// the backfill, so no client-side recomputation is needed.
const activeWorkMs = computed(() => session.value?.active_work_ms ?? 0)

const idleMs = computed(() => Math.max(0, traceDuration.value - activeWorkMs.value))

const activePct = computed(() => {
  if (!traceDuration.value) return null
  return (activeWorkMs.value / traceDuration.value) * 100
})

function barColor(name) {
  const map = {
    'skill.read': 'bg-blue-500',
    'skill.invoke': 'bg-green-500',
    'file.edit': 'bg-orange-500',
    'plan.edit': 'bg-green-600',
    'rule.check': 'bg-red-500',
    'plan session': 'bg-green-500',
    'plan.session': 'bg-green-600',
    'plan.draft': 'bg-green-500',
    'plan.review': 'bg-emerald-400',
    'plan.decision': 'bg-yellow-500',
    'plan.enter': 'bg-green-500',
    'plan.exit': 'bg-green-400',
    'prompt': 'bg-purple-500',
    'conversation': 'bg-slate-600',
    'compact.pre': 'bg-amber-500',
    'compact.post': 'bg-amber-600',
  }
  if (map[name]) return map[name]
  if (name.startsWith('tool.')) return 'bg-cyan-500'
  if (name.startsWith('pre_tool.')) return 'bg-indigo-400'
  return 'bg-gray-400'
}

// Distinct palette for the overview strip so each first-class span stands
// out regardless of its `name`. Cycled by index.
const spanPalette = [
  'bg-blue-500', 'bg-orange-500', 'bg-green-500', 'bg-purple-500',
  'bg-pink-500', 'bg-teal-500', 'bg-amber-500', 'bg-indigo-500',
  'bg-rose-500', 'bg-cyan-500', 'bg-lime-500', 'bg-fuchsia-500',
  'bg-emerald-500', 'bg-yellow-500', 'bg-sky-500', 'bg-violet-500',
]
function paletteColor(index) {
  return spanPalette[index % spanPalette.length]
}

// Grafana-style timeline ticks: 0%, 25%, 50%, 75%, 100% of the trace duration.
const timelineTicks = computed(() => {
  const total = traceDuration.value || 0
  return [0, 0.25, 0.5, 0.75, 1].map(p => ({
    pct: p * 100,
    label: fmtDuration(Math.round(total * p)),
  }))
})

function toolLabel(a, fallback) {
  const tool = toolDisplayLabel(a.tool_name || fallback)
  if (a.command_preview) return `${tool}: ${a.command_preview}`
  // Task tools: subject is the entire signal. Without this branch the
  // Timeline view renders 53 bare "TaskCreate"/"TaskUpdate" rows
  // indistinguishable from each other.
  if ((a.tool_name === 'TaskCreate' || fallback === 'TaskCreate') && a.subject) {
    return a.task_id ? `${tool} #${a.task_id}: ${a.subject}` : `${tool}: ${a.subject}`
  }
  if ((a.tool_name === 'TaskUpdate' || fallback === 'TaskUpdate') && a.task_id) {
    return a.status ? `${tool} #${a.task_id} → ${a.status}` : `${tool} #${a.task_id}`
  }
  if ((a.tool_name === 'TaskOutput' || fallback === 'TaskOutput') && a.task_id) {
    return a.status ? `${tool} #${a.task_id} → ${a.status}` : `${tool} #${a.task_id}`
  }
  if ((a.tool_name === 'Skill' || fallback === 'Skill') && a.skill_name) {
    return `${tool}: ${a.skill_name}`
  }
  const tsTools = a.loaded_tools && a.loaded_tools.length
    ? a.loaded_tools
    : a.selected_tools
  if (tsTools && tsTools.length) {
    return `${tool}: ${tsTools.map(t => t.split('__').pop()).join(', ')}`
  }
  if (a.query) return `${tool}: ${a.query}`
  if (a.pattern && a.file_path) return `${tool}: ${a.pattern} in ${a.file_path.split('/').pop()}`
  if (a.pattern) return `${tool}: ${a.pattern}`
  if (a.file_path) return `${tool}: ${a.file_path.split('/').pop()}`
  return tool
}

function subagentTag(a) {
  return a.agent_type || (a.agent_id ? a.agent_id.slice(0, 8) : '')
}

function spanLabel(span) {
  const a = span.attributes || {}
  switch (span.name) {
    case 'skill.read': return `read: ${a.skill_id || ''}`
    case 'skill.invoke': return `invoke: ${a.skill_id || ''}`
    case 'file.edit': return `edit: ${a.file_path ? a.file_path.split('/').pop() : ''}`
    case 'plan.edit': return `plan edit: ${a.file_path ? a.file_path.split('/').pop() : ''}`
    case 'rule.check': {
      const file = a.relative_path ? a.relative_path.split('/').pop() : 'rule check'
      if (a.status === 'no_applicable_rules') return `rule · ${file} — no applicable rules`
      const total = a.applicable_rule_count ?? 0
      const violations = a.violating_rule_count ?? 0
      if (violations > 0) return `rule · ${file} — ${violations} of ${total} violated`
      return `rule · ${file} — ${total} passed`
    }
    case 'plan.session': return `plan session: ${a.plan_filename || ''}`
    case 'plan.draft': return `plan draft: ${a.plan_filename || ''}`
    case 'plan.review': return `plan review: ${a.plan_filename || ''}`
    case 'plan.decision': return `plan decision: ${a.decision || ''}`
    case 'plan.enter': return `plan: ${a.plan_filename || ''}`
    case 'plan.exit': return 'plan exit'
    case 'compact.pre': {
      const tr = a.trigger ? ` (${a.trigger})` : ''
      const ci = a.custom_instructions ? `: ${a.custom_instructions.slice(0, 60)}` : ''
      return `context compacting${tr}${ci}`
    }
    case 'compact.post': {
      const tr = a.trigger ? ` (${a.trigger})` : ''
      return `context compacted${tr}`
    }
    case 'prompt': return a.text ? a.text.slice(0, 60) : 'prompt'
    case 'conversation': return 'conversation start'
    case 'harness.local_command': {
      const cmd = a.command_name || 'command'
      return a.args ? `${cmd} ${a.args}` : cmd
    }
    case 'workflow.phase':
      return a.title ? `phase: ${a.title}` : 'phase'
    case 'subagent.start': {
      const tag = subagentTag(a)
      return tag ? `subagent: ${tag}` : 'subagent'
    }
    case 'subagent.stop': {
      const tag = subagentTag(a)
      return tag ? `subagent done: ${tag}` : 'subagent done'
    }
    default:
      if (span.name === 'tool.failure') {
        const tool = toolDisplayLabel(a.tool_name || 'tool')
        const bits = [`failed: ${tool}`]
        if (a.is_interrupt) bits.push('(user interrupt)')
        if (a.error) bits.push(`— ${a.error}`)
        return bits.join(' ')
      }
      if (span.name.startsWith('tool.')) {
        return toolLabel(a, span.name.slice(5))
      }
      if (span.name.startsWith('pre_tool.')) {
        return `pre: ${toolDisplayLabel(a.tool_name || span.name.slice(9))}`
      }
      return span.name
  }
}

async function onNodeSelect(event) {
  const nodeData = event?.node?.data || event?.data
  if (!nodeData?.span_id) return
  const full = allSpans.value.find(s => s.span_id === nodeData.span_id)
  selectedSpan.value = full || nodeData

  const selectedNode = event?.node || findNodeBySpanId(treeNodes.value, nodeData.span_id)
  if (selectedNode && !selectedNode.leaf) {
    expandedKeys.value = { ...expandedKeys.value, [selectedNode.key]: true }
    await ensureNodeChildrenLoaded(nodeData.span_id)
  }
}

async function fetchTurns() {
  const res = await api.get(`/sessions/${route.params.id}/turn-usage`)
  turns.value = res.turns || []
  maxTurnConsumption.value = res.max_consumption_tokens || 0
}

async function fetchSpanContent(spanId) {
  if (spanContentCache.value.has(spanId)) return spanContentCache.value.get(spanId)
  try {
    const data = await api.get(`/sessions/${route.params.id}/spans/${spanId}/content`)
    const attrs = data.attributes || {}
    spanContentCache.value.set(spanId, attrs)
    return attrs
  } catch (e) {
    console.error('Failed to fetch span content:', e)
    return {}
  }
}

async function loadTurns() {
  if (turns.value || turnsLoading.value) return
  turnsLoading.value = true
  try {
    await fetchTurns()
  } finally {
    turnsLoading.value = false
  }
}

// Timestamp mixing note: `turn.timestamp` is UTC (`…Z`) from the
// transcript; `span.start_time` is naive local from
// `datetime.now().isoformat()` in the hooks. Both become correct
// epoch-ms when run through the JS `Date` parser — `Z` → UTC,
// no-tz → local. So the millisecond math below works without
// any manual normalization.

function turnStartMs(turn) {
  return turn && turn.timestamp ? new Date(turn.timestamp).getTime() : null
}

const selectedTurn = computed(() => {
  if (!turns.value || !selectedTurnUuid.value) return null
  return turns.value.find(t => t.turn_uuid === selectedTurnUuid.value) || null
})

// Which first-class (root) span(s) does the selected turn overlap?
// Tool-level spans live *under* prompts, but the overview strip only
// renders roots — so matching `span_refs` by id would never hit any
// strip bar. Instead compute overlap between each root's time range
// and the turn's interval `(prev_turn.ts, this_turn.ts]`. A single
// prompt that hosts multiple turns will light up for any of them,
// which is the right behavior: the turn *is* inside that prompt.
const selectedTurnInterval = computed(() => {
  if (!selectedTurn.value || !turns.value) return null
  const idx = turns.value.findIndex(t => t.turn_uuid === selectedTurn.value.turn_uuid)
  if (idx < 0) return null
  const end = turnStartMs(turns.value[idx])
  const start = idx > 0 ? turnStartMs(turns.value[idx - 1]) : -Infinity
  if (end == null) return null
  return { start, end }
})

const spanIdsInSelectedTurn = computed(() => {
  const ids = new Set()
  const iv = selectedTurnInterval.value
  if (!iv || !treeNodes.value.length) return ids
  for (const node of treeNodes.value) {
    const s = new Date(node.data.start_time).getTime()
    const e = node.data.end_time
      ? new Date(node.data.end_time).getTime()
      : s
    // Overlap test: [s, e] intersects (iv.start, iv.end].
    if (e > iv.start && s <= iv.end) ids.add(node.data.span_id)
  }
  return ids
})

// Vertical hairlines drawn on the overview strip, one per turn
// timestamp. Gives the user a visual anchor for turn boundaries even
// before they click anything.
const turnBoundaries = computed(() => {
  if (!turns.value || !traceStart.value || !traceDuration.value) return []
  return turns.value
    .map(t => turnStartMs(t))
    .filter(ms => ms != null && ms >= traceStart.value && ms <= traceEnd.value)
    .map(ms => ({ pct: ((ms - traceStart.value) / traceDuration.value) * 100 }))
})

function turnForSpan(span) {
  if (!turns.value || !turns.value.length || !span || !span.start_time) return null
  const t = new Date(span.start_time).getTime()
  // Turn N's interval is `(turn[N-1].ts, turn[N].ts]`; the first turn
  // owns everything before it as well, matching the backend.
  for (const turn of turns.value) {
    const ts = turnStartMs(turn)
    if (ts == null) continue
    if (t <= ts) return turn
  }
  return null
}

async function selectTurn(turnUuid) {
  if (selectedTurnUuid.value === turnUuid) {
    selectedTurnUuid.value = null
    expandedTurnUuid.value = null
    return
  }
  selectedTurnUuid.value = turnUuid

  // Jump to the actual tool span this turn produced — not the root
  // prompt that contains it. The backend attached `span_refs` (the
  // tool/skill/edit activity that happened in this turn's interval)
  // to each turn; we want selectedSpan to land on the first of those
  // so the details panel shows *what* happened, not just which
  // prompt the turn lived under.
  //
  // The tool spans live under a prompt in the tree, so we also need
  // to expand that prompt (lazy-fetching its children if this is the
  // first visit). Without expansion the target row doesn't exist in
  // the DOM, nothing scrolls into view, and PrimeVue has no key to
  // highlight.
  const turn = turns.value?.find(t => t.turn_uuid === turnUuid)
  if (!turn || !treeNodes.value.length) return

  // Empty-tool-use turns (pure text responses, no tool_use blocks)
  // have no span_refs. Fall back to the owning prompt so the click
  // still lands somewhere — at worst the user sees the prompt
  // highlighted and the ctx/token info in the turn row itself is
  // still the primary signal.
  const firstRef = turn.span_refs?.[0]
  if (!firstRef) {
    const rootIds = spanIdsInSelectedTurn.value
    const root = treeNodes.value.find(n => rootIds.has(n.data.span_id))
    if (root) {
      suppressSpanToTurnSync = true
      selectedSpan.value = root.data
      await nextTick()
      suppressSpanToTurnSync = false
    }
    return
  }

  // Find the root (prompt) whose interval contains this tool span.
  const refMs = new Date(firstRef.start_time).getTime()
  const rootNode = treeNodes.value.find(n => {
    const s = new Date(n.data.start_time).getTime()
    const e = n.data.end_time ? new Date(n.data.end_time).getTime() : s
    return refMs >= s && refMs <= e
  })

  if (rootNode) {
    // Load the entire subtree so the target tool span exists somewhere in
    // treeNodes — it may be a grandchild (subagent → tool), and only
    // loading the prompt's direct children would leave it missing.
    await ensureSpanSubtreeLoaded(rootNode.data.span_id)
    // Then expand every ancestor on the path to the target so the row
    // actually renders in the DOM (PrimeVue only mounts visible rows).
    const path = findNodePath(treeNodes.value, firstRef.span_id)
    if (path && path.length) {
      const next = { ...expandedKeys.value }
      for (const node of path.slice(0, -1)) next[node.key] = true
      expandedKeys.value = next
    }
  }

  // Resolve the full span object if lazy-fetch surfaced it; otherwise
  // fall back to the minimal shim (same shape `handleSpanRefClick`
  // uses) so the details panel still renders *something*.
  await nextTick()
  const full = (allSpans.value || []).find(s => s.span_id === firstRef.span_id)
  const target = full || {
    span_id: firstRef.span_id,
    name: firstRef.name,
    start_time: firstRef.start_time,
    end_time: firstRef.start_time,
    kind: 'internal',
    status_code: 'UNSET',
    attributes: firstRef.tool_name ? { tool_name: firstRef.tool_name } : {},
    duration_ms: 0,
  }

  suppressSpanToTurnSync = true
  selectedSpan.value = target
  // Set selectedKeys here so PrimeVue stamps `.p-highlight` on the new
  // row immediately. The selectedSpan watcher would otherwise do it, but
  // only after an async fetchSpanContent.
  const targetKey = findNodeKey(treeNodes.value, target.span_id)
  if (targetKey) selectedKeys.value = { [targetKey]: true }
  await nextTick()
  suppressSpanToTurnSync = false

  // Lazy-loaded child rows take a few cycles to mount + lay out in
  // PrimeVue. Poll for the row marked with the target span_id (we add
  // data-span-id in the cell template) instead of guessing tick count.
  scrollSpanRowIntoView(target.span_id)
}

function scrollSpanRowIntoView(spanId, attempt = 0) {
  // Both views mark rows with data-span-id: the timeline tree on the
  // inner cell <div>, the terminal log directly on the <tr>.
  const el = document.querySelector(`[data-span-id="${spanId}"]`)
  const row = el?.closest('tr') || el
  const scroller = document.querySelector('.content-scroll')
  if (!row || !scroller) {
    if (attempt < 20) setTimeout(() => scrollSpanRowIntoView(spanId, attempt + 1), 50)
    return
  }
  // PrimeVue's table wrapper has overflow: auto on both axes, so
  // scrollIntoView stops there and never reaches `.content-scroll`.
  // Compute the offset ourselves and jump instantly — `behavior: 'smooth'`
  // gets cancelled by PrimeVue's continuing tree re-renders and leaves
  // the scroll stuck near zero.
  const rowRect = row.getBoundingClientRect()
  const scrollerRect = scroller.getBoundingClientRect()
  const offset = scroller.scrollTop + (rowRect.top - scrollerRect.top)
  const top = offset - scroller.clientHeight / 2 + row.offsetHeight / 2
  scroller.scrollTo({ top: Math.max(0, top), behavior: 'auto' })
}

function toggleTurnExpanded(turnUuid) {
  expandedTurnUuid.value = expandedTurnUuid.value === turnUuid ? null : turnUuid
}

// Recursive lookup: `treeNodes` is the client-built hierarchy of root
// plus lazily-loaded children — find the node key for a span_id so
// we can drive PrimeVue's selection/expansion from a raw span ref.
function findNodeKey(nodes, spanId) {
  if (!nodes || !spanId) return null
  for (const n of nodes) {
    if (n.data?.span_id === spanId) return n.key
    if (n.children && n.children.length) {
      const k = findNodeKey(n.children, spanId)
      if (k) return k
    }
  }
  return null
}

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

watch(
  () => selectedSpan.value?.span_id,
  () => {
    promptExpanded.value = false
  },
)

watch(
  () => session.value?.trace_id,
  () => {
    sessionTitleExpanded.value = false
  },
)

// When the user picks a span (via the strip, the tree, or the
// details panel), surface the owning turn in the Turns sidebar:
// scroll it into view and mark it selected. This is the
// span → turn half of the cross-highlight; the turn → span half
// happens via `spanIdsInSelectedTurn` above.
watch(selectedSpan, async (span) => {
  if (suppressSpanToTurnSync) return
  if (!span || !turns.value) return
  const turn = turnForSpan(span)
  if (!turn) return
  selectedTurnUuid.value = turn.turn_uuid
  await nextTick()
  const row = turnRowRefs[turn.turn_uuid]
  if (row && typeof row.scrollIntoView === 'function') {
    row.scrollIntoView({ block: 'nearest' })
  }
})

function fmtLocalClock(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  const hh = String(d.getHours()).padStart(2, '0')
  const mm = String(d.getMinutes()).padStart(2, '0')
  const ss = String(d.getSeconds()).padStart(2, '0')
  return `${hh}:${mm}:${ss}`
}


// Per-turn consumption = the bytes the user actually paid for THIS
// turn. `context_used_tokens` is dominated by cache_read_tokens on a
// healthy session so it barely moves turn-to-turn — it's a good
// context-window gauge but a terrible per-turn cost indicator.
// These two are what the user should see at a glance.
function turnFreshInTokens(turn) {
  if (!turn) return 0
  return (turn.input_tokens || 0) + (turn.cache_creation_tokens || 0)
}

// Largest (input + cache_creation + output) across the session, for
// scaling per-row consumption bars. Populated from the API response
// so the client doesn't reduce over the full turn list on every render.
const maxTurnConsumption = ref(0)

function turnConsumptionPct(turn) {
  const max = maxTurnConsumption.value
  if (!max || max <= 0) return 0
  const c = turnFreshInTokens(turn) + (turn.output_tokens || 0)
  return Math.max(0, Math.min(100, (c / max) * 100))
}

function turnCtxClass(pct) {
  // Same thresholds as the header badge (`contextBadgeClass`) so the
  // row's bar color agrees with the session-wide indicator.
  if (pct == null) return 'bg-gray-200'
  if (pct >= 80) return 'bg-red-500'
  if (pct >= 50) return 'bg-amber-500'
  return 'bg-green-500'
}

function storeTurnRow(turnUuid, el) {
  if (el) turnRowRefs[turnUuid] = el
  else delete turnRowRefs[turnUuid]
}

function handleSpanRefClick(spanRef) {
  // The drill-down list shows span_refs from the backend turn-usage
  // response — those carry a minimal shape (span_id, name,
  // start_time, tool_name) without attributes or duration. Prefer a
  // fuller copy from `session.spans` / lazily-expanded children when
  // one has landed already; fall back to the ref so the details
  // panel still shows *something* (name + start) on first click.
  const cached = (allSpans.value || []).find(s => s.span_id === spanRef.span_id)
  if (cached) {
    selectedSpan.value = cached
    return
  }
  selectedSpan.value = {
    span_id: spanRef.span_id,
    name: spanRef.name,
    start_time: spanRef.start_time,
    end_time: spanRef.start_time,
    kind: 'internal',
    status_code: 'UNSET',
    attributes: spanRef.tool_name ? { tool_name: spanRef.tool_name } : {},
    duration_ms: 0,
  }
}

function toolBadgeColor(name) {
  // Reuse the tree view's span color map so the sidebar's
  // "Read×2·Bash" chips match the palette of the corresponding
  // tree rows — a quick visual link between the two views.
  return barColor(name.startsWith('tool.') ? name : 'tool.' + name)
}

function fmtTime(iso) {
  if (!iso) return '-'
  const d = new Date(iso)
  return d.toLocaleTimeString() + '.' + String(d.getMilliseconds()).padStart(3, '0')
}

// Spans whose duration_ms is a semantic latency (inference time) rather
// than a wall-clock envelope; their start_time marks completion, not start.
const SEMANTIC_DURATION_NAMES = new Set(['assistant_response', 'assistant.thinking'])

// Estimated inference start = completion − inference latency. The poster
// stores this as `attributes.estimated_start_time`; for spans ingested
// before that existed, reconstruct it from start_time − duration_ms.
function estStart(span) {
  if (!span) return null
  if (span.attributes?.estimated_start_time) return span.attributes.estimated_start_time
  if (!SEMANTIC_DURATION_NAMES.has(span.name) || !span.duration_ms) return null
  return new Date(new Date(span.start_time).getTime() - span.duration_ms).toISOString()
}

function fmtDate(iso) {
  if (!iso) return '-'
  return new Date(iso).toLocaleString()
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
  while (end > start && units[end].value === 0) {
    end--
  }

  return units.slice(start, end + 1).map(u => `${u.value}${u.label}`).join('')
}

// Match the thresholds used by ~/.claude/statusline-command.sh so the
// badge color here agrees with what the user sees in their terminal.
function contextBadgeClass(pct) {
  if (pct == null) return 'bg-gray-100 text-gray-500 border-gray-200'
  if (pct >= 80) return 'bg-red-50 text-red-700 border-red-200'
  if (pct >= 50) return 'bg-amber-50 text-amber-700 border-amber-200'
  return 'bg-green-50 text-green-700 border-green-200'
}

function hasToolTokens(d) {
  return d && (d.input_tokens != null || d.output_tokens != null)
}

function tokenTitle(d) {
  if (!d) return ''
  const parts = []
  if (d.input_tokens != null) parts.push(`in: ${d.input_tokens} (result on next turn)`)
  if (d.image_tokens) parts.push(`  image: ${d.image_tokens}`)
  if (d.output_tokens != null) parts.push(`out: ${d.output_tokens} (this turn's tool_use)`)
  if (d.cost_usd != null) parts.push(`cost: ${fmtCost(d.cost_usd)}`)
  return parts.join('\n')
}

function fmtCost(usd) {
  if (usd == null) return ''
  if (usd < 0.01) return '$' + usd.toFixed(4)
  return '$' + usd.toFixed(2)
}

// Per-session rollup: aggregated server-side from session_spans so we
// don't depend on having every tool span loaded in the tree (shallow
// mode only ships root spans). Untagged remainder = session-level
// tokens minus what's attributable to tools, prose (assistant_text)
// and reasoning (assistant_thinking); leftover is system prompt +
// conversation history + any untracked prose.
const toolRollupData = ref(null)

async function fetchToolRollup() {
  try {
    toolRollupData.value = await api.get(
      `/sessions/${route.params.id}/tool-rollup`
    )
  } catch (e) {
    toolRollupData.value = null
  }
}

async function fetchPlans() {
  try {
    const data = await api.get(
      `/plan-sessions?session=${encodeURIComponent(route.params.id)}&size=20`
    )
    plans.value = data.items || []
  } catch (e) {
    plans.value = []
  }
}

async function fetchWorkflowRuns() {
  try {
    const data = await api.get(
      `/sessions/${encodeURIComponent(route.params.id)}/workflow-runs`
    )
    workflowRuns.value = data.items || []
  } catch (e) {
    workflowRuns.value = []
  }
}

// Small badge prefix shown ahead of each chip in the Tokens-by-tool
// rollup. Mirrors the MCP badge convention so every chip carries a
// 2-3 char uppercase code instead of just MCP being special-cased.
// Color buckets are coarse on purpose: FS for file ops, SH for shell,
// AGT for subagent/skill/orchestration calls, BG for background-task
// dispatch, AI for assistant prose, MCP for plugin tools.
// Each badge doubles as a group definition for the grouped rollup view:
// `group` is the human-readable cluster name, `order` is the tiebreak
// when two groups have equal token totals (groups otherwise sort by
// total tokens desc).
const _TOOL_BADGE_FS = { label: 'FS', classes: 'bg-amber-100 text-amber-800', group: 'Read / write', order: 1 }
const _TOOL_BADGE_SH = { label: 'SH', classes: 'bg-slate-200 text-slate-700', group: 'Shell', order: 2 }
const _TOOL_BADGE_AGT = { label: 'AGT', classes: 'bg-pink-100 text-pink-800', group: 'Agents & skills', order: 3 }
const _TOOL_BADGE_BG = { label: 'BG', classes: 'bg-orange-100 text-orange-800', group: 'Background tasks', order: 4 }
const _TOOL_BADGE_NET = { label: 'NET', classes: 'bg-purple-100 text-purple-800', group: 'Network', order: 5 }
const _TOOL_BADGE_MCP = { label: 'MCP', classes: 'bg-cyan-100 text-cyan-800', group: 'MCP tools', order: 6 }
const _TOOL_BADGE_AI = { label: 'AI', classes: 'bg-emerald-100 text-emerald-800', group: 'Model output', order: 7 }
const _TOOL_BADGE_TH = { label: 'TH', classes: 'bg-amber-200 text-amber-900', group: 'Thinking', order: 8 }
const _TOOL_BADGE_SYS = { label: 'SYS', classes: 'bg-slate-100 text-slate-600', group: 'System', order: 9 }

function toolBadge(fullName) {
  if (!fullName) return _TOOL_BADGE_SYS
  if (fullName === 'assistant_text') return _TOOL_BADGE_AI
  if (fullName === 'assistant_thinking') return _TOOL_BADGE_TH
  if (fullName.startsWith('mcp__')) return _TOOL_BADGE_MCP
  if (['Read', 'Write', 'Edit', 'MultiEdit', 'NotebookEdit'].includes(fullName)) {
    return _TOOL_BADGE_FS
  }
  if (fullName === 'Bash') return _TOOL_BADGE_SH
  if (['WebFetch', 'WebSearch'].includes(fullName)) return _TOOL_BADGE_NET
  if (['Agent', 'Skill', 'AskUserQuestion', 'ToolSearch'].includes(fullName)) {
    return _TOOL_BADGE_AGT
  }
  if (['TaskCreate', 'TaskUpdate', 'TaskStop', 'TaskGet', 'TaskList',
       'TaskOutput', 'ScheduleWakeup', 'CronCreate', 'CronDelete',
       'CronList'].includes(fullName)) {
    return _TOOL_BADGE_BG
  }
  return _TOOL_BADGE_SYS
}

const toolTokenRollup = computed(() => {
  const raw = toolRollupData.value
  if (!raw || !Array.isArray(raw.rollup)) return []
  return raw.rollup.map(t => {
    const isMcp = (t.name || '').startsWith('mcp__')
    const isAssistantText = t.name === 'assistant_text'
    const isAssistantThinking = t.name === 'assistant_thinking'
    let displayName = t.name
    if (isMcp) displayName = toolDisplayLabel(t.name)
    else if (isAssistantThinking) displayName = 'thinking'
    return {
      name: displayName,
      fullName: t.name,
      isMcp,
      isAssistantText,
      isAssistantThinking,
      badge: toolBadge(t.name),
      input: t.input_tokens || 0,
      output: t.output_tokens || 0,
      cost: t.cost_usd || 0,
      n: t.calls || 0,
    }
  })
})

// Two ways to organize the rollup: 'groups' clusters tools by their
// badge bucket with a per-group token subtotal; 'tokens' is a flat list
// sorted by token spend. Default to the token-sorted flat list.
const rollupView = ref('tokens')

// The rollup collapses to a one-line summary by default so it doesn't
// compete with the header usage chips and timeline; expand to reveal the
// view toggle and per-tool chips.
const rollupExpanded = ref(false)

const _toolTokens = (t) => (t.input || 0) + (t.output || 0)

// Flat list sorted by total tokens desc (the 'tokens' view).
const toolTokenSorted = computed(() =>
  [...toolTokenRollup.value].sort((a, b) => _toolTokens(b) - _toolTokens(a))
)

// Tools clustered by badge bucket (the 'groups' view). Each group carries
// a token/cost/call subtotal. Groups sort by total tokens desc (badge
// `order` breaks ties); tools within a group sort by tokens desc.
const toolTokenGroups = computed(() => {
  const byKey = new Map()
  for (const tool of toolTokenRollup.value) {
    const badge = tool.badge
    let g = byKey.get(badge.label)
    if (!g) {
      g = { key: badge.label, label: badge.group, badge,
            order: badge.order, tools: [], input: 0, output: 0, cost: 0, n: 0 }
      byKey.set(badge.label, g)
    }
    g.tools.push(tool)
    g.input += tool.input
    g.output += tool.output
    g.cost += tool.cost
    g.n += tool.n
  }
  const groups = [...byKey.values()]
  for (const g of groups) {
    g.tools.sort((a, b) => _toolTokens(b) - _toolTokens(a))
  }
  groups.sort((a, b) => {
    const diff = (b.input + b.output) - (a.input + a.output)
    return diff !== 0 ? diff : a.order - b.order
  })
  return groups
})

const toolRollupSummary = computed(() => {
  const raw = toolRollupData.value
  if (!raw) {
    return { attributedIn: 0, attributedOut: 0, attributedCost: 0,
             untaggedIn: 0, untaggedOut: 0, hasData: false }
  }
  return {
    attributedIn: raw.attributed_input_tokens || 0,
    attributedOut: raw.attributed_output_tokens || 0,
    attributedCost: raw.attributed_cost_usd || 0,
    untaggedIn: raw.untagged_input_tokens || 0,
    untaggedOut: raw.untagged_output_tokens || 0,
    hasData: Array.isArray(raw.rollup) && raw.rollup.length > 0,
  }
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
    :style="{ '--regin-trace-header-h': stickyHeaderHeight ? stickyHeaderHeight + 'px' : '0px' }"
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
      class="sticky -top-4 lg:-top-6 z-20 bg-white -mx-4 -mt-4 px-4 pt-4 lg:-mx-8 lg:-mt-6 lg:px-8 lg:pt-6 pb-4 mb-4 border-b border-slate-200 shadow-[0_2px_4px_-2px_rgba(15,23,42,0.06)]"
    >
    <header class="flex items-start justify-between gap-4 flex-wrap mb-5">
      <div class="min-w-0 flex-1">
        <div class="text-[11px] tracking-widest uppercase text-slate-400 font-semibold mb-1">
          Observability · Session
        </div>
        <h1
          class="text-2xl font-semibold text-slate-900 leading-tight m-0 break-words"
          :title="session.title || ''"
        >{{ sessionTitle }}<span
          v-if="session.title && session.title_source"
          class="ml-2 align-middle inline-block rounded border border-slate-200 bg-slate-50 text-slate-500 text-[10px] font-medium px-1.5 py-0.5 uppercase tracking-wide"
          :title="titleSourceTooltip(session.title_source)"
        >{{ titleSourceLabel(session.title_source) }}</span></h1>
        <div
          v-if="sessionTitleNeedsExpand"
          class="mt-1.5"
        >
          <button
            type="button"
            class="text-xs font-medium text-blue-600 hover:text-blue-800 focus-visible:outline-2 focus-visible:outline-blue-500 rounded"
            @click="sessionTitleExpanded = !sessionTitleExpanded"
          >
            {{ sessionTitleExpanded ? 'Collapse title' : 'Show full title' }}
          </button>
        </div>
        <p class="mt-1.5 flex items-center flex-wrap gap-x-2 gap-y-1 text-xs text-slate-500 m-0">
          <code class="font-mono text-[11px] text-slate-600 bg-slate-100 px-1.5 py-0.5 rounded">{{ session.trace_id }}</code>
          <!-- No ⚙ workflow-name chip here: the session title already *is*
               the workflow name (title_source=workflow_name), so a chip
               repeating it would be redundant. The backlink below stays. -->
          <router-link
            v-if="workflowParentTo"
            :to="workflowParentTo"
            class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full border border-slate-300 bg-white text-[11px] font-medium text-slate-600 hover:bg-slate-50 no-underline focus-visible:outline-2 focus-visible:outline-blue-500"
            title="Open the Claude Code session that launched this workflow run"
          >↑ launched from session</router-link>
          <span class="text-slate-300">·</span>
          <span>{{ session.span_count_total ?? session.spans.length }} spans</span>
          <span class="text-slate-300">·</span>
          <span :title="`wall-clock from first to last span — includes user-idle gaps between turns`">
            duration <span class="font-mono">{{ fmtDuration(Math.round(traceDuration)) }}</span>
          </span>
          <template v-if="activeWorkMs > 0">
            <span class="text-slate-300">·</span>
            <span :title="`union of root-span intervals (overlaps merged) — agent work time, idle ${fmtDuration(idleMs)} excluded`">
              active <span class="font-mono">{{ activePct != null ? Math.round(activePct) + '%' : fmtDuration(activeWorkMs) }}</span>
            </span>
          </template>
          <template v-if="session.context_pct != null">
            <!-- Headline is main-conversation peak (matches terminal). -->
            <span
              class="inline-flex items-center gap-1 px-2 py-0.5 rounded border text-[11px] font-medium ml-1"
              :class="contextBadgeClass(session.context_pct)"
              :title="`main-conversation peak: ${session.peak_main_context_tokens || session.peak_context_tokens} / ${session.context_window_tokens} tokens`"
            >ctx {{ session.context_pct }}%
              <span class="opacity-75 font-mono">{{ fmtTokens(session.peak_main_context_tokens || session.peak_context_tokens) }} / {{ fmtTokens(session.context_window_tokens) }}</span>
            </span>
            <!-- All-inclusive peak only when it diverges (advisor turns). -->
            <span
              v-if="session.context_pct_all != null
                    && (session.context_pct_all - session.context_pct) > 1"
              class="inline-flex items-center gap-1 px-2 py-0.5 rounded border text-[10px] font-medium border-slate-200 bg-slate-50 text-slate-500"
              :title="`peak with advisor/sub-call tokens rolled in: ${session.peak_context_tokens} / ${session.context_window_tokens}`"
            >+sub {{ session.context_pct_all }}%</span>
          </template>
          <!-- Workflow runs have no single context window (no ctx% chip), so
               surface the run's authoritative grand total (manifest
               totalTokens — input + cache + output across all agents). -->
          <template v-if="session.total_tokens">
            <span
              class="inline-flex items-center gap-1 px-2 py-0.5 rounded border border-slate-200 bg-slate-50 text-slate-600 text-[11px] font-medium ml-1"
              title="total tokens across all workflow agents (input + cache + output), from the run manifest"
            >Σ <span class="opacity-75 font-mono">{{ fmtTokens(session.total_tokens) }}</span> tokens</span>
          </template>
          <template v-if="session.model">
            <span class="text-xs text-slate-500 font-mono ml-1">{{ session.model }}</span>
          </template>
          <!-- Plan chips: each PlanSession row this session authored or
               edited (from `plan_sessions`) lets the reader pivot
               session → plan from the header. N=1 renders inline as a
               direct link; N≥2 collapses to a `plans N` chip with
               click-to-expand, matching the tasks summary just above
               so the two summaries look and behave the same. -->
          <template v-if="plans.length === 1">
            <span class="text-slate-300">·</span>
            <router-link
              :to="`/plans/${encodeURIComponent(plans[0].plan_filename)}`"
              class="inline-flex items-center gap-1 px-2 py-0.5 rounded border text-[11px] font-medium border-blue-200 bg-blue-50 text-blue-700 hover:bg-blue-100 no-underline focus-visible:outline-2 focus-visible:outline-blue-500"
              :title="`plan: ${plans[0].plan_filename}`"
            >plan
              <span class="font-mono opacity-80 truncate max-w-[14rem]">{{ plans[0].plan_filename }}</span>
            </router-link>
          </template>
          <template v-else-if="plans.length > 1">
            <span class="text-slate-300">·</span>
            <span
              class="inline-flex items-center gap-1 px-2 py-0.5 rounded border text-[11px] font-medium ml-0 cursor-pointer select-none border-blue-200 bg-blue-50 text-blue-700 hover:bg-blue-100"
              :title="'plan files this session authored or edited — click to expand'"
              @click="plansExpanded = !plansExpanded"
            >plans {{ plans.length }}
              <span class="opacity-60 ml-0.5">{{ plansExpanded ? '▾' : '▸' }}</span>
            </span>
          </template>
          <!-- Workflow run chips: dynamic-workflow runs this session
               launched, so the reader can pivot session → run from the
               header. Mirrors the plan chips: N=1 inlines as `⚙ <name>`;
               N≥2 collapses to `workflows N` with click-to-expand. -->
          <template v-if="workflowRuns.length === 1">
            <span class="text-slate-300">·</span>
            <router-link
              :to="`/trace/sessions/${workflowRuns[0].run_id}`"
              class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full border text-[11px] font-medium border-emerald-300 bg-emerald-50 text-emerald-700 hover:bg-emerald-100 no-underline focus-visible:outline-2 focus-visible:outline-emerald-500"
              :title="`workflow run: ${workflowRuns[0].name || workflowRuns[0].run_id}`"
            >⚙ <span class="truncate max-w-[14rem]">{{ workflowRuns[0].name || 'workflow run' }}</span></router-link>
          </template>
          <template v-else-if="workflowRuns.length > 1">
            <span class="text-slate-300">·</span>
            <span
              class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full border text-[11px] font-medium cursor-pointer select-none border-emerald-300 bg-emerald-50 text-emerald-700 hover:bg-emerald-100"
              :title="'dynamic-workflow runs launched from this session — click to expand'"
              @click="workflowRunsExpanded = !workflowRunsExpanded"
            >⚙ workflows {{ workflowRuns.length }}
              <span class="opacity-60 ml-0.5">{{ workflowRunsExpanded ? '▾' : '▸' }}</span>
            </span>
          </template>
          <!-- Tasks summary badge: shows the final task-list state
               across the whole session so the reader doesn't have to
               scroll the spine to find it. Click to expand the full
               list inline. -->
          <template v-if="taskSummary">
            <span
              class="inline-flex items-center gap-1 px-2 py-0.5 rounded border text-[11px] font-medium ml-1 cursor-pointer select-none border-indigo-200 bg-indigo-50 text-indigo-700 hover:bg-indigo-100"
              :title="'session task list — click to expand'"
              @click="tasksExpanded = !tasksExpanded"
            >tasks {{ taskSummary.total }}
              <span class="opacity-75 font-mono">{{ taskSummary.completed }}☑ · {{ taskSummary.inProgress }}◐ · {{ taskSummary.pending }}☐</span>
              <span class="opacity-60 ml-0.5">{{ tasksExpanded ? '▾' : '▸' }}</span>
            </span>
          </template>
        </p>
        <!-- Expanded plans list (mirrors the tasks pattern below).
             Each row is a router-link to /plans/<filename>, so the
             reader can pivot to any of the session's plan files
             without scrolling or hunting in the spine. -->
        <div
          v-if="plans.length > 1 && plansExpanded"
          class="mt-2 rounded-md border border-blue-200 bg-blue-50/50 px-3 py-2 max-w-2xl"
        >
          <ul class="text-[13px] text-slate-800 leading-snug">
            <li
              v-for="p in plans"
              :key="p.id"
              class="flex items-baseline gap-2 py-0.5"
            >
              <router-link
                :to="`/plans/${encodeURIComponent(p.plan_filename)}`"
                class="font-mono text-[12px] text-blue-700 hover:text-blue-900 hover:underline focus-visible:outline-2 focus-visible:outline-blue-500 break-all"
                :title="p.plan_filename"
              >{{ p.plan_filename }}</router-link>
              <span class="text-slate-400 text-[11px] font-mono shrink-0">
                {{ fmtDate(p.started_at) }}<span v-if="p.ended_at"> – {{ fmtDate(p.ended_at) }}</span>
              </span>
            </li>
          </ul>
        </div>
        <!-- Expanded workflow runs list (mirrors the plans list). Each
             row links to the run's captured trace. -->
        <div
          v-if="workflowRuns.length > 1 && workflowRunsExpanded"
          class="mt-2 rounded-md border border-emerald-200 bg-emerald-50/50 px-3 py-2 max-w-2xl"
        >
          <ul class="text-[13px] text-slate-800 leading-snug">
            <li
              v-for="r in workflowRuns"
              :key="r.run_id"
              class="flex items-baseline gap-2 py-0.5"
            >
              <router-link
                :to="`/trace/sessions/${r.run_id}`"
                class="text-emerald-700 hover:text-emerald-900 hover:underline focus-visible:outline-2 focus-visible:outline-emerald-500 break-all"
                :title="r.run_id"
              >⚙ {{ r.name || r.run_id }}</router-link>
              <span class="text-slate-400 text-[11px] font-mono shrink-0">{{ r.run_id }}</span>
            </li>
          </ul>
        </div>
        <!-- Expanded task list (final state across the session).
             Each row is clickable: jumps the spine to that task's
             TaskCreate span and selects it, so the user can click a
             task in the summary and land on the moment it was opened
             without scrolling through hundreds of spans. -->
        <div
          v-if="taskSummary && tasksExpanded"
          class="mt-2 rounded-md border border-indigo-200 bg-indigo-50/50 px-3 py-2 max-w-2xl"
        >
          <ul class="text-[13px] text-slate-800 leading-snug">
            <li
              v-for="t in session.task_list?.final || []"
              :key="t.task_id"
              tabindex="0"
              class="flex items-baseline gap-2 rounded px-1 -mx-1 py-0.5 cursor-pointer hover:bg-indigo-100 focus-visible:outline-2 focus-visible:outline-indigo-400"
              :title="(t.current_span_id || t.created_span_id) ? `jump to the ${t.status === 'pending' ? 'creation' : t.status === 'in_progress' ? 'in-progress moment' : 'completion'} of this task` : ''"
              @click="jumpToTaskSpan(t)"
              @keydown.enter.prevent="jumpToTaskSpan(t)"
            >
              <span class="font-mono text-[12px]" :class="t.status === 'completed' ? 'text-emerald-600' : t.status === 'in_progress' ? 'text-amber-600' : 'text-slate-400'">{{ t.status === 'completed' ? '☑' : t.status === 'in_progress' ? '◐' : '☐' }}</span>
              <span class="font-mono text-[11px] text-slate-400 shrink-0">#{{ t.task_id }}</span>
              <span class="break-words flex-1 min-w-0" :class="t.status === 'completed' ? 'text-slate-500 line-through decoration-slate-300' : ''">{{ t.subject || '(no subject)' }}</span>
            </li>
          </ul>
        </div>
      </div>
      <div class="flex flex-col items-end gap-1.5 shrink-0">
        <div class="inline-flex items-center gap-1.5">
          <button
            v-for="opt in [
              { id: 'conversation', label: 'Conversation' },
              { id: 'timeline', label: 'Timeline' },
              { id: 'terminal', label: 'Terminal' },
            ]"
            :key="opt.id"
            type="button"
            class="px-3 py-1 text-xs rounded-full border transition-colors focus-visible:outline-2 focus-visible:outline-blue-500"
            :class="viewMode === opt.id
              ? 'bg-blue-50 border-blue-400 text-blue-700 font-medium'
              : 'bg-white border-slate-200 text-slate-600 hover:bg-slate-50'"
            @click="setViewMode(opt.id)"
          >{{ opt.label }}</button>
        </div>
        <div class="flex items-center gap-2 text-[11px] text-slate-400 font-mono">
          <span v-if="lastReloadedAt">updated {{ fmtLocalClock(lastReloadedAt.toISOString()) }}</span>
          <button
            type="button"
            class="text-blue-600 hover:text-blue-800 hover:underline disabled:opacity-50 disabled:cursor-not-allowed focus-visible:outline-2 focus-visible:outline-blue-500"
            :disabled="reloading || loading"
            :title="'Re-fetch spans' + (turns != null ? ' and turns' : '') + ' from the server'"
            @click="reload"
          >
            <span :class="reloading ? 'animate-spin inline-block' : 'inline-block'">↻</span>
            {{ reloading ? 'Reloading…' : 'Reload' }}
          </button>
        </div>
      </div>
    </header>

    <div
      v-if="toolRollupSummary.hasData"
      class="mb-4 rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-xs"
    >
      <div class="flex items-center gap-2" :class="rollupExpanded ? 'mb-1.5' : ''">
        <button
          type="button"
          class="flex items-center gap-2 -mx-1 px-1 rounded transition-colors hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400"
          :aria-expanded="rollupExpanded"
          @click="rollupExpanded = !rollupExpanded"
        >
          <svg
            class="w-3 h-3 text-slate-400 transition-transform"
            :class="rollupExpanded ? 'rotate-90' : ''"
            viewBox="0 0 12 12" fill="none" aria-hidden="true"
          >
            <path d="M4.5 2.5 8 6l-3.5 3.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
          <span class="font-medium text-slate-700">Tokens by tool</span>
          <span
            v-if="toolRollupSummary.attributedCost > 0"
            class="font-mono text-slate-500"
            :title="'cost computed from session model rates via models.dev'"
          >· {{ fmtCost(toolRollupSummary.attributedCost) }} attributed</span>
          <span class="font-mono text-slate-400">· {{ toolTokenRollup.length }} tools</span>
        </button>
        <div
          v-if="rollupExpanded"
          class="ml-auto inline-flex rounded-md border border-slate-200 overflow-hidden text-[10px] font-medium"
        >
          <button
            type="button"
            class="px-2 py-0.5 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400 focus-visible:ring-inset"
            :class="rollupView === 'groups' ? 'bg-slate-700 text-white' : 'bg-white text-slate-500 hover:bg-slate-50'"
            @click="rollupView = 'groups'"
          >Groups</button>
          <button
            type="button"
            class="px-2 py-0.5 transition-colors border-l border-slate-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400 focus-visible:ring-inset"
            :class="rollupView === 'tokens' ? 'bg-slate-700 text-white' : 'bg-white text-slate-500 hover:bg-slate-50'"
            @click="rollupView = 'tokens'"
          >By tokens</button>
        </div>
      </div>

      <!-- Grouped view: one row per badge bucket, leading label carries the
           group subtotal, followed by the per-tool chips. -->
      <div v-if="rollupExpanded && rollupView === 'groups'" class="flex flex-col gap-1">
        <div
          v-for="g in toolTokenGroups"
          :key="g.key"
          class="flex items-start gap-x-3 font-mono text-[11px]"
        >
          <span
            class="inline-flex items-center gap-1.5 shrink-0 w-[164px]"
            :title="g.n + ' call(s) · in ' + g.input + ' · out ' + g.output + (g.cost ? ' · ' + fmtCost(g.cost) : '')"
          >
            <span
              class="inline-block text-[9px] font-semibold uppercase tracking-wider px-1 py-px rounded"
              :class="g.badge.classes"
            >{{ g.badge.label }}</span>
            <span class="font-sans text-slate-600">{{ g.label }}</span>
            <span class="text-slate-500 font-semibold">{{ fmtTokens(g.input + g.output) }}</span>
          </span>
          <!-- Chips wrap within their own column so the second line indents
               under the first chip instead of the group gutter. -->
          <div class="flex flex-wrap items-center gap-x-3 gap-y-1 min-w-0">
            <span
              v-for="tool in g.tools"
              :key="tool.fullName"
              class="inline-flex items-center gap-1"
              :title="tool.fullName + ' — ' + tool.n + ' call(s) · in ' + tool.input + ' · out ' + tool.output + (tool.cost ? ' · ' + fmtCost(tool.cost) : '')"
            >
              <span class="text-slate-600">{{ tool.name }}</span>
              <span class="text-slate-400">{{ fmtTokens(tool.input + tool.output) }}</span>
            </span>
          </div>
        </div>
      </div>

      <!-- Flat view: every tool sorted by token spend, badge-prefixed. -->
      <div v-else-if="rollupExpanded" class="flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-[11px]">
        <span
          v-for="tool in toolTokenSorted"
          :key="tool.fullName"
          class="inline-flex items-center gap-1"
          :title="tool.fullName + ' — ' + tool.n + ' call(s) · in ' + tool.input + ' · out ' + tool.output + (tool.cost ? ' · ' + fmtCost(tool.cost) : '')"
        >
          <span
            class="inline-block text-[9px] font-semibold uppercase tracking-wider px-1 py-px rounded"
            :class="tool.badge.classes"
          >{{ tool.badge.label }}</span>
          <span class="text-slate-600">{{ tool.name }}</span>
          <span class="text-slate-400">{{ fmtTokens(tool.input + tool.output) }}</span>
        </span>
      </div>

      <!-- Untagged remainder: shown in both views as a trailing note. -->
      <div
        v-if="rollupExpanded && toolRollupSummary.untaggedIn + toolRollupSummary.untaggedOut > 0"
        class="mt-1.5 pt-1.5 border-t border-slate-100 font-mono text-[11px]"
      >
        <span
          class="inline-flex items-center gap-1"
          :title="'in: ' + toolRollupSummary.untaggedIn + ' · out: ' + toolRollupSummary.untaggedOut + ' — system prompt, conversation history, untracked prose'"
        >
          <span class="text-slate-400 italic">untagged</span>
          <span class="text-slate-400">{{ fmtTokens(toolRollupSummary.untaggedIn + toolRollupSummary.untaggedOut) }}</span>
        </span>
      </div>
    </div>

    <div class="mb-4 rounded-xl border border-slate-200 bg-slate-50 px-4 pt-3 pb-3.5">
      <!-- Time axis -->
      <div class="relative h-4 w-full text-[10px] text-gray-500 font-mono">
        <div
          v-for="tick in timelineTicks"
          :key="'tl-' + tick.pct"
          class="absolute top-0"
          :style="{ left: tick.pct + '%', transform: tick.pct === 0 ? 'translateX(0)' : tick.pct === 100 ? 'translateX(-100%)' : 'translateX(-50%)' }"
        >{{ tick.label }}</div>
      </div>
      <!-- Bars + gridlines -->
      <div class="relative h-5 w-full bg-white rounded border border-gray-200 overflow-hidden">
        <!-- gridlines -->
        <div
          v-for="tick in timelineTicks"
          :key="'gl-' + tick.pct"
          class="absolute top-0 bottom-0 w-px bg-gray-200"
          :style="{ left: tick.pct + '%' }"
        ></div>
        <div
          v-for="(node, idx) in treeNodes"
          :key="node.data.span_id"
          class="absolute top-0.5 bottom-0.5 rounded-sm cursor-pointer transition-opacity hover:opacity-100 focus-visible:outline-2 focus-visible:outline-blue-500"
          :class="[
            paletteColor(idx),
            selectedSpan && selectedSpan.span_id === node.data.span_id ? 'ring-2 ring-offset-1 ring-gray-800' : '',
            selectedTurnUuid && !spanIdsInSelectedTurn.has(node.data.span_id) ? 'opacity-20 hover:opacity-50' : 'opacity-90 hover:opacity-100',
          ]"
          :style="{ left: offsetPct(node.data.start_time) + '%', width: Math.max(widthPct(node.data.start_time, node.data.end_time), 0.2) + '%' }"
          :title="spanLabel(node.data) + ' — ' + fmtDuration(node.data.duration_ms)"
          @click="onOverviewSpanClick(node)"
        ></div>
        <!-- Turn boundary markers — faint vertical lines so the user
             can see turn cadence at a glance without selecting. -->
        <div
          v-for="(b, i) in turnBoundaries"
          :key="'tb-' + i"
          class="absolute top-0 bottom-0 w-px bg-indigo-300/50 pointer-events-none"
          :style="{ left: b.pct + '%' }"
        ></div>
      </div>
    </div>

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
    <!-- /sticky page header -->

    <div class="flex flex-col lg:flex-row gap-4 lg:items-start">
      <!-- Conversation view: rendered outside Card so its sidebar can be sticky -->
      <template v-if="viewMode === 'conversation'">
        <SessionConversationView
          :spans="allSpans"
          :turns="turns"
          :selected-span="selectedSpan"
          :trace-id="session?.trace_id"
          :context-window-tokens="session?.context_window_tokens"
          class="flex-1 min-w-0"
          @select-span="selectedSpan = $event"
          @fetch-content="fetchSpanContent"
          @load-subtree="ensureSpanSubtreeLoaded"
          @jump-live="jumpToLatestSpan"
        />
      </template>

      <template v-else>
        <Card :no-padding="true" class="trace-content-card flex-1 min-w-0 w-full">
          <!-- Timeline view: TreeTable -->
          <template v-if="viewMode === 'timeline'">
            <TreeTable
              :value="treeNodes"
              :lazy="true"
              v-model:expanded-keys="expandedKeys"
              v-model:selection-keys="selectedKeys"
              selection-mode="single"
              @node-select="onNodeSelect"
              class="text-sm"
              table-class="w-full table-fixed"
            >
              <Column field="name" header="Span" style="min-width: 14rem">
                <template #body="{ node }">
                  <div class="flex items-center gap-2 min-w-0 w-full" :data-span-id="node.data.span_id">
                    <TreeIndent
                      :depth="depthForNode(node)"
                      :leaf="node.leaf"
                      :expanded="!!expandedKeys[node.key]"
                      @toggle="toggleTimelineNode(node)"
                    />
                    <span
                      class="inline-block rounded-full shrink-0 w-1.5 h-1.5"
                      :class="barColor(node.data.name)"
                    ></span>
                    <div class="min-w-0 flex-1">
                      <div class="font-medium truncate flex items-center gap-1" :title="spanLabel(node.data)">
                        <span
                          v-if="mcpParts(node.data.name)"
                          class="inline-block text-[9px] font-semibold uppercase tracking-wider px-1 py-px rounded bg-cyan-100 text-cyan-800 shrink-0"
                        >MCP</span>
                        <span class="truncate">{{ spanLabel(node.data) }}</span>
                      </div>
                      <div class="text-xs text-gray-400 truncate" :title="node.data.name">{{ node.data.name }}</div>
                    </div>
                  </div>
                </template>
              </Column>

              <Column field="duration" header="Time" style="min-width: 5rem; width: 5rem">
                <template #body="{ node }">
                  <div class="text-right text-xs text-gray-400">
                    {{ fmtDuration(node.data.duration_ms) }}
                  </div>
                </template>
              </Column>

              <Column field="tokens" header="Tokens" style="min-width: 7rem; width: 7rem">
                <template #body="{ node }">
                  <div
                    v-if="hasToolTokens(node.data)"
                    class="text-right text-xs font-mono text-gray-500"
                    :title="tokenTitle(node.data)"
                  >
                    <span class="text-gray-700">{{ fmtTokens(node.data.input_tokens) }}</span>
                    <span class="text-gray-300 mx-1">/</span>
                    <span>{{ fmtTokens(node.data.output_tokens) }}</span>
                  </div>
                </template>
              </Column>
            </TreeTable>
          </template>

          <!-- Terminal view: flat log -->
          <template v-else-if="viewMode === 'terminal'">
            <SessionTerminalLog
              :spans="allSpans"
              :turns="turns"
              :selected-span="selectedSpan"
              @select-span="selectedSpan = $event"
              @fetch-content="fetchSpanContent"
              @load-subtree="ensureSpanSubtreeLoaded"
            />
          </template>
        </Card>
      </template>

      <aside
        v-if="selectedSpan"
        class="w-full lg:w-96 lg:shrink-0 lg:sticky lg:self-start lg:overflow-y-auto z-10"
        :style="{
          /* Page header is sticky-pinned with top: -1.5rem (lg padding-top)
             so its background covers .content-scroll padding-top. The
             sidebar pins flush under it: `header_h - 1.5rem + small gap`. */
          top: stickyHeaderHeight ? `calc(${stickyHeaderHeight}px - 1rem)` : '5rem',
          maxHeight: stickyHeaderHeight ? `calc(100vh - ${stickyHeaderHeight}px - 2rem)` : 'calc(100vh - 6rem)',
        }"
      >
        <Card v-if="selectedSpan">
          <h2 class="text-sm font-semibold text-slate-700 mb-3">Span details</h2>
          <div class="grid grid-cols-2 gap-3 text-sm mb-4">
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
                <button
                  v-if="selectedPromptNeedsExpand"
                  type="button"
                  class="text-[11px] font-medium text-blue-600 hover:text-blue-800 focus-visible:outline-2 focus-visible:outline-blue-500 rounded"
                  @click="promptExpanded = !promptExpanded"
                >
                  {{ promptExpanded ? 'Collapse' : 'Show full prompt' }}
                </button>
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
          <!-- Assistant response: render markdown above the attributes
               table because the text often spans many lines and looks
               cramped inside a two-column table cell. -->
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
          <!-- Rule check: header chip + per-rule pass/fail list. Replaces
               the bare `applicable_rules` JSON dump in the attributes
               table (hidden by visibleAttributeKeys). Severity shown as
               a coloured pill so violations stand out at a glance; the
               guide field, when present, links to the pattern doc that
               explains the rule. -->
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
                <!-- Mark-as-noise / un-mark control. Hidden on passing
                     rules (✓) — there's no fire to suppress. Only shown
                     when we resolved a backing rule_trigger row and the
                     user has editor+ role. -->
                <SuppressButton
                  v-if="canSuppressRule && ruleTriggersByRuleId[rule.id]"
                  class="shrink-0 ml-1"
                  :trigger-id="ruleTriggersByRuleId[rule.id].id"
                  :suppressed="ruleTriggersByRuleId[rule.id].suppressed"
                  :enabled="!!rule.violated"
                  @changed="loadTriggersForSelectedSpan"
                />
              </li>
            </ul>
          </div>

          <!-- Dynamic-workflow launch: jump to the captured run this
               Workflow tool call started. `workflow_run_id` is stamped on
               the span at ingest by matching the persisted script, so the
               jump only appears once the run has been captured. -->
          <div
            v-if="selectedSpan.name === 'tool.Workflow' && selectedSpan.attributes?.workflow_run_id"
            class="mb-4"
          >
            <router-link
              :to="`/trace/sessions/${selectedSpan.attributes.workflow_run_id}`"
              class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md border border-emerald-300 bg-emerald-50 text-sm font-medium text-emerald-700 hover:bg-emerald-100 no-underline focus-visible:outline-2 focus-visible:outline-emerald-500"
              title="Open the captured trace for this workflow run"
            >⚙ View workflow run →</router-link>
          </div>

          <!-- AskUserQuestion: render the Q&A round-trip as cards. Each
               question shows its options with the chosen one highlighted,
               and any user-added note appears below. Hidden from the
               generic attributes table by visibleAttributeKeys.
               Denied calls (synthesized by turn_trace when is_error=true)
               carry `attributes.denied` + `denial_reason` instead of
               answers — same panel, just a label flip. -->
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
            <!-- For a denied (or "chat about this") response, Claude
                 Code injects a templated prompt into the tool_result
                 telling the model the call was rejected and (sometimes)
                 quoting any user-typed note. This is NOT the user's
                 prose — the user just clicked a permission-dialog button
                 — so label it as "Claude Code injected prompt" rather
                 than "User said" to avoid misleading the reader. -->
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

          <!-- Generic-tool deny panel: same amber bar as the
               AskUserQuestion path, for any other denied tool (synth
               `tooldeny-*` from turn_trace — browser_evaluate, Bash,
               Edit, …). Skipped when the span IS AskUserQuestion to
               avoid double-rendering, since that path already includes
               its own denial_reason bar inside the Q&A card above. -->
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

        <Card class="mt-4">
          <div class="flex items-center justify-between mb-2">
            <button
              v-if="turns != null"
              type="button"
              class="flex items-center gap-1.5 text-sm font-semibold text-slate-700 hover:text-slate-900 focus-visible:outline-2 focus-visible:outline-blue-500"
              :title="turnsCollapsed
                ? (turnsStale ? 'Expand and refresh turns' : 'Expand turns list')
                : 'Collapse turns list'"
              @click="toggleTurnsCollapsed"
            >
              <span class="inline-block w-3 text-xs text-slate-400">{{ turnsCollapsed ? '▸' : '▾' }}</span>
              Turns
              <span
                v-if="turnsCollapsed && turnsStale"
                class="text-[10px] text-amber-600 font-normal"
                title="Spans were reloaded while turns were folded — they'll refresh on expand."
              >· stale</span>
            </button>
            <h2 v-else class="text-sm font-semibold text-slate-700">Turns</h2>
            <button
              v-if="turns == null"
              type="button"
              class="text-xs text-blue-600 hover:underline focus-visible:outline-2 focus-visible:outline-blue-500"
              :disabled="turnsLoading"
              @click="loadTurns"
            >{{ turnsLoading ? 'loading…' : 'load' }}</button>
            <span v-else class="text-xs text-gray-400">
              {{ turnsLoading ? 'loading…' : `${turns.length} turns` }}
            </span>
          </div>
          <p v-if="turns == null && !turnsLoading" class="text-xs text-gray-500">
            Per-API-call token usage. Click load to fetch.
          </p>
          <div v-else-if="turns && turns.length === 0 && !turnsCollapsed" class="text-xs text-gray-500">
            No turn-usage rows recorded for this session yet.
          </div>
          <ul v-else-if="turns && !turnsCollapsed" class="divide-y divide-gray-100">
            <li
              v-for="(t, i) in turns"
              :key="t.turn_uuid"
              :ref="(el) => storeTurnRow(t.turn_uuid, el)"
              class="py-1.5 cursor-pointer"
              :class="[
                selectedTurnUuid === t.turn_uuid
                  ? 'bg-indigo-50 -mx-2 px-2 rounded'
                  : 'hover:bg-gray-50 -mx-2 px-2 rounded',
              ]"
              @click="selectTurn(t.turn_uuid)"
            >
              <!-- Row 1: index · clock · duration · per-turn consumption bar+numbers · ctx% tag -->
              <div class="flex items-center gap-2 text-[11px] font-mono leading-tight">
                <span class="text-gray-400 w-6 text-right shrink-0">#{{ t.turn_index }}</span>
                <span class="text-gray-600 shrink-0"
                      :title="new Date(t.timestamp).toLocaleString()">{{ fmtLocalClock(t.timestamp) }}</span>
                <span class="text-gray-400 shrink-0 w-10 text-right"
                      :title="'time since the previous turn — counts tool-use round-trips'">
                  {{ t.duration_ms != null ? fmtDuration(t.duration_ms) : '—' }}
                </span>
                <!-- Per-turn consumption bar: width proportional to the
                     LARGEST fresh-in + out in this session, so the visual
                     ranking reads immediately. The numeric labels right
                     next to it are the actual per-turn costs the user
                     should be watching — cache_read is intentionally NOT
                     summed in, it's ~10% billed and dominates
                     `context_used_tokens`, drowning out every other
                     turn-to-turn signal. -->
                <span class="relative h-1.5 flex-1 bg-gray-100 rounded-sm overflow-hidden min-w-[2rem]"
                      :title="'fresh input + output this turn (bar scaled to session max)'">
                  <span
                    class="absolute inset-y-0 left-0 rounded-sm bg-indigo-500"
                    :style="{ width: turnConsumptionPct(t) + '%' }"
                  ></span>
                </span>
                <span class="text-gray-700 font-medium shrink-0 text-right tabular-nums"
                      :title="'fresh input this turn = input_tokens + cache_creation_tokens\n(the newly-billed input bytes; cache_read replays are ~10%)'">
                  ↑{{ fmtTokens(turnFreshInTokens(t)) }}
                </span>
                <span class="text-gray-700 font-medium shrink-0 text-right tabular-nums"
                      :title="'output_tokens — model-generated this turn'">
                  ↓{{ fmtTokens(t.output_tokens) }}
                </span>
                <!-- Trailing ctx% chip: the context-window gauge the
                     header badge also reports. Useful at a glance but
                     NOT the per-turn consumption signal, so it's small
                     and right-aligned. Server-side turns (advisor)
                     render with a striped border to flag that the %
                     reflects sub-call rollup, not main context. -->
                <span v-if="t.ctx_pct != null"
                      class="shrink-0 inline-flex items-center px-1 rounded text-[10px] gap-0.5"
                      :class="t.is_server_side
                                ? 'bg-slate-200 text-slate-700 ring-1 ring-dashed ring-slate-400'
                                : turnCtxClass(t.ctx_pct) + ' text-white'"
                      :title="t.is_server_side
                                ? 'advisor / server-side sub-call rollup — not main-conversation context (' + fmtTokens(t.context_used_tokens) + ' tokens charged to this turn)'
                                : 'context window used after this turn: ' + fmtTokens(t.context_used_tokens) + ' tokens'">
                  <span v-if="t.is_server_side" class="text-[9px] uppercase tracking-tight">sub</span>
                  {{ Math.round(t.ctx_pct) }}%
                </span>
                <!-- Reasoning-effort level the model ran at this turn
                     (set via the `effort` command, can change mid-session).
                     Explains output/reasoning-token size; only shown when
                     Claude Code reported it. -->
                <span v-if="t.effort_level"
                      class="shrink-0 inline-flex items-center px-1 rounded text-[10px] bg-violet-100 text-violet-700"
                      :title="'reasoning effort level for this turn: ' + t.effort_level">
                  {{ t.effort_level }}
                </span>
                <button
                  type="button"
                  class="text-slate-300 hover:text-slate-700 shrink-0 w-4 text-xs focus-visible:outline-2 focus-visible:outline-blue-500"
                  :aria-label="expandedTurnUuid === t.turn_uuid ? 'Collapse turn details' : 'Expand turn details'"
                  :title="expandedTurnUuid === t.turn_uuid ? 'collapse' : 'show per-span breakdown'"
                  @click.stop="toggleTurnExpanded(t.turn_uuid)"
                >{{ expandedTurnUuid === t.turn_uuid ? '−' : '+' }}</button>
              </div>
              <!-- Row 2: tool summary — chips colored to match tree view.
                   Tiny but the most useful "what happened in this turn" signal. -->
              <div v-if="t.tool_summary && t.tool_summary.length"
                   class="flex flex-wrap gap-1 mt-1 pl-8 text-[10px] leading-none">
                <span
                  v-for="ts in t.tool_summary"
                  :key="ts.name"
                  class="inline-flex items-center gap-0.5 px-1 py-0.5 rounded text-white/95"
                  :class="toolBadgeColor(ts.name)"
                >{{ ts.name }}<span v-if="ts.count > 1" class="opacity-75">×{{ ts.count }}</span></span>
                <span v-if="t.span_count === 0" class="text-gray-400 italic">no spans in this turn</span>
              </div>
              <div v-else-if="t.span_count === 0"
                   class="mt-1 pl-8 text-[10px] text-gray-400 italic leading-none">
                no spans in this turn
              </div>
              <!-- Drill-down: every span in this turn, labeled + clickable.
                   Clicking a span routes through the existing selectedSpan
                   path, so the details panel and strip highlight update
                   together. -->
              <div v-if="expandedTurnUuid === t.turn_uuid && t.span_refs && t.span_refs.length"
                   class="mt-1.5 ml-8 border-l border-gray-200 pl-2 space-y-0.5">
                <div
                  v-for="sr in t.span_refs"
                  :key="sr.span_id"
                  class="flex items-center gap-2 text-[10px] cursor-pointer hover:bg-white py-0.5 -mx-1 px-1 rounded"
                  :class="selectedSpan && selectedSpan.span_id === sr.span_id ? 'bg-white ring-1 ring-indigo-300' : ''"
                  @click.stop="handleSpanRefClick(sr)"
                >
                  <span
                    class="inline-block w-1.5 h-1.5 rounded-full shrink-0"
                    :class="barColor(sr.name)"
                  ></span>
                  <span class="text-gray-700 truncate flex-1">{{ sr.tool_name || sr.name }}</span>
                  <span class="text-gray-400 shrink-0 font-mono">{{ fmtLocalClock(sr.start_time) }}</span>
                </div>
              </div>
              <!-- Row 3 (expanded): full token breakdown the top row elides.
                   `in`, `cW`, `out` are per-turn; `cR` is the big cache replay
                   (~10% price) and `ctx` is the session-wide context window
                   that this turn observed. Tooltips explain the math for
                   anyone debugging cost from here. -->
              <div v-if="expandedTurnUuid === t.turn_uuid"
                   class="mt-1.5 ml-8 text-[10px] text-gray-500 font-mono grid grid-cols-5 gap-2">
                <div :title="'fresh (uncached) input this turn'">
                  <span class="text-gray-400">in</span> {{ fmtTokens(t.input_tokens) }}</div>
                <div :title="'cache_creation — new bytes written into the prompt cache this turn'">
                  <span class="text-gray-400">cW</span> {{ fmtTokens(t.cache_creation_tokens) }}</div>
                <div :title="'output_tokens — model-generated this turn'">
                  <span class="text-gray-400">out</span> {{ fmtTokens(t.output_tokens) }}</div>
                <div :title="'cache_read — replayed from prompt cache (~10% price, not a per-turn cost driver)'">
                  <span class="text-gray-400">cR</span> {{ fmtTokens(t.cache_read_tokens) }}</div>
                <div :title="'context_used = in + cR + cW — size of the prompt sent to the model this turn'">
                  <span class="text-gray-400">ctx</span> {{ fmtTokens(t.context_used_tokens) }}</div>
              </div>
            </li>
          </ul>
        </Card>
      </aside>
    </div>
    <!-- Infinite-feed-style footer: spinner during reload, otherwise
         a quiet end-of-timeline marker. Same pattern as Twitter/IG,
         no instructional text. -->
    <div class="mt-8 mb-4 flex items-center justify-center text-slate-400">
      <span v-if="reloading" class="inline-flex items-center gap-2 text-[12px]">
        <svg class="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none">
          <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="2" stroke-opacity="0.25"/>
          <path d="M22 12a10 10 0 0 1-10 10" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
        </svg>
        <span>Loading</span>
      </span>
      <span v-else class="text-[11px] tracking-wider uppercase">
        End of timeline
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
.trace-detail-root :deep(.p-treetable-thead > tr > th) {
  position: sticky;
  /* Page header pins at `top: -1rem` (mobile) / `-1.5rem` (desktop) so its
     opaque background covers `.content-scroll`'s padding-top; the thead
     pins flush below it, so subtract the same offset. */
  top: calc(var(--regin-trace-header-h, 0px) - 1rem);
  z-index: 5;
  background: #ffffff;
}
@media (min-width: 1024px) {
  .trace-detail-root :deep(.p-treetable-thead > tr > th) {
    top: calc(var(--regin-trace-header-h, 0px) - 1.5rem);
  }
}
</style>
