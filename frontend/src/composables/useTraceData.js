import { ref, watch, nextTick } from 'vue'
import api from '../api'
import {
  dropRetiredSpans, dropStalePendingSpans, isPendingPlaceholderId,
} from '../utils/traceFormatters.js'
import { isSyntheticSpanId } from './useSpanTree.js'
import { findNodeBySpanId, withNodeChildren } from '../utils/spanTree.js'
import { scrollSpanRowIntoView } from '../utils/scrollSpanRow.js'
import { getTraceScroller } from '../utils/traceScroller.js'

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

// Ids ride in the `/subtrees` query string, so the request line grows with the
// batch. 50 × a 63-char span id sits comfortably inside a default server line
// limit; a whole back-paged session's worth would not.
const SUBTREE_IDS_PER_REQUEST = 50

// Every identifier the tail's DERIVED data hangs off, in one string.
//
// `session_spans` is append-only (all dedup/reparent happens in the serve-time
// merge), so nothing can change under a loaded root without moving
// `span_count_total` or the merge's retired list. The token counters are the
// transcript's, and they move whenever the live rescan ingests a turn — which
// is precisely what `turn-usage` and `tool-rollup` read.
//
// So an unchanged fingerprint means none of the three follow-up requests a
// reconcile fires — the deep-subtree refresh, the tool rollup, the turn usage
// — could return anything new, and a quiet tick costs one /map instead of six.
const TAIL_FINGERPRINT_KEYS = [
  'newest_loaded_id', 'span_count_total',
  'input_tokens', 'output_tokens',
  'cache_read_tokens', 'cache_creation_tokens',
  'active_work_ms',
]

// One accumulator for the whole recursion, and a signature so a live poll
// re-hydrating the same broken response every tick says it once rather than
// forever. Both are about the WARNING below, not the join.
let lastDropWarning = ''

// Rejoin the two halves of a structural response.
//
// The wire ships each span body ONCE, in `spans`, and the tree names its spans
// by id (see `_wire_nodes` in web/blueprints/trace/sessions.py). Everything
// downstream — the TreeTable, `findNodePath`, `withNodeChildren`, the reconcile
// in `reloadLiveTail` — reads `node.data`, so the join happens here, at the
// edge, and the node shape the rest of the app sees is unchanged.
//
// A node's own fields win over the span's: a group's `start_time`/`end_time`
// are its whole subtree's envelope, not the group span's own pair.
export function hydrateNodes(nodes, byId, drops = null) {
  const top = drops === null
  const tally = drops || { dropped: 0, total: 0 }
  const out = []
  tally.total += (nodes || []).length
  for (const n of nodes || []) {
    const span = byId.get(n.span_id)
    // A node whose span did not ride along is unrenderable — every consumer
    // reads `data.span_id` first and would throw on the undefined.
    if (!span) { tally.dropped++; continue }
    const data = { ...span }
    if (n.isGroup) {
      data.isGroup = true
      if (n.start_time) data.start_time = n.start_time
      if (n.end_time) data.end_time = n.end_time
    }
    if (n.isSpan) data.isSpan = true
    if (n.child_count != null) data.child_count = n.child_count
    const node = { key: n.key, data }
    if ('leaf' in n) node.leaf = n.leaf
    if (n.children) node.children = hydrateNodes(n.children, byId, tally)
    out.push(node)
  }
  // Silence here reads as "this session is empty". A server still serving the
  // pre-join wire shape sends no `span_id` at all, so EVERY node drops and the
  // whole trace view goes blank with nothing to point at.
  if (top && tally.dropped) {
    const warning =
      `[trace] dropped ${tally.dropped}/${tally.total} tree nodes with no span body` +
      ' — stale server? restart `regin serve`'
    if (warning !== lastDropWarning) {
      lastDropWarning = warning
      console.warn(warning)
    }
  } else if (top) {
    lastDropWarning = ''
  }
  return out
}

// Index a response's `spans` for the rejoin above.
function spanIndex(data) {
  return new Map((data?.spans || []).map(s => [s.span_id, s]))
}

