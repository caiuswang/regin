import { ref } from 'vue'
import api from '../api'

// null = not yet answered by the server. Chrome gated on this (the
// Diagnostics nav group, the disabled-state banners) must render neither
// state until it resolves: an optimistic default paints the wrong chrome
// for every install on the other side of it, then yanks it once
// /diagnostics/state lands (the server default is OFF, per-machine opt-in).
const enabled = ref(null)
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
  if (!loaded && api.getToken()) { loaded = true; refresh() }
  return { enabled, refresh, setEnabled }
}
