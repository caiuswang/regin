import { ref, computed } from 'vue'
import api from '../api'
import { dropRetiredSpans } from '../utils/traceFormatters.js'
import {
  isActiveSession, parseLocalIso, STALE_FALLBACK_WINDOW_MS,
} from '../utils/sessionActivity.js'

// Data + poll lifecycle for the /live mobile session-tail card.
//
// Window / fold / tail ride the shallow-map pagination
// (`/api/sessions/<id>/map?shallow=1`), which pages by TURN ANCHORS and
// serializes only the window's ROOT spans — activity spans (tool calls,
// responses, PENDING placeholders) are children and arrive via per-root
// `/spans/<id>/children?deep=1` fetches, exactly like the desktop trace
// view's useTraceData (loadSession + refreshRecentRootSubtrees).
//
// The live poll re-fetches the full newest window (limit cursor, NOT
// after_id): `retired_span_ids` is computed from the requested window's raw
// rows, and an after_id poll returns an EMPTY window (and empty retired
// list) whenever no new prompt landed — so a `pending-*` placeholder that
// resolved mid-turn would never be pruned and the card would keep a
// duplicate row (the append-only two-render-paths gotcha). Mirroring
// SessionTraceView's reloadLiveTail keeps the prune authoritative on every
// poll.
// v7.2: `limit` pages TURN ANCHORS, not spans — the window unit is turns.
// 5 anchors keep the initial hydration to the newest handful of turns;
// limit=50 on a heavy session hydrates ~every span and the fold row never
// renders (verified against the worst-case fixture).
const PAGE_SIZE = 5
// State (idle/working/…) is a SERVER verdict now (phase / agent_phase) —
// the client only chooses how often to ask for it. The server's idle-settle
// window absorbs the mid-turn quiet gaps that this cadence would otherwise flap.
const POLL_ACTIVE_MS = 4000
const POLL_STALE_MS = 15000
// Deep-refresh the trailing N roots every poll — children stream in under
// the active prompt (and late subagents under the prior ones).
const DEEP_REFRESH_TAIL = 3
const CHILD_CONCURRENCY = 4
const MAX_ROOT_RETRIES = 5

// Reserved live-placeholder span_id prefixes (lib/trace/pending_spans.py).
const PENDING_PREFIXES = ['promptlive-', 'pending-', 'permreq-']

function bySpanTime(a, b) {
  const at = a.start_time ? (parseLocalIso(a.start_time)?.getTime() ?? 0) : 0
  const bt = b.start_time ? (parseLocalIso(b.start_time)?.getTime() ?? 0) : 0
  if (at !== bt) return at - bt
  return (a.id || 0) - (b.id || 0)
}

function isPendingPlaceholder(s) {
  if (s.status_code === 'PENDING') return true
  const sid = s.span_id || ''
  return PENDING_PREFIXES.some(p => sid.startsWith(p))
}

