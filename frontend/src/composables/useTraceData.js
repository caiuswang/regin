import { ref, watch, nextTick } from 'vue'
import api from '../api'
import { dropRetiredSpans } from '../utils/traceFormatters.js'
import { isSyntheticSpanId } from './useSpanTree.js'
import { findNodeBySpanId, withNodeChildren } from '../utils/spanTree.js'
import { scrollSpanRowIntoView } from '../utils/scrollSpanRow.js'

// The trace data core: owns the loaded span tree + the append-only span list
// (kept on the SFC-owned `session.spans`) and every primitive that fetches,
// merges, paginates, or reconciles them. This is deliberately one cohesive
// unit — `reloadLiveTail` + `refreshRecentRootSubtrees` + `mergeLoadedSpans` +
// the `dropRetiredSpans` pruning are a single invariant (the dual-render-path
// sync) and must not be split across module edges.
//
// Shared refs are threaded in, never hoisted to module scope: `session` and
// `selectedSpan` are owned by the SFC and mutated here; `allSpans` is the
// cache-overlaid computed from useSpanContentCache.
//
// Initial load + load-older + scroll-to-bottom reload all walk a turn-anchor
// cursor (DB `id`). PAGE_SIZE matches the backend default clamp.
const PAGE_SIZE = 50

// Refresh the deep subtrees of the most recent N roots on every reload. Just
// refreshing the single latest misses spans that land under an *older* prompt
// after the user has moved on (a background Agent finishing late, a delayed
// PostToolUse). Refreshing the trailing window catches both without re-fetching
// the whole tree every reload.
const RELOAD_DEEP_REFRESH_TAIL = 3

// Session-summary fields the backend returns alongside each span page. The
// header reads these; without re-merging them every paginated reload the header
// would freeze at initial-load values even though the server re-queries them.
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
  'agent_roster',
  'server_now',
]

