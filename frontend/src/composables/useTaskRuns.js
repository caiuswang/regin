import { ref } from 'vue'
import api from '../api'

/**
 * useTaskRuns — a task's attempt history from `/api/tasks/<id>/runs`.
 *
 * Each row carries the server's merged `live` flag alongside its own `status`,
 * and the view renders them as two separate facts: a settled-looking `running`
 * row with nothing behind it is the normal aftermath of a server restart, not
 * an agent still working.
 */
export function useTaskRuns() {
  const runs = ref([])
  const loading = ref(true)
  const error = ref('')

  async function load(taskId) {
    loading.value = true
    error.value = ''
    try {
      const data = await api.get(`/tasks/${taskId}/runs`)
      runs.value = data.runs || []
    } catch (e) {
      error.value = e?.message || 'Failed to read this task’s attempts.'
    } finally {
      loading.value = false
    }
  }

  return { runs, loading, error, load }
}