// The same index, backed by the bodies this client already holds.
//
// The subtree refresh withholds the body of any span it can prove we already
// have, so most nodes in its tree name a span that is not in the response.
// Those rejoin against our own store; only the ones that actually moved ride
// the wire.
function spanIndexOver(data, existing) {
  const byId = new Map((existing || []).map(s => [s.span_id, s]))
  for (const s of (data?.spans || [])) byId.set(s.span_id, s)
  return byId
}

// Highest DB row id among the descendants already loaded under `node`.
//
// This is the watermark the server checks: a span we have never seen is
// necessarily above it, because a new span is an INSERT and takes a fresh id.
// Null when nothing is loaded under the root, which asks for every body.
function loadedWatermark(node) {
  let max = null
  const walk = (children) => {
    for (const child of children || []) {
      const id = child.data?.id
      if (typeof id === 'number' && (max === null || id > max)) max = id
      walk(child.children)
    }
  }
  walk(node?.children)
  return max
}

// A subagent's rows are served under `sa-<agent_id>` (lib/trace/subagent_trace
// .py); the conversation scopes on the bare agent id.
const SUBAGENT_TRACE_PREFIX = 'sa-'
function agentIdOfChildTrace(traceId) {
  return (typeof traceId === 'string' && traceId.startsWith(SUBAGENT_TRACE_PREFIX))
    ? traceId.slice(SUBAGENT_TRACE_PREFIX.length) || null
    : null
}

// Guarantee `attributes` — and one body per span_id — on every span entering
// `session.spans`.
//
// Templates read `span.attributes.x` unguarded, and the structural map strips
// the bag entirely from a span with no kept keys. `allSpans` used to normalise
// on every read, which meant cloning every span on every poll; doing it once
// on the way in costs an allocation only for the spans actually missing it.
function normalizeSpans(spans) {
  const byId = new Map()
  for (const s of spans || []) {
    byId.set(s.span_id, s.attributes ? s : { ...s, attributes: {} })
  }
  return [...byId.values()]
}

// Would re-merging `next` over `prev` produce anything new?
//
// Conservative by construction: a non-primitive attribute value (a questions
// list, a rules array) is a fresh reference on every response and so reads as
// changed. That costs a wasted write on those spans and never a missed one,
// which is the right way round — the tail reconcile must not go blind.
function spanUnchanged(prev, next, attributes) {
  for (const k in next) {
    if (k !== 'attributes' && prev[k] !== next[k]) return false
  }
  return attributesUnchanged(prev.attributes, attributes)
}

function attributesUnchanged(prev, next) {
  if (prev === next) return true
  if (!prev || !next) return false
  const keys = Object.keys(next)
  if (keys.length !== Object.keys(prev).length) return false
  return keys.every(k => prev[k] === next[k])
}

