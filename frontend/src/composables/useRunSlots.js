import { ref } from 'vue'
import api from '../api'

const FINISHED = new Set(['done', 'failed'])

// A finished task whose runner is still registered: the agent reported, but
// nobody closed the session, so it keeps a `max_concurrent_runs` slot.
export function finishedHolders(slots) {
  return (slots?.holders || []).filter(h => FINISHED.has(h.task_status))
}

// A refusal whose slot list still reads full; Run stays disabled until a
// Close in that list frees one.
export function slotsFull(refusal) {
  const s = refusal?.slots
  return !!s && s.used >= s.limit
}

// Close one held session through the same stop route /live uses. Resolves to
// the refusal sentence, or '' once the runner took it.
export async function closeRun(traceId) {
  const result = await api.post(`/agent-runs/${traceId}/stop`)
  if (result.ok === false || result.delivered === false) {
    return result.msg || result.detail || 'The session did not take the close'
  }
  return ''
}

/**
 * useRunSlots — `GET /api/agent-runs/slots`, read on demand.
 *
 * Editor-only on the server, so a viewer never asks and reads `null`. Callers
 * re-read after a load or a Close; there is no timer.
 */
export function useRunSlots() {
  const slots = ref(null)
  const canRead = api.getStoredUser()?.role !== 'viewer'

  async function load() {
    if (!canRead) return
    try {
      slots.value = await api.get('/agent-runs/slots')
    } catch {
      slots.value = null
    }
  }

  return { slots, load }
}
