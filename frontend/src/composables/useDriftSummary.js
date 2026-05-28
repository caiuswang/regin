import { ref } from 'vue'
import api from '../api'

const pending = ref(0)
let polling = null

async function refresh() {
  try {
    const resp = await api.get('/schema-drift/summary')
    pending.value = resp.pending || 0
  } catch { /* ignore — keep last known value */ }
}

export function useDriftSummary() {
  if (!polling) {
    refresh()
    polling = setInterval(refresh, 60_000)
  }
  return { pending, refresh }
}
