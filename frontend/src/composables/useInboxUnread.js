import { ref } from 'vue'
import api from '../api'
import { createSequencer, useRealtime } from './useRealtime'

// Module-singleton so the nav badge and the Inbox view share one counter
// and one subscription (mirrors useDriftSummary).
const unread = ref(0)
const seq = createSequencer()
let painted = false

async function refresh() {
  const ticket = seq.claim()
  try {
    const resp = await api.get('/agent-messages/unread-count')
    seq.commit(ticket, () => { unread.value = resp.count || 0 })
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
      }),
      refresh,
    })
  }
  return { unread, refresh }
}
