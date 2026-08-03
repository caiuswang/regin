import { computed, ref, shallowRef, watch } from 'vue'
import api from '../api'
import { isDecisionMessage, notificationTier } from '../constants/inboxTypes'
import { useLiveDecisions } from './useLiveDecisions'
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

// Dismissing a blocker cannot resolve it, so "Later" is a snooze, not a close.
const SNOOZE_SECONDS = 45
const RESUMED_LINGER_MS = 6_000

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
const blocker = shallowRef(null)
const blockerSnoozed = ref(false)
const resumedTraceId = ref(null)
const suppressed = ref(false)
const now = ref(Date.now())

// Every message id this tab has already surfaced. A reconnect replays nothing,
// but the leader/follower relay and a same-tab hydrate can both offer the same
// row, and a message must never pop twice.
const seen = new Set()

const foldTimers = new Map()

let snoozeTimer = null
let resumedTimer = null
let clockTimer = null
let started = false
let openHandler = null

// ── Derived ────────────────────────────────────────────────────────

export const folded = computed(() => foldedIds.value.size)
export const awaitingTraceId = computed(() => blocker.value?.trace_id || null)
export const bannerVisible = computed(
  () => !!blocker.value && !blockerSnoozed.value && !suppressed.value)
export const stripVisible = computed(
  () => !!blocker.value && blockerSnoozed.value && !suppressed.value)

export const blockerWaitedFor = computed(() => {
  const since = blocker.value?.since
  if (!since) return ''
  const secs = Math.max(1, Math.round((now.value - since) / 1000))
  return secs < 60 ? `${secs}s` : `${Math.floor(secs / 60)}m ${secs % 60}s`
})

// The prototype's blocker carries its options as buttons. regin's blocker body
// is the formatted permission prompt from `lib/agent_messages/event_notify.py`,
// which renders each option as a `• ` line — so the options are recovered from
// the body rather than invented, and the remaining lines stay the question.
export function parseBlockerBody(body) {
  const lines = (body || '').split('\n')
  const options = []
  const rest = []
  for (const line of lines) {
    const trimmed = line.trim()
    if (trimmed.startsWith('• ')) options.push(trimmed.slice(2).trim())
    else if (trimmed) rest.push(trimmed)
  }
  return { question: rest.join('\n'), options }
}

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

function raiseBlocker(message, replay) {
  clearTimeout(snoozeTimer)
  const previous = blocker.value
  const sameSession = previous?.trace_id === message.trace_id
  blocker.value = {
    ...message,
    ...parseBlockerBody(message.body),
    // The clock measures how long the agent has been parked, so a re-prompt in
    // a session that was already waiting keeps counting from the first one.
    since: sameSession ? previous.since : messageStamp(message),
  }
  blockerSnoozed.value = false
  resumedTraceId.value = null
  if (!replay) osNotify(message, 1)
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
  if (blocker.value && matches(blocker.value, payload)) {
    clearBlocker({ resumed: payload.reason === 'dismissed' })
  }
}

// `resumed` is a claim about the agent, so only a real resolve may make it.
function clearBlocker({ resumed = true } = {}) {
  const traceId = blocker.value?.trace_id
  clearTimeout(snoozeTimer)
  blocker.value = null
  blockerSnoozed.value = false
  if (!traceId || !resumed) return
  resumedTraceId.value = traceId
  clearTimeout(resumedTimer)
  resumedTimer = setTimeout(() => {
    if (resumedTraceId.value === traceId) resumedTraceId.value = null
  }, RESUMED_LINGER_MS)
}

// ── Actions ────────────────────────────────────────────────────────

function snoozeBlocker() {
  if (!blocker.value) return
  blockerSnoozed.value = true
  clearTimeout(snoozeTimer)
  snoozeTimer = setTimeout(() => {
    if (blocker.value) blockerSnoozed.value = false
  }, SNOOZE_SECONDS * 1000)
}

function reopenBlocker() {
  clearTimeout(snoozeTimer)
  blockerSnoozed.value = false
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

function reset() {
  toasts.value = []
  foldedIds.value = new Set()
  seen.clear()
  for (const timer of foldTimers.values()) clearTimeout(timer)
  foldTimers.clear()
  clearTimeout(snoozeTimer)
  clearTimeout(resumedTimer)
  clearInterval(clockTimer)
  clockTimer = null
  blocker.value = null
  blockerSnoozed.value = false
  resumedTraceId.value = null
  started = false
  // `suppressed` and `openHandler` are deliberately NOT cleared: they are
  // registrations from still-mounted components (the route watcher, the toast
  // host), not per-user data. Clearing them on a 401 that does not unmount the
  // app would silently un-suppress /inbox and make OS-notification clicks
  // no-ops, with nothing left to re-register them.
}

// ── Hydration ──────────────────────────────────────────────────────

// The stream pushes what happens *next*; a tab opened (or reconnected) after a
// blocker was raised would otherwise never learn the agent is parked. One read
// of the unread feed closes that hole — replayed, so nothing pops.
async function hydrate() {
  const { refreshLive, isParked } = useLiveDecisions()
  try {
    const [data] = await Promise.all([
      api.get('/agent-messages?unread_only=true&limit=50'),
      refreshLive(),
    ])
    const rows = data.messages || []
    for (const row of [...rows].reverse()) {
      // Only a decision the agent is *still* parked on earns the banner: an
      // unanswered card from a session that has since ended would nag forever.
      if (notificationTier(row.msg_type) === 1
          && !(isDecisionMessage(row) && isParked(row))) {
        seen.add(`${row.id}:${row.version || 1}`)
        continue
      }
      ingest(row, { replay: true })
    }
  } catch { /* a failed hydrate leaves the stream as the only source — fine */ }
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
    toasts, folded, blocker, blockerSnoozed, resumedTraceId, suppressed,
    awaitingTraceId, bannerVisible, stripVisible, blockerWaitedFor,
    snoozeSeconds: SNOOZE_SECONDS,
    ingest, retire, fold, drop, markRead, reset, hydrate,
    snoozeBlocker, reopenBlocker, clearBlocker,
    setSuppressed: (value) => { suppressed.value = !!value },
    onOpen: (handler) => { openHandler = handler },
  }
}
