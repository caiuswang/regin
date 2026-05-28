import { ref } from 'vue'
import api from '../api'

const enabled = ref(true)
let loaded = false

async function refresh() {
  try {
    const resp = await api.get('/diagnostics/state')
    enabled.value = !!resp.enabled
  } catch { /* keep previous value */ }
}

async function setEnabled(value) {
  const resp = await api.post('/diagnostics/state', { enabled: !!value })
  enabled.value = !!resp.enabled
  return enabled.value
}

export function useDiagnosticsState() {
  if (!loaded) { loaded = true; refresh() }
  return { enabled, refresh, setEnabled }
}