export function useLiveTail(getRouteId) {
  const sessionId = ref(null)
  const meta = ref({})        // session row + map summary (title, status, …)
  const spans = ref([])       // flat, chronological, all loaded windows
  const spanCountTotal = ref(0)
  const hasMoreOlder = ref(false)
  const loading = ref(true)
  const loadingOlder = ref(false)
  const error = ref(null)
  // Spans appended by the most recent poll cycle — the view uses this to
  // follow-tail / count the "N new" chip without misreading filter changes.
  const appendedSpans = ref([])

  let oldestLoadedId = null
  let loadedOlderOnce = false
  let roots = []              // [{ id, span_id, leaf }] ascending by DB id
  let rootIds = new Set()
  // Roots whose children fetch failed — retried on subsequent polls so a
  // transient 500 doesn't permanently lose a turn's rows. A root that keeps
  // failing is abandoned after MAX_ROOT_RETRIES: while any root is failing
  // the stale-PENDING sweep is suspended, so one persistently-500ing root
  // must not be allowed to pin a dead placeholder in the NOW zone forever.
  let failedRootIds = new Set()
  let rootFailCounts = new Map()
  let timer = null
  let stopped = true
  let pollBusy = false
  // Generation guard: start() bumps it; any async work captured under an
  // older generation discards its results, so an in-flight poll from the
  // previous session can never merge stale spans/meta after a route switch.
  let epoch = 0

  function isStale(gen) {
    return gen !== epoch || stopped
  }

  // A resumed session (exit tmux → `claude --resume` in a fresh pane) keeps
  // its stale `ended_at` + `prompt_input_exit` reason, but the server flips
  // `status` back to 'active' and advances `last_seen` past the recorded end.
  // Trust `status` first; only honour `ended_at` when no activity followed it
  // — otherwise the card wrongly reads "✓ finished" mid-run.
  const ended = computed(() => {
    const m = meta.value
    if (m.status === 'active') return false
    if (m.status === 'ended') return true
    if (!m.ended_at) return false
    const e = parseLocalIso(m.ended_at)?.getTime()
    const s = parseLocalIso(m.last_seen)?.getTime()
    // last_seen more than 1s past ended_at ⇒ resumed, not ended.
    return !(Number.isFinite(e) && Number.isFinite(s) && s > e + 1000)
  })

  // Age of the newest ingested span, measured server−server (server_now vs
  // last_seen, both the server's wall-clock) so a viewer in another timezone
  // reads the same age. NaN until the first map summary lands.
  const lastSeenAgeMs = computed(() => {
    const m = meta.value
    const now = m.server_now ? parseLocalIso(m.server_now)?.getTime() : NaN
    const seen = m.last_seen ? parseLocalIso(m.last_seen)?.getTime() : NaN
    return Number.isFinite(now) && Number.isFinite(seen) ? now - seen : NaN
  })

  // `status` stays 'active' forever when a session dies without its
  // SessionEnd hook (crash/SIGKILL) — no server heartbeat corrects it. A
  // session that claims to be active but has ingested nothing for the
  // stale window gets its own header state instead of pulsing "running".
  const stale = computed(() => !ended.value
    && Number.isFinite(lastSeenAgeMs.value)
    && lastSeenAgeMs.value > STALE_FALLBACK_WINDOW_MS)

  // Same rule as the Sessions table's green badge (utils/sessionActivity),
  // except a stale-but-'active' session is demoted: it slows the poll to
  // the 15s cadence and keeps the NOW zone out of the idle-composer state.
  const active = computed(() => !stale.value && isActiveSession(meta.value))

  // Consecutive mid-session poll failures. One blip is normal; two misses
  // (≥8s dark) surface a "connection lost" hint — without this the card
  // keeps its last healthy render indefinitely during an outage.
  const pollFailCount = ref(0)
  const connectionLost = computed(() => pollFailCount.value >= 2)

  // Fold-row remainder. `span_count_total` excludes PENDING placeholders
  // (append-only store keeps them), so the loaded count must too.
  const earlierCount = computed(() => {
    const loaded = spans.value.filter(s => s.status_code !== 'PENDING').length
    return Math.max(0, spanCountTotal.value - loaded)
  })

  function mapUrl() {
    return `/sessions/${sessionId.value}/map?shallow=1&limit=${PAGE_SIZE}`
  }

  // Keyed merge (never removes — pruning is the retired-ids path). Prefer
  // non-empty incoming attributes over a cached `{}` shallow placeholder,
  // same rule as useTraceData.mergeLoadedSpans.
  function mergeSpans(list) {
    if (!list || !list.length) return
    const byId = new Map(spans.value.map(s => [s.span_id, s]))
    for (const s of list) {
      const prev = byId.get(s.span_id) || {}
      const attributes = s.attributes && Object.keys(s.attributes).length > 0
        ? s.attributes
        : (prev.attributes || s.attributes || {})
      byId.set(s.span_id, { ...prev, ...s, attributes })
    }
    spans.value = Array.from(byId.values()).sort(bySpanTime)
  }

  function dropSpanIds(ids) {
    if (!ids.size) return
    spans.value = spans.value.filter(s => !ids.has(s.span_id))
    roots = roots.filter(r => !ids.has(r.span_id))
    rootIds = new Set(roots.map(r => r.span_id))
  }

  function pruneRetired(retiredIds) {
    if (!retiredIds || !retiredIds.length) return
    spans.value = dropRetiredSpans(spans.value, retiredIds)
    const retired = new Set(retiredIds)
    roots = roots.filter(r => !retired.has(r.span_id))
    rootIds = new Set(roots.map(r => r.span_id))
  }

  // Invariant: a PENDING placeholder must not outlive the poll window. The
  // newest window is fully re-served every tick, so a placeholder absent
  // from this tick's responses (window roots + refreshed subtrees) is stale
  // — e.g. a permreq- whose anchor aged out of the 5-anchor window is never
  // in retired_span_ids and would otherwise pin the NOW zone forever.
  function pruneStalePending(seenIds) {
    const staleIds = new Set(
      spans.value
        .filter(s => isPendingPlaceholder(s) && !seenIds.has(s.span_id))
        .map(s => s.span_id),
    )
    dropSpanIds(staleIds)
  }

  // Track window roots (turn anchors) for the deep-children fetches.
  // Returns the span_ids that are new to this client.
  function recordRoots(tree) {
    const fresh = []
    for (const node of tree || []) {
      const d = node.data || {}
      if (!d.span_id || rootIds.has(d.span_id)) continue
      rootIds.add(d.span_id)
      roots.push({ id: d.id ?? 0, span_id: d.span_id, leaf: !!node.leaf })
      fresh.push(d.span_id)
    }
    roots.sort((a, b) => a.id - b.id)
    return fresh
  }

  function applySummary(data) {
    const patch = {}
    // bridge_reachable / bridge_pane ride the same poll (no extra client
    // polling loop) — they gate the NOW zone's bridge composer. server_now
    // is the server's wall-clock at read time — the NOW-zone elapsed anchors
    // to it so a viewer in a different timezone doesn't leak the offset.
    // status/ended_reason must refresh every poll too: pinning them to the
    // page-load row froze the header — a session ending (or resuming) while
    // viewed never flipped.
    // task_list (final task snapshot for the header chip + tasks sheet),
    // agent_roster (whole-session subagent roster — window-independent),
    // model / repo (header meta line), and the segment-aware live-peak
    // context_pct (ctx meter) all refresh every poll off the same summary.
    // phase / agent_phase are the server's state verdict — the card renders
    // them, never re-derives state (no client idle debounce).
    // queued_prompts (transcript-derived + bridge steers) drive the queued chips.
    const keys = ['title', 'started_at', 'ended_at', 'last_seen',
      'status', 'ended_reason', 'bridge_reachable', 'bridge_pane', 'server_now',
      'task_list', 'agent_roster', 'model', 'repo', 'context_pct',
      'phase', 'agent_phase', 'queued_prompts']
    for (const k of keys) {
      if (k in data) patch[k] = data[k]
    }
    // Phone-clock stamp of when this server_now landed — only DELTAS of the
    // phone clock are taken from it (never an absolute compare to a server
    // timestamp), so the elapsed ticks between polls without reintroducing
    // the timezone skew.
    if ('server_now' in data) patch.server_now_at = Date.now()
    meta.value = { ...meta.value, ...patch }
    if (data.span_count_total != null) spanCountTotal.value = data.span_count_total
  }

  // Cursor bookkeeping + session summary for ANY shallow-map page response —
  // one path for the initial window, the poll refetch, and before_id unfold.
  function applyPageBounds(data, { older = false } = {}) {
    if (older || !loadedOlderOnce) {
      if (data.oldest_loaded_id != null) oldestLoadedId = data.oldest_loaded_id
      hasMoreOlder.value = !!data.has_more_older
    }
    if (older) loadedOlderOnce = true
    applySummary(data)
  }

  async function fetchChildrenDeep(rootSpanId) {
    const data = await api.get(
      `/sessions/${sessionId.value}/spans/${rootSpanId}/children?deep=1`,
    )
    return data.spans || []
  }

  // Fetch children for a set of roots with bounded concurrency and collect
  // them — the CALLER merges once (single reactive update). A failed root
  // lands in failedRootIds and is retried by later polls.
  async function loadChildrenFor(spanIds, { gen = epoch } = {}) {
    const collected = []
    for (let i = 0; i < spanIds.length; i += CHILD_CONCURRENCY) {
      if (isStale(gen)) break
      const batch = spanIds.slice(i, i + CHILD_CONCURRENCY)
      const results = await Promise.all(
        batch.map(id => fetchChildrenDeep(id)
          .then(list => ({ id, list }))
          .catch(() => ({ id, list: null }))),
      )
      if (isStale(gen)) break
      for (const { id, list } of results) {
        if (list === null) {
          const n = (rootFailCounts.get(id) || 0) + 1
          rootFailCounts.set(id, n)
          if (n <= MAX_ROOT_RETRIES) failedRootIds.add(id)
          else failedRootIds.delete(id)
        } else {
          failedRootIds.delete(id)
          rootFailCounts.delete(id)
          collected.push(...list)
        }
      }
    }
    return collected
  }

  // Resolve the session row: explicit route id (full id or prefix), else the
  // newest session. The map summary refreshes the header fields (including
  // status/ended_reason) every poll; this row fetch only bootstraps them.
  async function resolveSessionRow() {
    const routeId = getRouteId()
    const qs = routeId
      ? `trace_id=${encodeURIComponent(routeId)}&kind=all&size=1`
      : 'kind=all&size=1&limit=1'
    const data = await api.get(`/sessions?${qs}`)
    const row = (data.sessions || [])[0] || null
    sessionId.value = row?.trace_id || routeId || null
    if (row) meta.value = { ...meta.value, ...row }
  }

  function applyWindow(data) {
    mergeSpans(data.spans || [])
    const fresh = recordRoots(data.tree)
    applyPageBounds(data)
    return fresh
  }

  function clearTimer() {
    if (timer) { clearTimeout(timer); timer = null }
  }

  function scheduleNext() {
    clearTimer()
    if (stopped) return
    // An ended session normally schedules nothing — but if any root's
    // children fetch failed during the initial load there would be no later
    // poll to retry it, leaving a permanent hole in the tail. Keep polling
    // until the retries succeed or hit the give-up cap.
    if (ended.value && failedRootIds.size === 0) return
    if (document.hidden) return // visibilitychange resumes
    timer = setTimeout(pollOnce, active.value ? POLL_ACTIVE_MS : POLL_STALE_MS)
  }

  // One tick's subtree refresh: trailing roots + retries of earlier
  // failures, merged in one shot; when every fetch succeeded, sweep stale
  // PENDING placeholders (a failed subtree fetch would make its still-live
  // placeholders look absent, so the sweep skips that tick).
  async function refreshSubtrees(data, freshRootIds, gen) {
    const tailCount = Math.max(DEEP_REFRESH_TAIL, freshRootIds.length)
    const refreshIds = new Set(roots.slice(-tailCount).map(r => r.span_id))
    for (const id of failedRootIds) {
      if (rootIds.has(id)) refreshIds.add(id)
    }
    const children = await loadChildrenFor([...refreshIds], { gen })
    if (isStale(gen)) return
    mergeSpans(children)
    if (failedRootIds.size === 0) {
      const seenIds = new Set((data.spans || []).map(s => s.span_id))
      for (const s of children) seenIds.add(s.span_id)
      pruneStalePending(seenIds)
    }
  }

  async function pollOnce() {
    timer = null // we own the tick now; onVisibility uses null as "resumable"
    if (pollBusy || stopped) return
    if (document.hidden) return // went hidden after scheduling — skip the tick
    const gen = epoch
    pollBusy = true
    // "Appended" = sorts strictly after the previously-last row. An id
    // watermark is not enough: a rescan-backfilled row can carry a high DB
    // id yet sort mid-list by start_time, and must not feed the "N new"
    // chip (which scrolls to the bottom).
    const prevLast = spans.value[spans.value.length - 1] || null
    try {
      const data = await api.get(mapUrl())
      if (isStale(gen)) return
      const fresh = applyWindow(data) // span_ids of roots new to this client
      // EVERY poll prunes what the serve-time merge dropped from the window
      // (promptlive- promoted to its anchor, pending-/permreq- resolved).
      pruneRetired(data.retired_span_ids)
      await refreshSubtrees(data, fresh, gen)
      if (isStale(gen)) return
      pollFailCount.value = 0
    } catch {
      // Keep the cadence, but count the miss — consecutive failures flip
      // `connectionLost` so the header can say the data is no longer live.
      if (!isStale(gen)) pollFailCount.value += 1
    }
    finally { pollBusy = false }
    if (isStale(gen)) return
    const appended = prevLast
      ? spans.value.filter(s => bySpanTime(s, prevLast) > 0)
      : spans.value.slice()
    if (appended.length) appendedSpans.value = appended
    scheduleNext()
  }

  function onVisibility() {
    if (document.hidden) { clearTimer(); return }
    // Same gate as scheduleNext: an ended session with failed root fetches
    // still owes retries — plain !ended here would orphan them on a
    // hide/show cycle.
    const done = ended.value && failedRootIds.size === 0
    if (!stopped && !done && !timer && !pollBusy) pollOnce()
  }

  function resetState() {
    sessionId.value = null
    meta.value = {}
    spans.value = []
    spanCountTotal.value = 0
    hasMoreOlder.value = false
    error.value = null
    appendedSpans.value = []
    oldestLoadedId = null
    loadedOlderOnce = false
    roots = []
    rootIds = new Set()
    failedRootIds = new Set()
    rootFailCounts = new Map()
    pollFailCount.value = 0
  }

  async function start() {
    stop()
    stopped = false
    const gen = ++epoch
    resetState()
    loading.value = true
    document.addEventListener('visibilitychange', onVisibility)
    try {
      await resolveSessionRow()
      if (isStale(gen)) return
      if (!sessionId.value) {
        // Nothing to poll: a null session must fully stop, or the scheduler
        // would hit /sessions/null/map forever.
        error.value = 'No sessions recorded yet.'
        stopped = true
        return
      }
      const data = await api.get(mapUrl())
      if (isStale(gen)) return
      applyWindow(data)
      // Newest-first so the tail (bottom) is covered first if interrupted.
      const fetchIds = [...roots].reverse().filter(r => !r.leaf).map(r => r.span_id)
      const children = await loadChildrenFor(fetchIds, { gen })
      if (isStale(gen)) return
      mergeSpans(children)
    } catch (e) {
      if (isStale(gen)) return
      error.value = e?.message || 'Failed to load session.'
    } finally {
      if (gen === epoch) loading.value = false
    }
    if (isStale(gen)) return
    scheduleNext()
  }

  function stop() {
    stopped = true
    clearTimer()
    document.removeEventListener('visibilitychange', onVisibility)
  }

  // Unfold one older page (before_id cursor), staged into a single merge so
  // the caller can anchor the scroll position around one DOM mutation.
  async function loadOlder() {
    if (loadingOlder.value || !hasMoreOlder.value || oldestLoadedId == null) return
    const gen = epoch
    loadingOlder.value = true
    try {
      const data = await api.get(`${mapUrl()}&before_id=${oldestLoadedId}`)
      if (isStale(gen)) return
      const staged = [...(data.spans || [])]
      const fresh = recordRoots(data.tree)
      staged.push(...await loadChildrenFor(fresh, { gen }))
      if (isStale(gen)) return
      mergeSpans(staged)
      applyPageBounds(data, { older: true })
    } catch { /* keep the fold row; the user can re-tap */ }
    finally {
      loadingOlder.value = false
    }
  }

  return {
    sessionId, meta, spans, spanCountTotal, hasMoreOlder, earlierCount,
    loading, loadingOlder, error, ended, active, stale, lastSeenAgeMs,
    connectionLost, appendedSpans,
    start, stop, loadOlder, mergeSpans,
  }
}
