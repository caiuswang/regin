import { computed, ref, shallowRef, watch } from 'vue'
import api from '../api'
import { notificationTier } from '../constants/inboxTypes'
import { useNotificationPrefs } from './useNotificationPrefs'
import { useOsNotifications } from './useOsNotifications'
import { useRealtime } from './useRealtime'

// The notification state machine: one live tier-1 blocker, a bounded stack of
// tier-2 toasts, and the set of messages that popped and were folded away.
// Module-singleton because the surfaces it drives (banner, toast host, sidebar
// badge, sessions list) mount in different places but must agree on one state
// — two copies would let a toast outlive the banner that answered it.
//
// The scenarios this has to survive, and where each is handled:
//
//   multi-tab                 → one leader holds the stream, followers read its
//                               BroadcastChannel relay (useRealtime)
//   acted on in another tab   → `resolved` frames retire the surfaces here
//   answered in the terminal  → same, via the loopback trigger
//   redelivery of one message → `seen` set; a message pops at most once
//   superseded prompt         → replaces the banner, keeps the waiting clock
//   stream down, then back    → `hydrate()` re-reads the unread feed on connect
//   blocker for a dead session→ liveness-gated, like the inbox decision surfaces
//   optimistic action fails   → the surface is restored, not silently eaten
//   logout / user switch      → `reset()` drops every timer and every row

// Folding a blocker cannot resolve it, so the fold collapses the detail into
// the strip and stays folded until the user reopens it — it never auto-pops.
// Dismissing is the separate, server-backed "never show this decision again".
const RESUMED_LINGER_MS = 6_000
const RETRY_AFTER_MS = 2_000
// The stream is the fast path, but a frame can be missed (laptop asleep, SSE
// down, a resolution that landed while no server was up). While a decision is
// showing, reconcile against server truth on a slow cadence so a banner can
// never outlive its answer by more than a minute.
const RECONCILE_MS = 60_000

// The `resolved` frame's vocabulary, mirrored from `_retire` in
// web/blueprints/trace/agent_messages.py (and pinned there by
// `test_every_retire_reason_is_one_the_client_knows`). An unrecognised reason
// retires nothing at all: the frame came from a producer this build does not
// understand, and guessing would be how a surface goes missing.
const KNOWN_REASONS = new Set(['read', 'hidden', 'dismissed'])
// `dismissed` = the prompt was answered (only `store.dismiss_keyed` knows
// that); `hidden` = a human closed the card, which is the sole escape from a
// banner whose session died mid-prompt. Everything else leaves the banner up.
const BLOCKER_RETIRING_REASONS = new Set(['dismissed', 'hidden'])

const toasts = ref([])
// Ids, not a counter: "mark all read" and a single retire both have to subtract
// exactly the right rows, which a bare number cannot do.
const foldedIds = ref(new Set())
// EVERY decision waiting, not the newest one. Several agents park at once and
// a single slot silently dropped all but the last — the banner pages through
// this list instead. Oldest first: the longest-parked agent is next to answer.
const blockers = shallowRef([])
const blockerIndex = ref(0)
const blockerFolded = ref(false)
const resumedTraceId = ref(null)
const suppressed = ref(false)
const now = ref(Date.now())

// trace_id → when that agent first parked. Held outside the rows because the
// rows are replaced wholesale on every refresh, and the clock must measure how
// long the AGENT has waited, not how long this copy of the row has existed.
const parkedSince = new Map()

// `refreshBlockers`'s response REPLACES the list, so a response that left the
// server before a `resolved` frame arrived would re-install the card that
// frame just retired — the banner re-asking an answered question. A monotonic
// generation makes the newest request the only one allowed to land.
//
// Nothing else is needed to protect a row raised mid-flight: `raiseBlocker`
// bumps this counter synchronously, so every refresh in flight when a frame
// lands is already superseded, and the refresh the raise itself triggers is
// the one that answers. If THAT response omits the row, the server has been
// asked and said the card is not live — which is authoritative, and dropping
// it is correct.
let refreshGen = 0

