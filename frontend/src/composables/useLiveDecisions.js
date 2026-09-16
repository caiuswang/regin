import { ref } from 'vue'
import api from '../api'
import { isDecisionMessage } from '../constants/inboxTypes'

// A parked-agent card is only actionable while its session is still there to
// receive the answer, and `status='active'` alone is NOT that signal: a
// session that dies without an end event stays `active` forever, so the inbox
// accumulates long-orphaned `permission-pending` cards.
//
// This used to re-derive that judgement here, by aging /api/sessions rows
// against a 10-minute window. That was the same rule the server has since
// repudiated (lib/agent_messages/blockers._live_hook_trace_ids): an agent
// parked on a question emits nothing until it is answered, so the window was
// measuring the wait and hiding exactly the sessions this surface exists for.
// Re-deriving it also guaranteed drift — the banner and the inbox answered
// "is this parked?" with two different rules. Ask the one authority instead:
// /agent-messages/blockers already applies the park derivation, the retire
// signals, dismissal, and the reachability proof that replaced the window.
export function useLiveDecisions() {
  const liveTraceIds = ref(new Set())

  async function refreshLive() {
    try {
      const data = await api.get('/agent-messages/blockers')
      const fresh = new Set()
      for (const b of data.blockers || []) {
        if (b.trace_id) fresh.add(b.trace_id)
        // An SDK run is traced twice; the inbox rows carry the CLI child's id
        // while the card is keyed by the `sdk-` one, so a card that names an
        // alias must match under both or its own row reads as unparked.
        for (const alias of b.alias_trace_ids || []) fresh.add(alias)
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