function tailFingerprint(data) {
  const parts = TAIL_FINGERPRINT_KEYS.map(k => data?.[k] ?? '')
  // The drop list's VERSION, not its length: the response omits the list
  // itself once we have applied that version, and counting a list that isn't
  // there would read as a change on every quiet tick — costing exactly the
  // follow-up requests the fingerprint exists to avoid.
  parts.push(data?.retired_digest ?? '')
  return parts.join('|')
}

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
  // `status` is the FIRST thing sessionLiveness() reads and it short-circuits
  // on it, so refreshing `ended_at` beside a pinned `status` changes nothing.
  // `ended_reason` rides with it: it tells a manual close ('closed') from a
  // lifecycle end. The /live card's allowlist (useLiveTail.js `applySummary`)
  // carries both; this list is its desktop sibling and must not drift from it.
  'status', 'ended_reason',
  // The runner's own verdict for a run regin launched (null otherwise) — what
  // lets the pill say `idle` for an agent parked at the prompt instead of
  // ageing it out to `inactive`. Refreshed, not pinned: a run that finishes
  // mid-view stops having one.
  'runner_phase',
  'title', 'title_source',
  'display_title', 'display_title_source', 'task',
  'span_count_total',
  // How many of `span_count_total` a child trace serves instead of this view
  // — the vitals strip names the difference rather than leaving it a gap.
  // Refreshed, not pinned: it grows with every agent the session launches.
  'span_count_delegated',
  'task_list',
  'queued_prompts',
  'agent_roster',
  // The shell roster, for the same reason as `agent_roster`: it is
  // whole-session and server-owned, so it changes without any span this
  // client watches changing — a shell settles when a notification lands
  // elsewhere. Pinned to the page-load row, the foot-of-feed block went on
  // claiming a killed shell was running, elapsed ticking upward.
  //
  // Deliberately NOT added to useLiveTail's sibling allowlist: the /live card
  // renders no shell block, so carrying it there would ship a payload nothing
  // reads.
  'background_shells',
  'server_now',
  'server_now_utc',
  // Steerability gates the conversation feed's follow-up composer; pinned to
  // the page-load row it would keep offering a send into a session that has
  // since gone unreachable.
  'bridge_reachable',
  // Rides with it: the pane label the raw-terminal peek footer names. Left
  // off this list it froze at its page-load value while `bridge_reachable`
  // refreshed beside it.
  'bridge_pane',
  // The terminal's own verdict on what this session is doing — the spine's
  // live node renders it. Anchored to the session, not to any span, so it
  // survives the hook-silent stretches a PENDING row cannot cover.
  'live_screen',
  // The id this session is really keyed on — the child's, for a regin-launched
  // run. Refreshed rather than pinned because it CHANGES under the page: a run
  // launched from this view is navigated to under its own `sdk-…` id, the only
  // one that exists before the child names itself, so the first /map reports
  // that id as canonical and only a later poll can name the child.
  'canonical_trace_id',
  // Set when this session ended but its run is still going — `/clear` opens a
  // new conversation under a new id. It keeps the composer alive on the page
  // the operator is already on; without it here the server field never
  // reaches the view.
  'successor_trace_id',
  // The mirror of `bridge_reachable` for a session nothing can steer: whether
  // the CLI could still reopen it. Refreshed rather than pinned because the
  // answer changes under the page — a run stopped elsewhere becomes resumable,
  // and one relaunched stops being.
  'resumable',
]

