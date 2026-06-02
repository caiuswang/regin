import { ref } from 'vue'
import api from '../api'

// Per-session tool/token rollup, aggregated server-side from session_spans so
// the header strip doesn't depend on having every tool span loaded in the tree
// (shallow mode ships root spans only). `route` supplies the session id; call
// `fetchToolRollup` on mount and on every reload.
export function useToolRollup(route) {
  const toolRollupData = ref(null)

  async function fetchToolRollup() {
    try {
      toolRollupData.value = await api.get(
        `/sessions/${route.params.id}/tool-rollup`
      )
    } catch (e) {
      toolRollupData.value = null
    }
  }

  return { toolRollupData, fetchToolRollup }
}
