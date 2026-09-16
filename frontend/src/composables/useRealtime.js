import api from '../api'

const STREAM_PATH = '/api/notifications/stream'
const TICKET_TIMEOUT_MS = 8_000
const STREAM_STALE_MS = 60_000
const FALLBACK_POLL_MS = 30_000
const MAX_BACKOFF_MS = 30_000
const FALLBACK_AFTER_ATTEMPTS = 3

const LOCK_NAME = 'regin-badge-stream'
const RELAY_NAME = 'regin-badges'

// Not `crypto.randomUUID`: that is gated to secure contexts, and the
// dashboard is routinely reached over plain http on a LAN address (the phone
// answering an agent's prompt). A module-scope call would throw there before
// the app rendered anything — a blank page on every route, since the app shell
// imports this. `getRandomValues` carries no such gate.
function randomId() {
  const bytes = new Uint8Array(16)
  crypto.getRandomValues(bytes)
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
}

// This tab's name for its stream. The leader opens the stream under it and
// then declares the union of everyone's interests against it; the server pairs
// the id with the authenticated user, so it is a handle, not a credential.
const STREAM_ID = randomId()

// Counts ride SSE's *unnamed* event, whose DOM name is 'message'; the relay
// reuses that label so leader and follower speak one vocabulary.
const COUNTS = 'message'
const NAMED_EVENTS = ['notification', 'resolved', 'trace_spans', 'session_successor']

const subscribers = new Map()

// What this tab wants, per subscriber key, and what the other tabs told us
// they want, per tab. Only the leader holds a stream, so the frames a follower
// needs are only requested if the leader asks for them on its behalf.
const localInterests = new Map()
const remoteInterests = new Map()
const TAB_ID = randomId()

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
let sentTopics = null
let sendingTopics = false
let topicsDirty = false
let topicsUnsent = false

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

// `receive` takes the badge counts; `onEvent(name, payload)` takes the named
// frames (`notification`, `resolved`) and is optional — a subscriber that only
// wants the badge stays unchanged.
export function useRealtime(key, { receive, refresh, onEvent }) {
  subscribers.set(key, { receive, refresh, onEvent })
  bindListeners()
  leadOrFollow()
}

// Name the addressed frames this subscriber wants. Unaddressed frames arrive
// regardless; declaring nothing (the default) means only those.
export function setInterests(key, topics) {
  const next = [...new Set(topics || [])].sort()
  const current = localInterests.get(key)
  if (current && current.length === next.length
      && current.every((t, i) => t === next[i])) return
  if (next.length) localInterests.set(key, next)
  else localInterests.delete(key)
  announceInterests()
}

// A follower has no stream to declare against, so it hands its interests to
// whichever tab does. The leader folds them into the union it posts; the
// relay stays a same-user, same-browser channel, so this is noise reduction,
// never a disclosure boundary.
function announceInterests() {
  if (leading || !channel) pushTopics()
  if (channel && !leading) {
    channel.postMessage({ ctl: 'interests', tab: TAB_ID, topics: unionLocal() })
  }
}

// A tab that is closed rather than navigated leaves nothing behind, so the
// leader would carry its topics for the rest of its own life. `pagehide` is
// the last event a closing tab reliably gets, and it fires for the bfcache
// case too — where the relay is already frozen, so a retraction that is never
// sent costs a stale topic, not a lost one.
function retractInterests() {
  if (!channel || leading) return
  channel.postMessage({ ctl: 'interests', tab: TAB_ID, topics: [] })
}

function unionLocal() {
  const out = new Set()
  for (const topics of localInterests.values()) for (const t of topics) out.add(t)
  return [...out]
}

// This tab's own interests first. The server caps the list it stores, so the
// order IS the priority: sorting would let an alphabetically early topic from
// some other tab push the session on screen here off the end of the cap, and
// the request would still answer 200.
function unionAll() {
  const out = new Set(unionLocal())
  for (const topics of remoteInterests.values()) for (const t of topics) out.add(t)
  return [...out]
}

