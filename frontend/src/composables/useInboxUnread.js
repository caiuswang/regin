import { ref } from 'vue'
import api from '../api'

// Module-singleton so the nav badge and the Inbox view share one counter
// and one poll loop (mirrors useDriftSummary).
const unread = ref(0)
let polling = null

async function refresh() {
  try {
    const resp = await api.get('/agent-messages/unread-count')
    unread.value = resp.count || 0
  } catch { /* ignore — keep last known value */ }
}

export function useInboxUnread() {
  if (!polling) {
    refresh()
    polling = setInterval(refresh, 20_000)
  }
  return { unread, refresh }
}