// Every message id this tab has already surfaced. A reconnect replays nothing,
// but the leader/follower relay and a same-tab hydrate can both offer the same
// row, and a message must never pop twice.
const seen = new Set()

const foldTimers = new Map()

let reconcileTimer = null
let resumedTimer = null
let clockTimer = null
let started = false
let openHandler = null

// ── Derived ────────────────────────────────────────────────────────

export const folded = computed(() => foldedIds.value.size)
export const blockerCount = computed(() => blockers.value.length)
// The index is clamped rather than stored trusted: a refresh that drops the
// row being viewed must land on a neighbour, never on `undefined`.
export const blockerPos = computed(
  () => Math.min(blockerIndex.value, Math.max(0, blockers.value.length - 1)))
export const blocker = computed(() => blockers.value[blockerPos.value] || null)

// A Set, not the one visible row's id: the list highlight has to flag EVERY
// parked session at once, and it is read on a page the banner may not be
// paging to.
export const awaitingTraceIds = computed(
  () => new Set(blockers.value.map(b => b.trace_id).filter(Boolean)))

export const bannerVisible = computed(
  () => !!blocker.value && !blockerFolded.value && !suppressed.value)
export const stripVisible = computed(
  () => !!blocker.value && blockerFolded.value && !suppressed.value)

function waitedFor(row) {
  const since = row && parkedSince.get(row.trace_id)
  if (!since) return ''
  const secs = Math.max(1, Math.round((now.value - since) / 1000))
  return secs < 60 ? `${secs}s` : `${Math.floor(secs / 60)}m ${secs % 60}s`
}

export const blockerWaitedFor = computed(() => waitedFor(blocker.value))

// How long a *named* session has been parked — the sessions list flags rows
// the banner is not currently paging to.
export function waitedForTrace(traceId) {
  return waitedFor({ trace_id: traceId })
}

export function showBlockerAt(index) {
  const total = blockers.value.length
  if (!total) return
  blockerIndex.value = ((index % total) + total) % total
}

export function nextBlocker() { showBlockerAt(blockerPos.value + 1) }
export function prevBlocker() { showBlockerAt(blockerPos.value - 1) }

// ── Ingest ─────────────────────────────────────────────────────────

function ingest(message, { replay = false } = {}) {
  if (!message?.id || message.is_test) return
  // A superseded card keeps its id, so version is part of the identity — a new
  // prompt under the same `msg_key` must be allowed to interrupt again.
  const fingerprint = `${message.id}:${message.version || 1}`
  if (seen.has(fingerprint)) return
  seen.add(fingerprint)

  const tier = notificationTier(message.msg_type)
  if (tier === 1) {
    raiseBlocker(message, replay)
    return
  }
  // Replayed history is context, not news: it must never pop or reach the OS,
  // or reconnecting after lunch would fire a dozen banners at once.
  if (replay || tier === 3 || suppressed.value) return
  osNotify(message, tier)
  pushToast(message)
}

// A tier-1 frame off the stream. It carries the inbox row but not the parked
// span, so the options and the answer channel are unknown until `refreshBlockers`
// lands — the row goes up immediately (the agent is stopped NOW) and is
// enriched a moment later rather than waiting for the round trip.
function raiseBlocker(message, replay) {
  stampParked(message)
  const next = blockers.value.filter(b => !sameCard(b, message))
  next.push({ options: [], answerable: null, ...message })
  blockers.value = next
  // A fresh decision reopens a folded banner: the fold was about the
  // decisions the user had already seen, not this one.
  blockerFolded.value = false
  resumedTraceId.value = null
  if (!replay) osNotify(message, 1)
  refreshBlockers()
}

