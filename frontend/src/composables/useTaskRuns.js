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
  // The schedule's decisions: a started one names its run, a skipped or
  // refused one is a row of its own in the history.
  const fires = ref([])
  const loading = ref(true)
  const error = ref('')

  async function load(taskId) {
    loading.value = true
    error.value = ''
    try {
      const [data, fired] = await Promise.all([
        api.get(`/tasks/${taskId}/runs`),
        api.get(`/tasks/${taskId}/schedule/fires`)])
      runs.value = data.runs || []
      fires.value = fired || []
    } catch (e) {
      error.value = e?.message || 'Failed to read this task’s attempts.'
    } finally {
      loading.value = false
    }
  }

  return { runs, fires, loading, error, load }
}
