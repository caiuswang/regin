import { ref } from 'vue'
import api from '../api'
import { createSequencer, useRealtime } from './useRealtime'

const pending = ref(0)
const seq = createSequencer()
let painted = false

async function refresh() {
  const ticket = seq.claim()
  try {
    const resp = await api.get('/schema-drift/summary')
    seq.commit(ticket, () => { pending.value = resp.pending || 0 })
  } catch { /* ignore — keep last known value */ }
}

export function useDriftSummary() {
  if (api.getToken()) {
    if (!painted) {
      painted = true
      refresh()
    }
    useRealtime('drift', {
      receive: (counts) => seq.commit(seq.claim(), () => {
        pending.value = counts.drift_pending || 0
      }),
      refresh,
    })
  }
  return { pending, refresh }
}
