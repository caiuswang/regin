// Who the grain would accept on the other side of one parentage.
//
// The list is READ, never computed: the rule that decides who may be a parent
// is the same rule that refuses the write, and a copy of it in the browser is
// a copy that drifts. Nothing here filters `items` — a narrowing term goes to
// the server as `q`, so the way in and the legality stay two different things.
import { ref } from 'vue'
import api from '../api'

export function useGrainCandidates() {
  const items = ref([])
  const loading = ref(false)
  const error = ref('')

  /**
   * `child` asks who may be that task's parent — 0 for a task the composer has
   * not captured yet. `parent` asks who may become a run of that task.
   */
  async function load({ child = null, parent = null, q = '' } = {}) {
    loading.value = true
    error.value = ''
    const side = child !== null ? `child=${child}` : `parent=${parent}`
    try {
      const data = await api.get(
        `/tasks/grain-candidates?${side}&q=${encodeURIComponent(q)}`)
      items.value = data.items || []
    } catch (e) {
      error.value = e.message || String(e)
      items.value = []
    } finally {
      loading.value = false
    }
  }

  return { items, loading, error, load }
}

/** `#12 wire the picker` — a candidate as one scannable line. */
export function candidateLabel(task) {
  return `#${task.id} ${task.title}`
}