export function useTraceData(route, { session, allSpans, selectedSpan }) {
  const treeNodes = ref([])
  const loadingChildren = ref(new Set())
  // span_ids whose full subtree has been deep-loaded in one request — so a
  // repeat jump/selection into the same subtree doesn't refetch it.
  const subtreeLoaded = ref(new Set())

  // Pagination cursors.
  const hasMoreOlder = ref(false)
  const oldestLoadedId = ref(null)
  const newestLoadedId = ref(null)
  const loadingOlder = ref(false)

  // Terminal tab needs the FULL span list (not shallow), so the flat-log view
  // can render every event chronologically. Other tabs stay shallow to keep the
  // initial fetch cheap. We fetch the full map once and merge it in.
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
  // descend into. Deep-load the whole run subtree once (with attributes) so the
  // Conversation tab can project it into phase-sectioned chat.
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

  // Load the initial page of the session. Paginated by turn anchor (latest
  // PAGE_SIZE prompts). The scroll-to-bottom additive reload and scroll-to-top
  // load-older extend this incrementally — see reloadLiveTail() and loadOlder().
  async function loadSession() {
    const prevSelectedId = selectedSpan.value?.span_id

    const data = await api.get(
      `/sessions/${route.params.id}/map?shallow=1&limit=${PAGE_SIZE}`,
    )
    session.value = { ...data, spans: data.spans || [], server_now_at: Date.now() }
    treeNodes.value = data.tree || []
    hasMoreOlder.value = !!data.has_more_older
    oldestLoadedId.value = data.oldest_loaded_id ?? null
    newestLoadedId.value = data.newest_loaded_id ?? null
    workflowSpansLoaded.value = false
    await ensureWorkflowSpansLoaded()

    // Skip the chat-style default entirely when a `?span=` deep-link is
    // present: setting the latest prompt first makes the conversation spine
    // expand+scroll it, then the async deep-link resolves and jumps again —
    // a visible flash that reads as "jumped to the last prompt". Let the
    // deep-link own the initial selection.
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

    // `?span=<id>` deep-link wins over the chat-style default — used by the
    // /trace/triggers drawer to jump to the exact PostToolUse span that
    // recorded a rule trigger, and the run view's backlink.
    await applyDeepLinkSpan()
  }

  async function applyDeepLinkSpan() {
    const target = route.query.span
    if (!target) return
    let hit = allSpans.value.find(s => s.span_id === target)
    // Shallow load returns root prompts only — rule.check and other nested
    // spans aren't in allSpans until their owning subtree is expanded. Resolve
    // the root via /ancestors, then load just that subtree (cheaper than the
    // full-map fetch on large sessions).
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
        // Span genuinely missing or transient error — fall through to the
        // no-op return below; the page still renders with the chat-style
        // default selection.
      }
    }
    if (!hit) return
    selectedSpan.value = hit
    nextTick(() => scrollSpanRowIntoView(hit.span_id))
  }

  // Merge the session-summary fields the backend returns alongside the span
  // page into `session.value` so the header stays live across reloads.
  function applySessionSummary(data) {
    if (!session.value || !data) return
    const patch = {}
    for (const k of SESSION_SUMMARY_KEYS) {
      if (k in data) patch[k] = data[k]
    }
    // Client-clock stamp of when server_now landed — elapsed readouts use
    // only DELTAS of each clock (see useAgentElapsed), never a cross-clock
    // subtraction, so a viewer in another timezone never leaks the offset.
    if ('server_now' in data) patch.server_now_at = Date.now()
    session.value = { ...session.value, ...patch }
  }

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
      // accumulated tool calls / assistant_response spans since. withNodeChildren
      // re-derives leaf from the response, so a genuinely empty subtree just
      // stays leaf=true.
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
  // retired and replaced by its real prompt-<uuid> anchor), re-fetch the latest
  // page and reconcile the recent tail against it. The fresh page is the DB's
  // source of truth for the recent window, so a retired placeholder (absent) is
  // dropped and its real anchor (present) takes its place: no missing, no
  // duplicate, no drift. Older immutable history (loaded via loadOlder) is kept
  // untouched, and a root that persists reuses its already-loaded subtree.
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
    // resolved span). mergeLoadedSpans never removes, so without this the
    // conversation cards (which read session.spans) keep a duplicate next to the
    // resolved card. retired_span_ids is the server's authoritative drop list.
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

  // Reverse-pagination: pull the next page of older roots and prepend them to
  // the tree. Preserves the user's scroll position by capturing scrollTop +
  // scrollHeight before the DOM mutation and restoring scrollTop after.
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
      // Wait for DOM update, then restore visible scroll position so the
      // user's viewport stays anchored to what they were reading.
      await nextTick()
      const delta = scroller.scrollHeight - prevScrollHeight
      if (delta > 0) {
        scroller.scrollTop = prevScrollTop + delta
      }
    } finally {
      loadingOlder.value = false
    }
  }

  function mergeLoadedSpans(spans) {
    if (!session.value || !spans?.length) return
    const byId = new Map((session.value.spans || []).map(s => [s.span_id, s]))
    for (const s of spans) {
      const prev = byId.get(s.span_id) || {}
      // /map (non-shallow) omits the `attributes` field for descendants, so
      // they get cached as `attributes: {}`. A later /children?recursive=1
      // fetch carries the real attributes; we must take them over the empty
      // placeholder. `prev.attributes || s.attributes` is wrong because `{}` is
      // truthy — it keeps the placeholder and discards the real attrs.
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

  async function ensureSpanSubtreeLoaded(rootSpanId, { force = false } = {}) {
    // Synthesized scoped-task prompt ids exist only client-side — 404 upstream.
    if (!rootSpanId || isSyntheticSpanId(rootSpanId)) return
    if (!force && subtreeLoaded.value.has(rootSpanId)) return
    if (loadingChildren.value.has(rootSpanId)) return
    // One `deep=1` fetch returns the whole nested subtree. The previous BFS
    // fired a shallow `/children` per non-leaf node, which exploded into
    // hundreds of requests once a span had many children. The TreeTable still
    // renders lazily via expandedKeys, so grafting the full subtree is cheap.
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

  // Force-refetch a subtree that already settled: a RUNNING scoped agent keeps
  // growing new spans under its start marker, and the trailing-roots refresh in
  // refreshRecentRootSubtrees misses agents anchored under older prompts.
  const refreshSpanSubtree = (rootSpanId) =>
    ensureSpanSubtreeLoaded(rootSpanId, { force: true })

  // Live link from rule-trigger event rows: changing ?span= without changing
  // /sessions/<id>/ keeps the user on the same session and just re-selects.
  watch(() => route.query.span, (v) => {
    if (v) applyDeepLinkSpan()
  })

  return {
    treeNodes, loadingChildren, subtreeLoaded,
    hasMoreOlder, oldestLoadedId, newestLoadedId, loadingOlder,
    loadSession, applyDeepLinkSpan, applySessionSummary,
    refreshRecentRootSubtrees, reloadLiveTail, loadOlder,
    mergeLoadedSpans, ensureNodeChildrenLoaded, ensureSpanSubtreeLoaded,
    refreshSpanSubtree,
    ensureTerminalSpansLoaded, ensureWorkflowSpansLoaded,
  }
}
