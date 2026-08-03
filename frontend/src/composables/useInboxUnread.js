import { ref } from 'vue'
import api from '../api'
import { createSequencer, useRealtime } from './useRealtime'

// Module-singleton so the nav badge and the Inbox view share one counter
// and one subscription (mirrors useDriftSummary).
const unread = ref(0)
// The most severe type still unread. A count alone cannot say whether it is a
// parked agent or seven progress lines, and the badge should not colour those
// the same. Pushed with the count so the two can never disagree.
const severity = ref(null)
const seq = createSequencer()
let painted = false

async function refresh() {
  const ticket = seq.claim()
  try {
    const resp = await api.get('/agent-messages/unread-count')
    seq.commit(ticket, () => {
      unread.value = resp.count || 0
      severity.value = resp.severity ?? null
    })
  } catch { /* ignore — keep last known value */ }
}

export function useInboxUnread() {
  if (api.getToken()) {
    if (!painted) {
      painted = true
      refresh()
    }
    useRealtime('inbox', {
      receive: (counts) => seq.commit(seq.claim(), () => {
        unread.value = counts.inbox_unread || 0
        severity.value = counts.inbox_severity ?? null
      }),
      refresh,
    })
  }
  return { unread, severity, refresh }
}
