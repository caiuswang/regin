import api from '../api'
import { setInterests, useRealtime } from './useRealtime'

// The open trace view's wake-up line.
//
// SessionTraceView retires its live poll once a session stops changing, which
// would otherwise leave it deaf to a session that goes quiet and then resumes.
// The span ingest raises a `trace_spans` frame per accepted batch, so the
// resume arrives as an event rather than being discovered by a poll that is no
// longer running.
//
// The frame is addressed `trace:<canonical id>`, so a stream that has not
// named the open trace is never woken by another session's ingest at all.
const TOPIC_PREFIX = 'trace:'
const KEY = 'trace-spans'

// One subscription for the whole app, registered on first use, holding a
// single listener slot rather than a set: exactly one trace is open at a time
// — the same reason useTraceSearchSource holds one source.
let listener = null
let registered = false

function register() {
  if (registered || !api.getToken()) return
  registered = true
  useRealtime(KEY, {
    // The badge counters and the periodic re-read belong to the surfaces that
    // own them; this subscriber exists only for the named frame. `refresh`
    // still has work to do: it fires after the stream reconnects, when frames
    // were delivered to nobody, so a wake-up missed during the outage is
    // recovered by waking unconditionally.
    receive: () => {},
    refresh: () => listener?.(null),
    onEvent: (name, payload) => {
      if (name !== 'trace_spans') return
      listener?.(payload?.trace_id || null, payload || null)
    },
  })
}

// `fn(traceId, frame)` runs when spans land; `traceId` is null when the stream
// reconnected and cannot say which trace moved. `frame` carries `epoch` and
// the `span_ids` that moved, or is null for the same reconnect case. Returns
// the detach, which also drops the interest — a view that has gone away must
// not keep its session on the stream.
export function onTraceSpans(fn) {
  register()
  listener = fn
  return () => {
    if (listener !== fn) return
    listener = null
    setInterests(KEY, [])
  }
}

// Name the sessions this view wants woken for. Both the route id and the
// canonical id are declared: the ingest addresses the canonical one, but a
// view that has not loaded its session yet knows only what is in the URL, and
// for most sessions the two are the same id anyway.
//
// `fn` is the caller's own span handler, and it is a gate, not a formality:
// the trace route is keyed by path, so session A -> session B REMOUNTS the
// view, and Vue runs B's setup before A's unmount hook. A declaration made
// from setup would therefore be wiped by A's teardown moments later, leaving
// B on a stream that names nobody. Only the holder of the listener slot may
// speak for it, so a view declares after `onTraceSpans` has handed it over.
export function setTraceInterest(fn, traceIds) {
  if (listener !== fn) return
  setInterests(KEY, (traceIds || []).filter(Boolean)
    .map((id) => `${TOPIC_PREFIX}${id}`))
}
