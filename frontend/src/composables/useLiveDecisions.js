import { ref } from 'vue'
import api from '../api'
import { isDecisionMessage } from '../constants/inboxTypes'
import { serverAgeMs, STALE_FALLBACK_WINDOW_MS } from '../utils/sessionActivity'

// A parked-agent card is only actionable while its session is still there to
// receive the answer. `status='active'` alone is NOT that signal: a session
// that dies without an end event stays `active` forever, so the inbox
// accumulates long-orphaned `permission-pending` cards. One measurement of
// the live DB (2026-07-31) found 30 such cards, 7 whose session still claimed
// `active`, and 1 genuinely live: the recency term is what collapses 30 → 1,
// and the absolute figures only grow with the feed. Gate the
// "needs your decision" surface on liveness, never on the mere absence of a
// resolve (which `events.resolve` frequently never delivers).
export function useLiveDecisions() {
  const liveTraceIds = ref(new Set())

  // `size`, not `limit`: /api/sessions ignores `limit` and falls back to a
  // default page of 50, which silently hid active sessions from the set. 200
  // is the endpoint's hard cap, and rows come back last_seen DESC, so the
  // 10-minute window is always inside the first page.
  async function refreshLive() {
    try {
      const data = await api.get('/sessions?active=active&size=200')
      // Age server-minus-server. The API serialises naive host-local stamps,
      // so a viewer in a different timezone than the host measuring against
      // its own Date.now() reads every session as hours stale (or hours in
      // the future) — the exact trap utils/sessionActivity documents.
      const clock = {
        local: data.server_now, utc: data.server_now_utc, atMs: Date.now(),
      }
      const fresh = new Set()
      for (const s of data.sessions || []) {
        if (!s.trace_id) continue
        const age = serverAgeMs(s.last_seen, clock.local ? clock : null)
        if (age !== null && age >= 0 && age < STALE_FALLBACK_WINDOW_MS) fresh.add(s.trace_id)
      }
      liveTraceIds.value = fresh
    } catch {
      // A failed probe must not invent decisions — an empty set degrades to
      // "nothing is parked", which is the safe direction to be wrong in.
      liveTraceIds.value = new Set()
    }
  }

  // Is the agent still parked? Read state deliberately plays no part:
  // acknowledging a notification is not answering it, and gating on `read_at`
  // meant "Mark read" removed the "Answer in live" button while the agent was
  // still waiting for exactly that answer.
  function isParked(message) {
    return isDecisionMessage(message) && liveTraceIds.value.has(message.trace_id)
  }

  // Does it still need chasing? This one DOES respect read state — it drives
  // the nagging surfaces (the header pill, the section, the row badge), which
  // the operator needs a way to quiet once they have dealt with it.
  function isLiveDecision(message) {
    return isParked(message) && !message.read_at
  }

  return { liveTraceIds, refreshLive, isLiveDecision, isParked }
}