// One card per (session, key) — a re-prompt supersedes rather than stacks,
// which is the same identity `store.dismiss_keyed` retires by.
function sameCard(a, b) {
  return a.trace_id === b.trace_id && a.msg_key === b.msg_key
}

// The clock measures how long the AGENT has been parked, so a re-prompt in a
// session that was already waiting keeps counting from the first one.
function stampParked(message) {
  if (!message.trace_id || parkedSince.has(message.trace_id)) return
  parkedSince.set(message.trace_id, messageStamp(message))
}

// Server-stamped so a re-hydrated blocker shows how long it has *really* been
// waiting, not how long this tab has been open.
function messageStamp(message) {
  const parsed = message.created_at ? new Date(message.created_at) : null
  return parsed && !Number.isNaN(parsed.getTime()) ? parsed.getTime() : Date.now()
}

function pushToast(message) {
  const { prefs } = useNotificationPrefs()
  if (!prefs.toastsEnabled) return
  // A superseded card keeps its id, so a version that folded earlier is still
  // in the fold set; leaving it there would count the very toast now on screen.
  unfold(id => id === message.id)
  const next = [{ ...message, at: Date.now() }, ...toasts.value.filter(t => t.id !== message.id)]
  toasts.value = next.slice(0, prefs.maxToasts)
  for (const overflow of next.slice(prefs.maxToasts)) addFolded(overflow.id)
  armFold(message.id)
}

// Kept per message so a toast that leaves and comes back (an optimistic
// mark-read the server rejected) is re-armed rather than left on screen for
// good by a timer that already fired against nothing.
function armFold(id) {
  const { prefs } = useNotificationPrefs()
  clearTimeout(foldTimers.get(id))
  foldTimers.set(id, setTimeout(() => fold(id), prefs.toastDurationSec * 1000))
}

function disarmFold(id) {
  clearTimeout(foldTimers.get(id))
  foldTimers.delete(id)
}

function osNotify(message, tier) {
  // A blocker interrupts even on a page that suppresses the in-app banner —
  // suppression means "you are already looking at the queue", which is not
  // true of the OS surface when the tab is behind another window.
  if (tier !== 1 && suppressed.value) return
  useOsNotifications().post(message, { onOpen: (m) => openHandler?.(m) })
}

// ── Retire ─────────────────────────────────────────────────────────

function addFolded(id) {
  // Folded is terminal for the card, so its fold timer has nothing left to do.
  disarmFold(id)
  const next = new Set(foldedIds.value)
  next.add(id)
  foldedIds.value = next
}

function unfold(predicate) {
  const next = new Set([...foldedIds.value].filter(id => !predicate(id)))
  if (next.size !== foldedIds.value.size) foldedIds.value = next
}

// A toast that times out folds into the badge — it is still unread, so the
// count must not drop. Dismissing (✕) and opening it are the same promise:
// hide the card, keep the row unread, and keep it counted in the fold pill —
// otherwise "N more folded" claims less than the badge is actually holding.
function fold(id) {
  disarmFold(id)
  if (!toasts.value.some(t => t.id === id)) return
  toasts.value = toasts.value.filter(t => t.id !== id)
  addFolded(id)
}

// Take the card away without counting it: only for a message that is no
// longer unread (marked read here, or retired by another tab).
function drop(id) {
  disarmFold(id)
  toasts.value = toasts.value.filter(t => t.id !== id)
}

function matches(message, payload) {
  if (payload.all) return true
  if (payload.message_ids?.includes(message.id)) return true
  return !!payload.msg_key && payload.trace_id === message.trace_id
    && payload.msg_key === message.msg_key
}

