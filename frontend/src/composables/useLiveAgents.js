import { computed, reactive } from 'vue'
import { fmtTime } from '../utils/traceFormatters.js'

// One roster entry from the server's whole-session snapshot. startSpan is
// resolved against the LOADED tail — null when the marker sits outside the
// window (the sheet then hides the span-detail chevron; scoping still works
// off agentId). `waiting` (blocked on a human answer/permission) counts as
// running for the badge but renders its own amber treatment.
function fromServerEntry(e, spans) {
  const startSpan = e.start_span_id
    ? spans.find(s => s.span_id === e.start_span_id) || null
    : null
  return {
    spanId: e.start_span_id || `roster-${e.agent_id}`,
    startSpan,
    agentId: e.agent_id,
    agentType: e.agent_type || 'agent',
    description: e.description || '',
    promptPreview: e.prompt_preview || '',
    resultPreview: e.result_preview || '',
    durationMs: e.duration_ms || 0,
    startTime: e.started_at || '',
    startClock: e.started_at ? fmtTime(e.started_at) : '',
    status: e.status,
    running: e.status === 'running' || e.status === 'waiting',
    lastSeenClock: e.last_seen ? fmtTime(e.last_seen) : '',
    spanCount: e.span_count || 0,
  }
}

// Roster of subagent runs for the /live card's Agents button + sheet.
// The server's `agent_roster` summary is the single source of truth —
// computed over the WHOLE session, so agents whose markers sit outside the
// loaded tail window (or were lost to an ingest outage and reconstructed)
// still count. The server owns classification (running | waiting | finished
// | interrupted | stale); the client only projects entries onto loaded
// spans. Empty until the first summary lands.
export function useLiveAgents(getSpans, getRoster) {
  const agents = computed(() => {
    const roster = getRoster ? getRoster() : null
    if (!Array.isArray(roster)) return []
    const spans = getSpans() || []
    return roster.map(e => fromServerEntry(e, spans))
  })

  // `running` includes waiting-for-input agents — they are alive and count
  // toward the header badge; the sheet renders them amber inside the
  // running group.
  const runningAgents = computed(() => agents.value.filter(a => a.running))
  // The "Finished" disclosure group holds every non-running outcome —
  // finished, interrupted, stale — all still scopeable (their spans stay
  // investigable; that's the point of keeping them reachable).
  const finishedAgents = computed(() => agents.value.filter(a => !a.running))
  const runningCount = computed(() => runningAgents.value.length)
  const waitingCount = computed(() =>
    agents.value.filter(a => a.status === 'waiting').length)

  // reactive() so nested refs unwrap when the single `liveAgents` object is
  // read in the view template (no per-field destructuring → no extra
  // top-level decls against the SFC surface-area budget).
  return reactive({ agents, runningAgents, finishedAgents, runningCount, waitingCount })
}