// Post the union, but only from the tab that owns the stream and only once it
// is open — the subscription the server updates is created by the stream's own
// request. A post already in flight is not cancelled: the newer set is marked
// dirty and sent when it lands, so the last write wins without a timer.
async function pushTopics() {
  if (!leading && channel) return
  if (!source || !api.getToken()) return
  if (sendingTopics) { topicsDirty = true; return }
  const topics = unionAll()
  const wire = topics.join('\u0000')
  if (wire === sentTopics) return
  // Nothing declared and nothing to declare: a stream starts interested in
  // nothing, so saying so is a round-trip that changes no state. A RETRACTION
  // is not that case even though it is also empty — the server is still waking
  // this stream for a trace nobody reads — which is why the failed-post flag
  // is checked separately rather than inferred from an empty union.
  if (!topics.length && sentTopics === null && !topicsUnsent) return
  sendingTopics = true
  try {
    await api.post('/notifications/stream/topics',
      { stream: STREAM_ID, topics })
    sentTopics = wire
    topicsUnsent = false
  } catch {
    // A 404 means the stream this id named is gone, and the next `onopen`
    // re-posts against the new subscription. A transient failure brings no
    // reconnect, though, so leaving it here would strand the stream
    // undeclared for its whole life — the keepalive retries it instead.
    sentTopics = null
    topicsUnsent = true
  } finally {
    sendingTopics = false
    if (topicsDirty) { topicsDirty = false; pushTopics() }
  }
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
    const data = event.data || {}
    if (data.ctl) return control(data)
    if (leading) return
    const { name, payload } = data
    if (name === COUNTS) applyCounts(payload)
    else if (name) applyEvent(name, payload)
  }
  navigator.locks.request(LOCK_NAME, () => {
    leading = true
    // The previous leader took its tally of who wants what with it, and a
    // BroadcastChannel has no backlog to replay, so the new one has to ask.
    channel.postMessage({ ctl: 'need-interests' })
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
    `${STREAM_PATH}?ticket=${encodeURIComponent(ticket)}`
    + `&stream=${encodeURIComponent(STREAM_ID)}`)
  source = stream
  stream.onopen = () => {
    // A RE-connect means frames were delivered to nobody while we were away,
    // and a frame is delivered once — so every subscriber has to re-read or it
    // silently misses whatever happened during the gap. Only on a reconnect:
    // the first open follows each subscriber's own initial read. Previously
    // this leg existed only behind the fallback poll, which arms after
    // FALLBACK_AFTER_ATTEMPTS, so a short outage recovered with a stale view.
    const missedFrames = attempts > 0
    attempts = 0
    stopFallback()
    // A reconnect is a NEW subscription on the server, interested in nothing
    // until it is told otherwise, so the declaration is part of opening the
    // stream rather than something done once per interest change.
    sentTopics = null
    topicsUnsent = false
    pushTopics()
    if (missedFrames) refreshAll()
  }
  stream.onmessage = (event) => {
    markAlive()
    applyCounts(event.data)
  }
  for (const name of NAMED_EVENTS) {
    stream.addEventListener(name, (event) => {
      markAlive()
      applyEvent(name, parse(event.data))
    })
  }
  stream.addEventListener('ping', () => {
    markAlive()
    // The only event this stream is guaranteed to keep producing, so it is
    // what a failed declaration is retried on. `pushTopics` is a no-op once
    // the union on the server matches.
    if (sentTopics === null) pushTopics()
  })
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

function control({ ctl, tab, topics }) {
  if (ctl === 'need-interests') {
    if (!leading) {
      channel.postMessage({ ctl: 'interests', tab: TAB_ID, topics: unionLocal() })
    }
    return
  }
  if (ctl !== 'interests' || !leading || !tab) return
  if (topics?.length) remoteInterests.set(tab, topics)
  else remoteInterests.delete(tab)
  pushTopics()
}

function parse(payload) {
  if (typeof payload !== 'string') return payload
  try {
    return JSON.parse(payload)
  } catch {
    return null
  }
}

function applyCounts(payload) {
  const counts = parse(payload)
  if (!counts) return
  relay(COUNTS, counts)
  for (const subscriber of subscribers.values()) subscriber.receive(counts)
}

function applyEvent(name, payload) {
  if (!payload) return
  relay(name, payload)
  for (const subscriber of subscribers.values()) subscriber.onEvent?.(name, payload)
}

function relay(name, payload) {
  if (leading && channel) channel.postMessage({ name, payload })
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
  sentTopics = null
  topicsUnsent = false
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
  window.addEventListener('pagehide', retractInterests)
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') resume()
  })
  api.onUnauthorized(disconnect)
}
