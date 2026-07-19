import api from '../api'

const STREAM_PATH = '/api/notifications/stream'
const TICKET_TIMEOUT_MS = 8_000
const STREAM_STALE_MS = 60_000
const FALLBACK_POLL_MS = 30_000
const MAX_BACKOFF_MS = 30_000
const FALLBACK_AFTER_ATTEMPTS = 3

const LOCK_NAME = 'regin-badge-stream'
const RELAY_NAME = 'regin-badges'

const subscribers = new Map()

let channel = null
let leading = false
let source = null
let connecting = false
let generation = 0
let attempts = 0
let reconnectTimer = null
let fallbackTimer = null
let staleTimer = null
let listenersBound = false

// Two async sources write one ref — the stream and the REST refresh a view
// fires after mutating. Without ordering, a slow refresh can land after a
// newer pushed frame and reinstate the older number.
export function createSequencer() {
  let issued = 0
  let applied = 0
  return {
    claim: () => ++issued,
    commit(ticket, write) {
      if (ticket <= applied) return
      applied = ticket
      write()
    },
  }
}

export function useRealtime(key, { receive, refresh }) {
  subscribers.set(key, { receive, refresh })
  bindListeners()
  leadOrFollow()
}

// Browsers cap an origin at ~6 HTTP/1.1 connections and a stream holds one
// for the tab's lifetime, so one stream per tab locks the origin solid at the
// sixth: every request hangs and a seventh tab cannot load the app at all.
// One tab holds the lock and the stream; the rest read its relay. The lock
// releases when that tab closes, and the next one takes over.
function leadOrFollow() {
  if (!navigator.locks || !window.BroadcastChannel) {
    connect()
    return
  }
  if (channel) return
  channel = new BroadcastChannel(RELAY_NAME)
  channel.onmessage = (event) => {
    if (!leading) applyCounts(event.data)
  }
  navigator.locks.request(LOCK_NAME, () => {
    leading = true
    connect()
    // Held until this tab goes away; releasing is what promotes the next.
    return new Promise(() => {})
  })
}

async function connect() {
  if (source || connecting || reconnectTimer || !api.getToken()) return
  connecting = true
  const claimed = ++generation
  let ticket
  try {
    const resp = await api.post('/auth/stream-ticket', null,
      { signal: AbortSignal.timeout(TICKET_TIMEOUT_MS) })
    ticket = resp.ticket
  } catch {
    // A 401 routes through handleUnauthorized -> disconnect(), which bumps
    // the generation; retrying then would only 401 again.
    if (claimed === generation) {
      connecting = false
      scheduleReconnect()
    }
    return
  }
  if (claimed !== generation) return
  connecting = false
  openStream(ticket)
}

function openStream(ticket) {
  const stream = new EventSource(
    `${STREAM_PATH}?ticket=${encodeURIComponent(ticket)}`)
  source = stream
  stream.onopen = () => {
    attempts = 0
    stopFallback()
  }
  stream.onmessage = (event) => {
    markAlive()
    applyCounts(event.data)
  }
  stream.addEventListener('ping', markAlive)
  // EventSource retries on its own, but the ticket is single-use, so its
  // retry can only 401 forever. Own the reconnect instead.
  stream.onerror = () => reset(stream)
  markAlive()
}

function reset(stream) {
  stream.close()
  if (source !== stream) return
  source = null
  clearStale()
  scheduleReconnect()
}

function markAlive() {
  clearStale()
  staleTimer = setTimeout(() => {
    // A peer lost to a sleeping laptop keeps the stream looking open, so
    // silence past two keepalives is the only evidence it is gone.
    if (source) reset(source)
  }, STREAM_STALE_MS)
}

function clearStale() {
  if (!staleTimer) return
  clearTimeout(staleTimer)
  staleTimer = null
}

function scheduleReconnect() {
  if (!api.getToken()) {
    stopFallback()
    return
  }
  if (reconnectTimer) return
  attempts += 1
  if (attempts > FALLBACK_AFTER_ATTEMPTS) startFallback()
  const delay = Math.min(1_000 * 2 ** (attempts - 1), MAX_BACKOFF_MS)
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null
    connect()
  }, delay)
}

function applyCounts(payload) {
  let counts = payload
  if (typeof payload === 'string') {
    try {
      counts = JSON.parse(payload)
    } catch {
      return
    }
  }
  if (leading && channel) channel.postMessage(counts)
  for (const subscriber of subscribers.values()) subscriber.receive(counts)
}

function refreshAll() {
  if (!api.getToken()) return
  for (const subscriber of subscribers.values()) subscriber.refresh()
}

function startFallback() {
  if (fallbackTimer) return
  fallbackTimer = setInterval(refreshAll, FALLBACK_POLL_MS)
}

function stopFallback() {
  if (!fallbackTimer) return
  clearInterval(fallbackTimer)
  fallbackTimer = null
}

function disconnect() {
  generation += 1
  connecting = false
  attempts = 0
  stopFallback()
  clearStale()
  if (reconnectTimer) {
    clearTimeout(reconnectTimer)
    reconnectTimer = null
  }
  const stream = source
  source = null
  if (stream) stream.close()
}

function resume() {
  // A follower has no stream to resume; it is already queued for the lock.
  if (channel && !leading) return
  if (source || connecting || reconnectTimer) return
  connect()
}

function bindListeners() {
  if (listenersBound) return
  listenersBound = true
  window.addEventListener('focus', resume)
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') resume()
  })
  api.onUnauthorized(disconnect)
}