// Another tab — or the terminal, via the loopback trigger — handled it. The
// surfaces here have to clear without the user clicking anything.
function retire(payload) {
  if (!payload || !KNOWN_REASONS.has(payload.reason)) return
  toasts.value = toasts.value.filter(t => !matches(t, payload))
  if (payload.all) foldedIds.value = new Set()
  else if (payload.message_ids?.length) {
    const gone = new Set(payload.message_ids)
    unfold(id => gone.has(id))
  }
  // Fail CLOSED on the blocker. Only two reasons may retire it, and only one
  // of them may claim the agent resumed — see `_retire` in
  // web/blueprints/trace/agent_messages.py. Anything else (a `read`, an
  // unknown reason, a producer that sent none) leaves the banner up: a banner
  // wrongly shown is noise, a banner wrongly cleared silently loses a stopped
  // agent, and the row is then out of the unread feed so `hydrate()` can never
  // bring it back.
  const retiresBlocker = BLOCKER_RETIRING_REASONS.has(payload.reason)
  if (!retiresBlocker) return
  const gone = blockers.value.filter(b => matches(b, payload))
  for (const row of gone) {
    clearBlocker(row, { resumed: payload.reason === 'dismissed' })
  }
}

// `resumed` is a claim about the agent, so only a real resolve may make it.
//
// Scoped to ONE card, not to the session: a session can hold a live
// `permission-pending` AND a live `plan-pending` at once (separate emit paths
// in event_notify.py, and `list_decision_messages` does not dedup per trace).
// Retiring by trace_id dropped both, so answering the permission prompt
// silently took away the plan banner for an agent that was still stopped.
function clearBlocker(card, { resumed = true } = {}) {
  const traceId = card?.trace_id
  const remaining = blockers.value.filter(b => !sameCard(b, card))
  // Keep the reader on the card they were looking at: dropping row 1 of 4 must
  // not silently advance them past row 2 unread.
  const wasBefore = blockers.value
    .slice(0, blockerPos.value)
    .filter(b => sameCard(b, card)).length
  blockers.value = remaining
  blockerIndex.value = Math.max(0, blockerPos.value - wasBefore)
  // The clock is per-session and the sibling card still needs it.
  if (!remaining.some(b => b.trace_id === traceId)) parkedSince.delete(traceId)
  if (!remaining.length) blockerFolded.value = false
  // Nor may the row read "resumed" while the session's other prompt is parked.
  if (!traceId || !resumed) return
  if (remaining.some(b => b.trace_id === traceId)) return
  resumedTraceId.value = traceId
  clearTimeout(resumedTimer)
  resumedTimer = setTimeout(() => {
    if (resumedTraceId.value === traceId) resumedTraceId.value = null
  }, RESUMED_LINGER_MS)
}

// ── Actions ────────────────────────────────────────────────────────

// Fold hides the DETAIL, not the decision: the strip stays up, still counted,
// until the user reopens it or the queue empties. No timer — an auto-reopen
// took the choice back out of the user's hands 45 seconds after they made it.
function foldBlocker() {
  if (blocker.value) blockerFolded.value = true
}

function unfoldBlocker() {
  blockerFolded.value = false
}

// Dismiss is the "never show this decision again" promise — server-backed
// (`dismissed_at`), so it survives reloads and clears every other tab with
// reason `hidden`, which claims nothing about the agent. Optimistic but
// reversible: a card the server never dismissed must not vanish silently.
async function dismissBlocker(row) {
  if (!row?.id) return
  clearBlocker(row, { resumed: false })
  try {
    await api.post(`/agent-messages/${row.id}/dismiss`)
  } catch {
    stampParked(row)
    blockers.value = [...blockers.value, row]
  }
}

// Optimistic, but reversible: hiding a card the server never marked read would
// lose it silently — the badge would still count it with nothing left to click.
// The blocker is deliberately untouched: marking a parked agent's question read
// is not answering it, so its banner outlives the acknowledgement.
async function markRead(id) {
  const restore = toasts.value.find(t => t.id === id)
  drop(id)
  try {
    await api.post('/agent-messages/read', { ids: [id] })
  } catch {
    if (!restore) return
    toasts.value = [restore, ...toasts.value]
    armFold(id)
  }
}

