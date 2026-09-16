import { useRealtime } from './useRealtime'

// The stranded page's wake-up line.
//
// `/clear` ends the conversation and opens another under a new id, so the page
// an operator is holding goes quiet forever — there is no poll to discover the
// heir with, because the heir's spans land under a trace this page is not
// watching. The server raises a `session_successor` frame naming THIS page's id
// the moment the succession becomes resolvable, which is the only signal that
// can arrive without asking for it.
//
// One subscription for the whole app holding a single listener slot, exactly
// like useTraceSpanEvents: at most one session view is mounted at a time.
let listener = null
let registered = false

function register() {
  if (registered) return
  registered = true
  useRealtime('session-successor', {
    // The badge counters belong to the surfaces that own them; this subscriber
    // exists only for the named frame. `refresh` is the recovery leg: it fires
    // after the stream reconnects, when frames were delivered to nobody, so a
    // succession announced during the outage is re-read from the summary
    // rather than lost.
    receive: () => {},
    refresh: () => listener?.(null),
    onEvent: (name, payload) => {
      if (name !== 'session_successor') return
      if (payload?.trace_id && payload?.successor_trace_id) listener?.(payload)
    },
  })
}

// `fn(frame)` runs when a succession is announced; `frame` is
// `{ trace_id, successor_trace_id }`, or null when the stream reconnected and
// the caller should re-read its own summary instead. Returns the detach.
export function onSessionSuccessor(fn) {
  register()
  listener = fn
  return () => { if (listener === fn) listener = null }
}
