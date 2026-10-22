import { ref } from 'vue'
import api from '../api'

// Module-scoped like `useProjects`: the shell's chip label and the picker's
// grouping read the same list, so they share one request.
const items = ref([])
let loaded = false
let inflight = null

async function fetchWorkspaces() {
  try {
    const data = await api.get('/workspaces')
    items.value = data.workspaces || []
    loaded = true
  } catch {
    // A failed read leaves the picker flat — every repo is still reachable.
    items.value = []
  } finally {
    inflight = null
  }
}

export function useWorkspaces() {
  function load() {
    inflight = inflight || fetchWorkspaces()
    return inflight
  }

  async function ensure() {
    if (loaded) return
    await load()
  }

  return { items, load, ensure }
}