// ── Answering, from wherever the operator happens to be ────────────

// The banner's option buttons and allow/deny run the SAME two endpoints the
// /live sheet does — there is no second delivery path to keep in step. What
// the banner adds is reach: the agent is stopped, and making the operator
// first navigate to /live to say "yes" is the whole complaint.
//
// The card is retired ONLY on `delivered: true`. A refused or failed send
// leaves the agent parked, so a banner that closed anyway would be a surface
// claiming an answer that never arrived.
async function deliver(row, path, payload) {
  if (!row?.trace_id) return { delivered: false, detail: 'no session' }
  let res = null
  try {
    res = await api.post(`/sessions/${row.trace_id}/${path}`, payload)
  } catch {
    res = null
  }
  if (res?.delivered) clearBlocker(row, { resumed: true })
  return res || { delivered: false, detail: 'send failed' }
}

// `option_index` is the span's own ordering, carried through from
// `blockers._options_of` — never a position in some re-sorted client list, or
// the bridge picks a different entry than the one that was clicked.
function answerBlocker(row, option) {
  return deliver(row, 'bridge-answer', {
    option_index: option.index, label: option.label,
  })
}

function decideBlocker(row, behavior, reason) {
  return deliver(row, 'bridge-decide', {
    behavior, reason: reason || undefined,
    tool_use_id: row?.span_id || undefined,
  })
}

function reset() {
  toasts.value = []
  foldedIds.value = new Set()
  seen.clear()
  parkedSince.clear()
  for (const timer of foldTimers.values()) clearTimeout(timer)
  foldTimers.clear()
  clearInterval(reconcileTimer)
  reconcileTimer = null
  clearTimeout(resumedTimer)
  clearInterval(clockTimer)
  clockTimer = null
  // The LIST, not `blocker` — that is a computed, so assigning to it is a
  // silent no-op in a production build and the previous user's question, its
  // session title and its live option buttons survived a 401.
  blockers.value = []
  blockerIndex.value = 0
  // Anything still in flight resolved against the user who just went away.
  refreshGen += 1
  blockerFolded.value = false
  resumedTraceId.value = null
  started = false
  // `suppressed` and `openHandler` are deliberately NOT cleared: they are
  // registrations from still-mounted components (the route watcher, the toast
  // host), not per-user data. Clearing them on a 401 that does not unmount the
  // app would silently un-suppress /inbox and make OS-notification clicks
  // no-ops, with nothing left to re-register them.
}

// ── Hydration ──────────────────────────────────────────────────────

// The parked set, read from the server. This is the ONLY leg that survives a
// reload: the stream delivers a frame once, so a tab that opens after the
// prompt fired — or simply navigates to a page that mounts the banner — has no
// other way to learn an agent is stopped. It is not a poll; it runs on mount,
// on route entry, on stream reconnect, and when a tier-1 frame needs enriching
// with the span data a frame cannot carry.
//
// The rows are server truth (options, answer channel, liveness), so they
// REPLACE local state rather than merging into it — a card the server no
// longer lists has been answered or its session has gone, and either way the
// banner must stop claiming it.
async function refreshBlockers({ retries = 1 } = {}) {
  const generation = ++refreshGen
  let rows = null
  try {
    const data = await api.get('/agent-messages/blockers')
    rows = data.blockers || []
  } catch {
    // The stream's own row stands — but a row raised off a frame is on screen
    // WITHOUT its options or its answer channel until an enrichment lands, and
    // while the stream is healthy nothing else re-reads. So retry once rather
    // than strand a live decision in the read-only branch until the operator
    // happens to navigate. Skipped when superseded: a newer refresh owns it.
    if (retries > 0 && generation === refreshGen) {
      setTimeout(() => refreshBlockers({ retries: retries - 1 }), RETRY_AFTER_MS)
    }
    return
  }
  // A newer refresh already answered this question; this response is history.
  // Without the guard, a reply that left the server before a `resolved` frame
  // arrived would re-install the card that frame had just retired — the
  // banner re-asking a question the operator already answered.
  if (generation !== refreshGen) return
  const anchor = blocker.value
  for (const row of rows) {
    stampParked(row)
    seen.add(`${row.id}:${row.version || 1}`)
  }
  blockers.value = rows
  for (const id of [...parkedSince.keys()]) {
    if (!rows.some(r => r.trace_id === id)) parkedSince.delete(id)
  }
  // Hold the reader on the card they were reading, wherever it moved to.
  const at = anchor ? rows.findIndex(r => sameCard(r, anchor)) : -1
  blockerIndex.value = at >= 0 ? at : 0
  if (!rows.length) blockerFolded.value = false
}