export function useTraceData(route, { session, allSpans, selectedSpan, router }) {
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
  // Last tail fingerprint observed by a reconcile. Plain `let`: nothing
  // renders it, and a ref would make every quiet tick a reactive write.
  let lastTailFingerprint = null

  // Push-stream continuity, which is what licenses the subtree refresh to
  // leave bodies out.
  //
  // The fan-out sheds frames under load and re-sends nothing (`hub._offer`),
  // so "I received a frame" is not evidence of having received them all.
  // `expectedEpoch` is our tally of changes seen; every /map response carries
  // the server's authoritative count. They agree only while the stream has
  // delivered everything — and a writer that cannot name the spans it touched
  // still advances the server's count, so this catches those too. Any
  // disagreement sets `resyncNeeded`, and the next refresh asks for full
  // bodies exactly as it did before any of this existed.
  let expectedEpoch = null
  // Version of the merge's drop list we have already pruned against. Echoed
  // back so the server can skip re-sending a list we have applied — it runs to
  // thousands of ids on a long session and none of them can change.
  let appliedRetiredDigest = null
  // Structure version applied per root, so a subtree whose shape has not moved
  // is not serialised to us again. The trailing prompts are asked about on
  // every reconcile and the older ones have usually finished for good.
  const appliedSubtreeDigests = new Map()
  let resyncNeeded = true
  // Bumped every time a resync is raised, so a refresh already in flight can
  // tell "the resync I was sent to satisfy" from one raised while it ran.
  let resyncGeneration = 0
  const dirtySpanIds = new Set()

  function requireResync() {
    resyncNeeded = true
    resyncGeneration += 1
  }

  // A pushed frame: accept it only if its arithmetic closes.
  //
  // The frame quotes the server's LIVE counter, so it already contains any
  // bump raised since our last one — including a writer that changed spans
  // without naming them (a reconciler, a materialize reparent, a token
  // attribution). Simply banking the number would let such a bump hide inside
  // the next ordinary frame and read as EQUAL against /map, which is a change
  // we never localised and would never re-fetch.
  //
  // So require the counter to have moved by exactly the number of spans this
  // frame accounts for. Anything left over is a change nobody named, and the
  // only safe answer is to re-ask for everything.
  function noteSpanFrame(frame) {
    const epoch = frame?.epoch
    const ids = frame?.span_ids
    if (typeof epoch !== 'number' || !Array.isArray(ids)
        || expectedEpoch === null) {
      requireResync()
      if (typeof epoch === 'number') expectedEpoch = epoch
      return
    }
    if (epoch - expectedEpoch !== ids.length) {
      requireResync()
      expectedEpoch = epoch
      return
    }
    expectedEpoch = epoch
    for (const id of ids) dirtySpanIds.add(id)
  }

  // The authoritative count, on every reconcile. THIS is the gate.
  //
  // Equal means every change the server recorded also reached us as a frame,
  // so the spans we hold are current except for the ones those frames named.
  // Anything else — a shed frame, a reconnect, a writer that named no spans,
  // a restarted server — is a change we cannot localise, so we give up the
  // cheap path for one tick. Rebasing afterwards is what makes the recovery
  // self-healing rather than permanent.
  function noteServerEpoch(epoch) {
    if (typeof epoch !== 'number' || expectedEpoch !== epoch) requireResync()
    if (typeof epoch === 'number') expectedEpoch = epoch
  }

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
    session.value = {
      ...data, spans: normalizeSpans(data.spans), server_now_at: Date.now(),
    }
    treeNodes.value = hydrateNodes(data.tree, spanIndex(data))
    hasMoreOlder.value = !!data.has_more_older
    oldestLoadedId.value = data.oldest_loaded_id ?? null
    newestLoadedId.value = data.newest_loaded_id ?? null
    // A fresh load owes no resync — everything on screen came from this
    // response — so start the continuity chain from its epoch.
    appliedRetiredDigest = data.retired_digest ?? null
    appliedSubtreeDigests.clear()
    expectedEpoch = typeof data.span_epoch === 'number' ? data.span_epoch : null
    if (expectedEpoch === null) requireResync()
    else { resyncNeeded = false; resyncGeneration += 1 }
    dirtySpanIds.clear()
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
        // The span belongs to a subagent this view delegates: no ancestor
        // chain here can reveal it, because its rows are served by the
        // agent's own trace. Enter that agent's scope on THIS page (what
        // "Agent view →" does) — `?agent=` drives the scoped surface, which
        // resolves the `?span=` against the spans it fetched.
        const agentId = agentIdOfChildTrace(a?.child_trace_id)
        if (agentId) {
          router.replace({ query: { ...route.query, agent: agentId } })
          return
        }
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
    // Don't trust the cached `leaf` flag here. It's a snapshot from the
    // initial shallow load — a prompt that was childless then may have
    // accumulated tool calls / assistant_response spans since. withNodeChildren
    // re-derives leaf from the response, so a genuinely empty subtree just
    // stays leaf=true.
    const tail = sorted.slice(-RELOAD_DEEP_REFRESH_TAIL)
      .filter(n => n?.data?.span_id)
    const ids = tail.map(n => n.data.span_id)
    if (!ids.length) return
    // ONE request, and so one server-side projection, for the whole trailing
    // window. Asked per root it re-merged the entire session once per id.
    //
    // The watermarks are what keep it small: they tell the server which
    // bodies we already hold, and it sends back only what moved. They go only
    // when the push stream has demonstrably delivered every change since our
    // last sync — otherwise we ask for everything, which is what the endpoint
    // does with no watermarks at all.
    const params = [`ids=${ids.map(encodeURIComponent).join(',')}`]
    const trusted = !resyncNeeded
    const generation = resyncGeneration
    const sentDirty = trusted ? [...dirtySpanIds] : []
    if (trusted) {
      // Only for a root whose subtree was loaded WHOLE. A lazy expand
      // (`ensureNodeChildrenLoaded`) grafts direct children and no deeper, so
      // its highest child id says nothing about the grandchildren — and a
      // grandchild sorting below that id would be withheld as "already held"
      // when it had never been fetched, leaving a hole in the rendered tree.
      const known = tail
        .filter(n => subtreeLoaded.value.has(n.data.span_id))
        .map(n => [n.data.span_id, loadedWatermark(n)])
        .filter(([, w]) => w !== null)
        .map(([id, w]) => `${encodeURIComponent(id)}:${w}`)
      if (known.length) params.push(`known=${known.join(',')}`)
      if (sentDirty.length) {
        params.push(`dirty=${sentDirty.map(encodeURIComponent).join(',')}`)
      }
      const seen = ids
        .filter(id => appliedSubtreeDigests.has(id))
        .map(id => `${encodeURIComponent(id)}:${appliedSubtreeDigests.get(id)}`)
      if (seen.length) params.push(`seen=${seen.join(',')}`)
    }
    try {
      const data = await api.get(
        `/sessions/${route.params.id}/subtrees?${params.join('&')}`,
      )
      mergeLoadedSpans(data.spans || [])
      // Only now, and only for what this request actually covered: a failure
      // must leave the debt standing, and a frame that landed WHILE it was in
      // flight describes a change this response could not have carried.
      if (generation === resyncGeneration) resyncNeeded = false
      for (const id of sentDirty) dirtySpanIds.delete(id)
      const byId = spanIndexOver(data, session.value?.spans)
      const unchanged = new Set(data.unchanged || [])
      for (const spanId of ids) {
        // Its shape is what we already hold and nothing under it moved.
        if (unchanged.has(spanId)) { subtreeLoaded.value.add(spanId); continue }
        // A root the projection no longer knows (retired placeholder, or
        // reparented under another node between polls) is absent from
        // `subtrees`, and must CLEAR its children rather than keep them:
        // the per-root endpoint this replaced returned `[]` for that case,
        // and skipping it leaves a stale subtree expanded in the timeline.
        const children = data.subtrees?.[spanId] || []
        treeNodes.value = withNodeChildren(
          treeNodes.value, spanId, hydrateNodes(children, byId))
        // Whole now, either way: a root we sent no watermark for was answered
        // in full, and one we did was answered with everything that moved. So
        // the NEXT tick may quote a watermark for it.
        subtreeLoaded.value.add(spanId)
        const digest = data.digests?.[spanId]
        if (digest) appliedSubtreeDigests.set(spanId, digest)
        else appliedSubtreeDigests.delete(spanId)
      }
    } catch (_) {
      // A stale tail shouldn't abort the whole reload — the next tick retries.
    }
  }

  // Converge the append-only `session.spans` (what the conversation cards read)
  // to the server's reconciliation. `mergeLoadedSpans` only ever grows it, and
  // TWO rules shrink it — the timeline applies both to `treeNodes`, and the
  // cards were applying only the first:
  //
  //  * `retired_span_ids`, the authoritative drop list. Absent means "the
  //    version you already applied", so there is nothing to redo.
  //  * placeholders below the fresh window, which that list structurally cannot
  //    name (see `dropStalePendingSpans`). Without this a resolved tool call
  //    whose retirement the client missed kept its running chip forever.
  function pruneCardSpans(data, minFreshId) {
    if (!session.value) return
    const spans = session.value.spans || []
    const hasList = Array.isArray(data.retired_span_ids)
    const next = dropStalePendingSpans(
      hasList ? dropRetiredSpans(spans, data.retired_span_ids) : spans,
      minFreshId)
    if (next.length !== spans.length) {
      session.value = { ...session.value, spans: next }
    }
    if (hasList) appliedRetiredDigest = data.retired_digest ?? null
  }

  // Live tail reconcile. Instead of blindly appending new roots (which drifts
  // from the DB whenever a row is mutated — chiefly the promptlive- placeholder
  // retired and replaced by its real prompt-<uuid> anchor), re-fetch the latest
  // page and reconcile the recent tail against it. The fresh page is the DB's
  // source of truth for the recent window, so a retired placeholder (absent) is
  // dropped and its real anchor (present) takes its place: no missing, no
  // duplicate, no drift. Older immutable history (loaded via loadOlder) is kept
  // untouched, and a root that persists reuses its already-loaded subtree.
  // Returns whether the tail actually moved, so the caller can skip the three
  // follow-up requests (and the poll can retire itself) on a quiet tick.
  async function reloadLiveTail() {
    if (newestLoadedId.value == null) {
      // No cursor yet (initial load failed?) — fall back to a full load.
      await loadSession()
      return true
    }
    const data = await api.get(
      `/sessions/${route.params.id}/map?shallow=1&limit=${PAGE_SIZE}`,
    )
    noteServerEpoch(data.span_epoch)
    const fingerprint = tailFingerprint(data)
    const changed = fingerprint !== lastTailFingerprint
    lastTailFingerprint = fingerprint
    const freshTail = hydrateNodes(data.tree, spanIndex(data))
    const freshIds = freshTail.map(n => n.data?.id).filter(v => v != null)
    const minFreshId = freshIds.length ? Math.min(...freshIds) : 0
    mergeLoadedSpans(data.spans || [])
    pruneCardSpans(data, minFreshId)
    applySessionSummary(data)
    if (!freshTail.length) {
      if (changed) await refreshRecentRootSubtrees(treeNodes.value)
      return changed
    }

    // The fresh page owns every root with id >= minFreshId; keep the older
    // nodes (immutable, below the window) exactly as they are.
    //
    // "Immutable" holds only for SETTLED roots. A retired placeholder is
    // written BEFORE the row that supersedes it, so it carries a LOWER id
    // than its replacement and slips into `older` — where nothing ever prunes
    // it, and it renders as an extra turn flag / Turns count for one prompt.
    // An SDK-launched run hits this on every prompt: both writers announce it
    // (lib/trace/alias.py), so one submission produces up to three prompt rows
    // in id order (sdk placeholder → hook placeholder → transcript anchor),
    // each retiring the last. A still-PENDING placeholder below the window is
    // stale by construction — a live one is always the NEWEST row — so that
    // test covers the polls that missed the server's retired list.
    //
    // Both halves of the placeholder test are load-bearing. The reserved
    // prefixes are NOT proof of a placeholder on their own: a resolved
    // `pending-<tool_use_id>` tool row keeps its id, and merge DEMOTES a
    // superseded pending to an ERROR/`is_interrupt` row it still expects the
    // timeline to render. Dropping on the prefix alone deleted both.
    const retired = new Set(data.retired_span_ids || [])
    const isStalePlaceholder = n => n.data?.status_code === 'PENDING'
      && isPendingPlaceholderId(n.data?.span_id)
    const older = treeNodes.value.filter(n => (n.data?.id ?? Infinity) < minFreshId
      && !retired.has(n.data?.span_id)
      && !isStalePlaceholder(n))

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
    if (changed) await refreshRecentRootSubtrees(treeNodes.value)
    return changed
  }

  // Reverse-pagination: pull the next page of older roots and prepend them to
  // the tree. Preserves the user's scroll position by capturing scrollTop +
  // scrollHeight before the DOM mutation and restoring scrollTop after.
  async function loadOlder() {
    if (loadingOlder.value || !hasMoreOlder.value) return
    if (oldestLoadedId.value == null) return
    loadingOlder.value = true
    // Capture pre-mutation scroll state from the actual content viewport.
    const scroller = getTraceScroller()
    const prevScrollHeight = scroller.scrollHeight
    const prevScrollTop = scroller.scrollTop
    try {
      const data = await api.get(
        `/sessions/${route.params.id}/map?shallow=1&limit=${PAGE_SIZE}&before_id=${oldestLoadedId.value}`,
      )
      const incoming = hydrateNodes(data.tree, spanIndex(data))
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
    let changed = false
    for (const s of spans) {
      const prev = byId.get(s.span_id)
      // /map (non-shallow) omits the `attributes` field for descendants, so
      // they get cached as `attributes: {}`. A later /children?recursive=1
      // fetch carries the real attributes; we must take them over the empty
      // placeholder. `prev.attributes || s.attributes` is wrong because `{}` is
      // truthy — it keeps the placeholder and discards the real attrs.
      const prevAttrs = prev?.attributes
      const newAttrs = s.attributes
      const attributes =
        newAttrs && Object.keys(newAttrs).length > 0
          ? newAttrs
          : (prevAttrs || newAttrs || {})
      // The live poll re-merges the same root spans every few seconds. Writing
      // them back reassigns `session.value`, which invalidates every computed
      // hanging off it — so a tick that learned nothing must write nothing.
      if (prev && spanUnchanged(prev, s, attributes)) continue
      changed = true
      byId.set(s.span_id, { ...prev, ...s, attributes })
    }
    if (!changed) return
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
      treeNodes.value = withNodeChildren(
        treeNodes.value, spanId, hydrateNodes(data.children, spanIndex(data)))
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
      treeNodes.value = withNodeChildren(
        treeNodes.value, rootSpanId, hydrateNodes(data.children, spanIndex(data)))
      subtreeLoaded.value.add(rootSpanId)
    } finally {
      loadingChildren.value.delete(rootSpanId)
    }
  }

  // Several roots, ONE projection. Focus mode force-opens every rendered turn
  // at once, so asked one root at a time the entry cost is a `deep=1` fetch per
  // turn — 50 on a full page, each re-running the whole-session merge server
  // side. The same filtering as the single-root path, applied to the set.
  async function ensureSpanSubtreesLoaded(rootSpanIds) {
    const ids = [...new Set(rootSpanIds)].filter(id => id
      && !isSyntheticSpanId(id)
      && !subtreeLoaded.value.has(id)
      && !loadingChildren.value.has(id))
    if (!ids.length) return
    // Nothing to batch, and the per-root endpoint answers it without paying
    // for a projection over the whole session.
    if (ids.length === 1) return ensureSpanSubtreeLoaded(ids[0])
    // The ids ride in the query string, and `loadOlder` keeps appending pages
    // — so the forced-open set is not bounded by PAGE_SIZE. Unchunked, a
    // reader who pages back far enough builds a request line past the server's
    // limit and gets a 414 for the whole page at once.
    for (let i = 0; i < ids.length; i += SUBTREE_IDS_PER_REQUEST) {
      await fetchSubtrees(ids.slice(i, i + SUBTREE_IDS_PER_REQUEST))
    }
  }

  // One bulk request for a chunk of never-loaded roots. No watermarks: every
  // id here is one we have never held, so there is nothing we could truthfully
  // claim to already have.
  async function fetchSubtrees(ids) {
    for (const id of ids) loadingChildren.value.add(id)
    try {
      const data = await api.get(
        `/sessions/${route.params.id}/subtrees`
        + `?ids=${ids.map(encodeURIComponent).join(',')}`,
      )
      mergeLoadedSpans(data.spans || [])
      const byId = spanIndexOver(data, session.value?.spans)
      for (const id of ids) {
        // Absent from `subtrees` means the projection no longer knows this
        // root (retired, reparented) — clear its children rather than keep
        // whatever the shallow map left there.
        const children = data.subtrees?.[id] || []
        treeNodes.value = withNodeChildren(
          treeNodes.value, id, hydrateNodes(children, byId))
        subtreeLoaded.value.add(id)
        const digest = data.digests?.[id]
        if (digest) appliedSubtreeDigests.set(id, digest)
        else appliedSubtreeDigests.delete(id)
      }
    } catch (_) {
      // Left unloaded, so a later span change re-asks. On a session whose poll
      // has retired nothing will re-ask — the turns stay spineless until the
      // reader toggles the mode — which is the same as the per-root path did,
      // and better than marking them loaded and empty forever.
    } finally {
      for (const id of ids) loadingChildren.value.delete(id)
    }
  }

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
    ensureSpanSubtreesLoaded,
    noteSpanFrame,
    ensureTerminalSpansLoaded, ensureWorkflowSpansLoaded,
  }
}
