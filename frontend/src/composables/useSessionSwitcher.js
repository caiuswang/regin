// One roster, two surfaces: the ⌘J palette facet and the status rail under
// every trace view. Module-scoped state, not per-component, because both are
// mounted at once and a second copy would mean two polls and two answers to
// "which session is waiting on you".
//
// Polling is reference-counted: a surface calls `hold()` while it is visible
// and releases on unmount, and the timer only exists while someone is holding.
// The rail is ambient and the palette is momentary, so they share a cadence
// rather than each picking one.

import { computed, onBeforeUnmount, ref } from 'vue'
import { useRouter } from 'vue-router'
import api from '../api'
import { MIN_ID_CHARS } from '../utils/searchDocs.js'
import { orderRows, traceHref } from '../utils/sessionSwitcher.js'

// The roster is not free: the server phases a wider candidate window than it
// returns (so a session parked on a question cannot be trimmed away by
// recency), and phasing is what costs — ~145ms of indexed reads over ~2400
// sessions, most of it irreducible because the rank IS the phase. The rail is
// ambient and its coarsest label is minutes, so a slower cadence is invisible
// here and keeps the duty cycle near 1.5%.
const POLL_MS = 10000

const rows = ref([])
const loading = ref(false)
const loaded = ref(false)
const error = ref(null)
// `{ local, utc, atMs }` — the server's clock at the moment the payload
// landed, so elapsed times are measured server−server rather than against the
// viewer's own timezone.
const clock = ref(null)

// The `#<session-id>` lookup, held apart from the roster on purpose: the two
// answer different questions (what is live vs. what did you address) and a
// lookup that overwrote `rows` would leave the ambient rail rendering one
// session until the next poll.
const lookupRows = ref([])
const lookupLoading = ref(false)
const lookupError = ref(null)
// Same guard the search fan-out uses: every request carries the prefix it was
// issued for, and an answer whose prefix is no longer the one on screen is a
// straggler from an earlier keystroke.
let lookupPrefix = ''
let lookupTimer = null
// Mirrors the palette's own DEBOUNCE_MS. A 36-character id pasted a character
// at a time is 32 lookups otherwise, each of which phases its hits.
const LOOKUP_DEBOUNCE_MS = 140

let holders = 0
// Holders that need each row's `tail` — the palette's preview pane, and only
// while it is open. It is two thirds of the payload and the ambient rail never
// renders it, so the poll asks for it exactly when something is showing it.
let tailHolders = 0
let timer = null
let inFlight = null
// A number-chord that has to fetch first is not instantaneous, and a reader
// who presses twice before the roster lands means the SECOND number — not two
// navigations, and not the first one they changed their mind about. So the
// digit is a slot the newer press overwrites, and only one attach is in
// flight against it.
let attachPending = false
let pendingNumber = null

async function refresh() {
  if (inFlight) return inFlight
  loading.value = true
  inFlight = (async () => {
    try {
      const data = await api.get(
        tailHolders ? '/session-switcher?tail=1' : '/session-switcher')
      rows.value = data.sessions || []
      if (data.server_now) {
        clock.value = {
          local: data.server_now, utc: data.server_now_utc, atMs: Date.now(),
        }
      }
      error.value = null
      loaded.value = true
    } catch (e) {
      error.value = e?.message || 'Could not read the session roster.'
    } finally {
      loading.value = false
      inFlight = null
    }
  })()
  return inFlight
}

async function runLookup(prefix) {
  lookupLoading.value = true
  try {
    const data = await api.get(
      `/session-switcher?id=${encodeURIComponent(prefix)}&tail=1`)
    if (lookupPrefix !== prefix) return
    lookupRows.value = data.sessions || []
    lookupError.value = null
    if (data.server_now) {
      clock.value = {
        local: data.server_now, utc: data.server_now_utc, atMs: Date.now(),
      }
    }
  } catch (e) {
    if (lookupPrefix !== prefix) return
    lookupRows.value = []
    lookupError.value = e?.message || 'Could not look up that session id.'
  } finally {
    if (lookupPrefix === prefix) lookupLoading.value = false
  }
}

// `prefix` is what followed the `#`; null means the reader is not addressing
// anything. `''` is the resting state — below the floor, and never a legal
// argument to `runLookup`, so it can serve as the sentinel a late answer is
// checked against.
function lookup(prefix) {
  if (lookupTimer) clearTimeout(lookupTimer)
  const next = (prefix == null || prefix.length < MIN_ID_CHARS) ? '' : prefix
  if (next !== lookupPrefix) {
    // The rows on screen answer the PREVIOUS prefix. Holding them through the
    // debounce leaves the cursor pinned to a session the query no longer
    // names — and ↵ during that window attaches to it, landing the reader in
    // a different session than the id they are looking at.
    lookupRows.value = []
    lookupError.value = null
  }
  lookupPrefix = next
  if (!next) { lookupLoading.value = false; return }
  lookupLoading.value = true
  lookupTimer = setTimeout(() => runLookup(next), LOOKUP_DEBOUNCE_MS)
}

function startTimer() {
  if (timer) return
  timer = setInterval(refresh, POLL_MS)
}

function stopTimer() {
  if (!timer) return
  clearInterval(timer)
  timer = null
}

export function useSessionSwitcher() {
  const router = useRouter()
  let held = false

  const ordered = computed(() => orderRows(rows.value))

  // `tail: true` for a surface that renders the preview chat; the refresh
  // below re-reads immediately, so the first frame the palette paints already
  // has the tails the ambient poll was not carrying.
  function hold({ tail = false } = {}) {
    if (held) return
    held = tail ? 'tail' : true
    holders += 1
    if (tail) tailHolders += 1
    refresh()
    startTimer()
  }

  function release() {
    if (!held) return
    if (held === 'tail') tailHolders = Math.max(0, tailHolders - 1)
    held = false
    holders = Math.max(0, holders - 1)
    if (!holders) stopTimer()
  }

  onBeforeUnmount(release)

  // Attaching is a route change, not a mode: the trace view IS the reader for
  // whichever session you are attached to, so there is nothing to keep in sync
  // beyond the URL.
  function attach(row) {
    if (!row) return
    router.push(traceHref(row))
  }

  // ⌃1–⌃9 fires from anywhere, including pages that never held the roster —
  // so fetch it on demand rather than making every page poll for a chord it
  // may never see used.
  async function attachByNumber(n) {
    pendingNumber = n
    // An attach is already waiting on the roster; it will read the slot above
    // when it lands, so this press is honoured without a second navigation.
    if (attachPending) return true
    attachPending = true
    try {
      if (!loaded.value) await refresh()
      const row = ordered.value[pendingNumber - 1]
      if (row) attach(row)
      return !!row
    } finally {
      attachPending = false
      pendingNumber = null
    }
  }

  return {
    rows: ordered, loading, loaded, error, clock,
    lookupRows, lookupLoading, lookupError, lookup,
    hold, release, refresh, attach, attachByNumber,
  }
}
