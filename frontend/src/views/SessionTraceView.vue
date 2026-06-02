<script setup>
import { ref, onMounted, onUnmounted, computed, watch, nextTick } from 'vue'
import { useRoute } from 'vue-router'
import api from '../api'
import Card from '../components/Card.vue'
import MarkdownContent from '../components/MarkdownContent.vue'
import SessionTerminalLog from '../components/SessionTerminalLog.vue'
import SessionConversationView from '../components/SessionConversationView.vue'
import SuppressButton from '../components/triggers/SuppressButton.vue'
import { dropRetiredSpans } from '../utils/traceFormatters.js'
import { useTraceScroll } from '../composables/useTraceScroll.js'
import ToolTokenRollup from '../components/ToolTokenRollup.vue'
import SessionTraceHeader from '../components/SessionTraceHeader.vue'
import TraceOverviewStrip from '../components/TraceOverviewStrip.vue'
import SpanDetailPanel from '../components/SpanDetailPanel.vue'
import { findNodeBySpanId, findNodePath, findNodeKey, withNodeChildren } from '../utils/spanTree.js'
import SessionTurnsSidebar from '../components/SessionTurnsSidebar.vue'
import SessionTimelineTree from '../components/SessionTimelineTree.vue'

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

const expandedKeys = ref({})
const selectedKeys = ref({})   // PrimeVue TreeTable v-model:selection-keys
const treeNodes = ref([])
const loadingChildren = ref(new Set())
// span_ids whose full subtree has been deep-loaded in one request — so a
// repeat jump/selection into the same subtree doesn't refetch it.
const subtreeLoaded = ref(new Set())

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
    (s) => s.name === 'session.start' && s.attributes?.run_id != null,
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

// Dynamic-workflow runs this session launched (its `tool.Workflow` calls,
// each stamped with workflow_run_id + name at ingest). Surfaced as a
// header pivot chip mirroring `plans`: N=1 inlines as `⚙ <name>` linking
// to the run; N≥2 collapses to `workflows N` with click-to-expand.
const workflowRuns = ref([])
// run_id → enriched run record (agent_count, phase_count, status, tokens),
// so the inline `tool.Workflow` spine row and the detail panel can render a
// rich collapsed summary without each opening the run's trace.
const workflowRunsById = computed(() => {
  const map = {}
  for (const r of workflowRuns.value) map[r.run_id] = r
  return map
})

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

// Scroll/wheel/touch-driven auto-reload (pull-to-refresh at the bottom,
// pull-older at the top). The composable owns the DOM mechanics + latches and
// attaches its own document listeners on mount; we hand it the loader
// callbacks and the gating refs it reads.
useTraceScroll({ reloading, loading, loadingOlder, hasMoreOlder, reload, loadOlder })

// General live poll. The trace view is a live dashboard but `reload()`
// otherwise only fires on scroll/wheel — so a user parked at the bottom
// watching their session never sees updates (and any transient duplicate
// from a placeholder→anchor handoff never gets reconciled away) until they
// scroll. A lightweight visibility-gated tick keeps the reconcile
// (`reloadLiveTail`) converging the tail to the DB every few seconds.
let livePollTimer = null
const LIVE_POLL_MS = 4000
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

onMounted(async () => {
  const rollupP = fetchToolRollup()
  const plansP = fetchPlans()
  const wfRunsP = fetchWorkflowRuns()
  await loadSession()
  loading.value = false
  await Promise.all([rollupP, plansP, wfRunsP])
  startLivePoll()
  if (viewMode.value === 'terminal') ensureTerminalSpansLoaded()
  // Scroll/wheel/touch auto-reload listeners are attached by useTraceScroll().
  // Wait for the v-else branch to render the sticky header element before
  // attaching the observer (the empty-state branches render different DOM).
  await nextTick()
  attachStickyHeaderObserver()
})

