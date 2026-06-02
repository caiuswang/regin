import { computed } from 'vue'

// Session-level timeline bounds for the overview strip + header duration.
// `session` and `allSpans` are shared refs/computeds owned by the SFC.
//
// Bounds come from the DB session row (pre-computed at ingest) and fall back
// to a span-scan only when that row is missing. The live edge (`traceEnd`)
// also folds in the latest loaded-span timestamp so an in-progress span keeps
// the header duration growing before its end_time lands.
export function useTraceTimeline(session, allSpans) {
  const traceStart = computed(() => {
    if (session.value?.started_at) return new Date(session.value.started_at).getTime()
    if (!allSpans.value.length) return null
    return Math.min(...allSpans.value.map(s => new Date(s.start_time).getTime()))
  })

  const traceEnd = computed(() => {
    // ended_at marks when the session formally ended; last_seen tracks
    // MAX(span.end_time). For well-formed sessions they agree, but if a
    // session is resumed after `ended_at` is set, later spans push
    // last_seen forward without resetting ended_at.
    //
    // We also fold in the latest timestamp across the loaded spans. This
    // is what keeps the header duration *live*: an in-progress span (the
    // assistant still responding) has a null end_time, so the server's
    // last_seen — which is MAX(end_time) — doesn't advance until that
    // span finishes. Without the span-scan the duration freezes at the
    // last completed span's end until the active turn completes and a
    // later reload bumps last_seen. Using start_time for unfinished spans
    // lets the timeline grow the moment new live spans stream in.
    //
    // Completed spans never exceed last_seen (it's their MAX(end_time)),
    // so lazy-expanding old subtrees can't reshape the range — only the
    // live edge moves it. Take the max of all three so late spans never
    // overflow the timeline panel (offsetPct past 100%).
    const endedAt = session.value?.ended_at ? new Date(session.value.ended_at).getTime() : null
    const lastSeen = session.value?.last_seen ? new Date(session.value.last_seen).getTime() : null
    // Single-pass max over loaded spans — runs once per span-set change,
    // not per render (the computed is cached). Avoids `Math.max(...spread)`
    // so a very large span count can't overflow the call stack.
    let spanMax = null
    for (const s of allSpans.value) {
      const t = s.end_time ? new Date(s.end_time).getTime() : new Date(s.start_time).getTime()
      if (spanMax === null || t > spanMax) spanMax = t
    }
    let end = null
    for (const v of [endedAt, lastSeen, spanMax]) {
      if (v != null && (end === null || v > end)) end = v
    }
    return end
  })

  const traceDuration = computed(() => {
    if (!traceStart.value || !traceEnd.value) return 0
    return Math.max(traceEnd.value - traceStart.value, 1)
  })

  // Server-side aggregate; see web/trace_projection._compute_active_work_ms
  // for the gap-based definition. Always populated since migration 0004 +
  // the backfill, so no client-side recomputation is needed.
  const activeWorkMs = computed(() => session.value?.active_work_ms ?? 0)

  return { traceStart, traceEnd, traceDuration, activeWorkMs }
}