// Tier-2 history for the badge's fold accounting. Replayed, so nothing pops:
// reconnecting after lunch must not fire a dozen toasts at once.
async function hydrateToasts() {
  try {
    const data = await api.get('/agent-messages/inbox?unread=true&limit=50')
    for (const row of [...(data.messages || [])].reverse()) {
      if (notificationTier(row.msg_type) === 1) {
        // Blockers come from `/blockers`, which knows liveness and options;
        // marking them seen here stops a later stream replay re-raising a
        // stale one behind the server's back.
        seen.add(`${row.id}:${row.version || 1}`)
        continue
      }
      ingest(row, { replay: true })
    }
  } catch { /* a failed hydrate leaves the stream as the only source — fine */ }
}

async function hydrate() {
  await Promise.all([refreshBlockers(), hydrateToasts()])
}

// ── Wiring ─────────────────────────────────────────────────────────

// Lowering the cap has to bite immediately; the excess folds rather than
// vanishing, so the badge still accounts for it.
const { prefs: livePrefs } = useNotificationPrefs()
watch(() => livePrefs.maxToasts, (max) => {
  if (toasts.value.length <= max) return
  for (const overflow of toasts.value.slice(max)) addFolded(overflow.id)
  toasts.value = toasts.value.slice(0, max)
})

// Runs only while a decision is actually showing — an idle tab polls nothing.
watch(blockerCount, (count) => {
  if (count > 0 && !reconcileTimer) {
    reconcileTimer = setInterval(() => refreshBlockers(), RECONCILE_MS)
  } else if (!count && reconcileTimer) {
    clearInterval(reconcileTimer)
    reconcileTimer = null
  }
})

export function useNotificationCenter() {
  if (!started && api.getToken()) {
    started = true
    clockTimer = setInterval(() => { now.value = Date.now() }, 1000)
    api.onUnauthorized(reset)
    hydrate()
    useRealtime('notification-center', {
      // The badge itself is another subscriber's job, but zero unread is the
      // one count that contradicts local state: nothing can still be folded
      // away if there is nothing left unread (a retention prune, say, deletes
      // rows without ever sending a `resolved`).
      receive: (counts) => {
        if (!counts.inbox_unread) foldedIds.value = new Set()
      },
      refresh: hydrate,
      onEvent: (name, payload) => {
        if (name === 'notification') ingest(payload)
        else if (name === 'resolved') retire(payload)
      },
    })
  }
  return {
    toasts, folded, blocker, blockers, blockerFolded, resumedTraceId,
    suppressed, awaitingTraceIds, bannerVisible, stripVisible,
    blockerWaitedFor, waitedForTrace, blockerCount, blockerPos,
    ingest, retire, fold, drop, markRead, reset, hydrate, refreshBlockers,
    foldBlocker, unfoldBlocker, dismissBlocker, clearBlocker,
    nextBlocker, prevBlocker, showBlockerAt,
    answerBlocker, decideBlocker,
    setSuppressed: (value) => { suppressed.value = !!value },
    onOpen: (handler) => { openHandler = handler },
  }
}