onUnmounted(() => {
  // Scroll/wheel/touch listeners are detached by useTraceScroll().
  if (stickyHeaderRO) { stickyHeaderRO.disconnect(); stickyHeaderRO = null }
  stopCompactWatch()
  stopLivePoll()
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

  // Skip the chat-style default entirely when a `?span=` deep-link is
  // present: setting the latest prompt first makes the conversation spine
  // expand+scroll it, then the async deep-link resolves and jumps again —
  // a visible flash that reads as "jumped to the last prompt" (e.g. the
  // workflow run's "↑ launched from session" backlink, which targets the
  // tool.Workflow span). Let the deep-link own the initial selection.
  if (!route.query.span) {
    if (prevSelectedId) {
      const fresh = allSpans.value.find(s => s.span_id === prevSelectedId)
      // Default to the LATEST root (most recent prompt) — chat-style.
      selectedSpan.value = fresh
        || allSpans.value[allSpans.value.length - 1]
        || null
    } else if (allSpans.value.length) {
      selectedSpan.value = allSpans.value[allSpans.value.length - 1]
    }
  }

  // `?span=<id>` deep-link wins over the chat-style default — used by
  // the /trace/triggers drawer to jump to the exact PostToolUse span
  // that recorded a rule trigger, and the run view's backlink.
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
  'queued_prompts',
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

// Live tail reconcile. Instead of blindly appending new roots (which drifts
// from the DB whenever a row is mutated — chiefly the promptlive- placeholder
// that's retired and replaced by its real prompt-<uuid> anchor, a different
// row id), re-fetch the latest page and reconcile the recent tail against it.
// The fresh page is the DB's source of truth for the recent window, so a
// retired placeholder (absent from it) is dropped and its real anchor
// (present) takes its place: no missing, no duplicate, no drift. Older
// immutable history (loaded via loadOlder) is kept untouched, and a root that
// persists across the tick reuses its already-loaded subtree.
async function reloadLiveTail() {
  if (newestLoadedId.value == null) {
    // No cursor yet (initial load failed?) — fall back to a full load.
    await loadSession()
    return
  }
  const data = await api.get(
    `/sessions/${route.params.id}/map?shallow=1&limit=${PAGE_SIZE}`,
  )
  const freshTail = data.tree || []
  mergeLoadedSpans(data.spans || [])
  // Prune placeholders the serve-time merge has since dropped from this window
  // (prompt promoted to its anchor; pending tool/permission superseded by its
  // resolved span). `mergeLoadedSpans` never removes, so without this the
  // conversation cards (which read `session.spans`) keep a duplicate next to
  // the resolved card — the bug the `treeNodes` reconcile alone never reached.
  // `retired_span_ids` is the server's authoritative drop list for the window.
  if (session.value) {
    const pruned = dropRetiredSpans(session.value.spans || [], data.retired_span_ids)
    if (pruned.length !== (session.value.spans || []).length) {
      session.value = { ...session.value, spans: pruned }
    }
  }
  applySessionSummary(data)
  if (!freshTail.length) {
    await refreshRecentRootSubtrees(treeNodes.value)
    return
  }

  // The fresh page owns every root with id >= minFreshId; keep the older
  // nodes (immutable, below the window) exactly as they are.
  const freshIds = freshTail.map(n => n.data?.id).filter(v => v != null)
  const minFreshId = freshIds.length ? Math.min(...freshIds) : 0
  const older = treeNodes.value.filter(n => (n.data?.id ?? Infinity) < minFreshId)

  // Reuse an existing node when its span_id persists, so its already-loaded
  // subtree survives — only refresh the shallow root data + leaf flag.
  const existingBySpan = new Map(treeNodes.value.map(n => [n.data?.span_id, n]))
  const reconciled = freshTail.map((fresh) => {
    const prev = existingBySpan.get(fresh.data?.span_id)
    if (prev && prev.children && prev.children.length) {
      return { ...prev, data: fresh.data, leaf: fresh.leaf }
    }
    return fresh
  })

  treeNodes.value = [...older, ...reconciled]
  if (data.newest_loaded_id != null) newestLoadedId.value = data.newest_loaded_id
  if (older.length === 0) {
    // No older-than-page history is loaded, so the fresh page's cursors are
    // authoritative; otherwise leave the loaded-older bookkeeping untouched.
    if (data.oldest_loaded_id != null) oldestLoadedId.value = data.oldest_loaded_id
    hasMoreOlder.value = !!data.has_more_older
  }

  // Deep-refresh the recent tail of prompts — children may still be streaming
  // under the active prompt (or a prior prompt whose subagent finished late).
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
    const tasks = [reloadLiveTail(), fetchToolRollup()]
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
  s => s.name === 'session.start' && s.attributes?.run_id != null,
) || null)

// Set when the rendered tree is a *stale* manifest snapshot: the run has
// resumed and progressed past the snapshot, but the runtime only flushes the
// manifest at pause/completion, so phases/counts here lag reality and can't be
// refreshed from disk. The header surfaces this so the view doesn't look
// current. Value is the ISO time the snapshot was taken.
const snapshotStaleAt = computed(
  () => workflowRoot.value?.attributes?.snapshot_stale_at || null)

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
  if (!rootSpanId || subtreeLoaded.value.has(rootSpanId)) return
  if (loadingChildren.value.has(rootSpanId)) return
  // One `deep=1` fetch returns the whole nested subtree. The previous BFS
  // fired a shallow `/children` per non-leaf node, which exploded into
  // hundreds of requests once a span had many children (e.g. a tool.Workflow
  // span with its re-parented workflow agents). The TreeTable still renders
  // lazily via expandedKeys, so grafting the full subtree up front is cheap.
  loadingChildren.value.add(rootSpanId)
  try {
    const data = await api.get(
      `/sessions/${route.params.id}/spans/${rootSpanId}/children?deep=1`,
    )
    mergeLoadedSpans(data.spans || [])
    treeNodes.value = withNodeChildren(treeNodes.value, rootSpanId, data.children || [])
    subtreeLoaded.value.add(rootSpanId)
  } finally {
    loadingChildren.value.delete(rootSpanId)
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

// Server-side aggregate; see web/trace_projection._compute_active_work_ms
// for the gap-based definition. Always populated since migration 0004 +
// the backfill, so no client-side recomputation is needed.
const activeWorkMs = computed(() => session.value?.active_work_ms ?? 0)

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

// Largest (input + cache_creation + output) across the session, for
// scaling per-row consumption bars. Populated from the API response
// so the client doesn't reduce over the full turn list on every render.
const maxTurnConsumption = ref(0)

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

function fmtTime(iso) {
  if (!iso) return '-'
  const d = new Date(iso)
  return d.toLocaleTimeString() + '.' + String(d.getMilliseconds()).padStart(3, '0')
}

function fmtDate(iso) {
  if (!iso) return '-'
  return new Date(iso).toLocaleString()
}

// Match the thresholds used by ~/.claude/statusline-command.sh so the
// badge color here agrees with what the user sees in their terminal.

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
    />

    <ToolTokenRollup :rollup-data="toolRollupData" />

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
      <!-- Conversation view: rendered outside Card so its sidebar can be sticky -->
      <template v-if="viewMode === 'conversation'">
        <SessionConversationView
          :spans="allSpans"
          :turns="turns"
          :selected-span="selectedSpan"
          :trace-id="session?.trace_id"
          :context-window-tokens="session?.context_window_tokens"
          :workflow-runs-by-id="workflowRunsById"
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
        <SpanDetailPanel
          :key="selectedSpan && selectedSpan.span_id"
          :selected-span="selectedSpan"
          :rule-triggers-by-rule-id="ruleTriggersByRuleId"
          :can-suppress-rule="canSuppressRule"
          :workflow-runs-by-id="workflowRunsById"
          @suppress-changed="loadTriggersForSelectedSpan"
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
