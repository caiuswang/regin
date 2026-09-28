import { getCurrentInstance, onActivated, onBeforeUnmount, onDeactivated, onMounted } from 'vue'
import api from '../api'
import { setInterests, useRealtime } from './useRealtime'

// The Pipeline's freshness line. The server raises a `pipeline` frame after
// every committed task / feature / defect write (lib/pipeline/live.py) —
// including a CLI write, which reaches the server over the loopback — and a
// mounted view re-reads its own data when one lands. Nothing here polls.
const KEY = 'pipeline'
const TOPIC = 'pipeline'

const listeners = new Set()
let registered = false
let holders = 0

function register() {
  if (registered || !api.getToken()) return
  registered = true
  useRealtime(KEY, {
    receive: () => {},
    // A reconnect means frames went to nobody, so every view re-reads.
    refresh: () => dispatch(null),
    onEvent: (name, payload) => { if (name === 'pipeline') dispatch(payload) },
  })
}

function dispatch(frame) {
  for (const fn of listeners) fn(frame)
}

// Held by every live view while it is mounted — the shell and the task detail
// page, which sits outside the shell — so no stream is woken for pipeline
// writes once the operator has left them all.
function holdPipelineTopic() {
  register()
  holders += 1
  if (holders === 1) setInterests(KEY, [TOPIC])
  return () => {
    holders = Math.max(0, holders - 1)
    if (holders === 0) setInterests(KEY, [])
  }
}

export function onPipelineEvent(fn) {
  register()
  listeners.add(fn)
  return () => listeners.delete(fn)
}

// One read at a time. An event that lands while a read is in flight marks the
// data dirty, and exactly one more read follows the current one, however many
// events arrived meanwhile — never a request per event.
export function createLiveReader(read) {
  let inFlight = false
  let dirty = false
  async function run() {
    if (inFlight) { dirty = true; return }
    inFlight = true
    try {
      do {
        dirty = false
        try { await read() } catch { /* the view renders its own error */ }
      } while (dirty)
    } finally {
      inFlight = false
    }
  }
  return run
}

// Re-read `read` on every pipeline event while the calling view is on screen.
// A KeepAlive'd tab that is off screen only remembers it missed something and
// re-reads once when it comes back, so hidden tabs cost no requests.
export function usePipelineLive(read) {
  const run = createLiveReader(read)
  let active = true
  let missed = false
  const detach = onPipelineEvent(() => {
    if (active) run()
    else missed = true
  })
  if (getCurrentInstance()) {
    let release = null
    onMounted(() => { active = true; release = holdPipelineTopic() })
    onActivated(() => {
      active = true
      if (missed) { missed = false; run() }
    })
    onDeactivated(() => { active = false })
    onBeforeUnmount(() => { detach(); release?.() })
  }
  return run
}
