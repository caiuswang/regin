import { ref } from 'vue'
import api from '../api'

/**
 * useTaskDeps — replace one task's blocker set.
 *
 * The refusal is kept structured: a self-edge or a cycle comes back as a 400
 * naming the offending `pair`, and showing the operator which two tasks close
 * the loop is the difference between a fixable message and "invalid".
 */
export function useTaskDeps() {
  const saving = ref(false)
  const depError = ref(null)

  async function save(taskId, ids) {
    saving.value = true
    const result = await api.put(`/tasks/${taskId}/deps`, { depends_on: ids })
    saving.value = false
    if (result.ok === false) {
      depError.value = {
        message: result.msg || 'The server refused these dependencies.',
        pair: result.body?.pair || [],
      }
      return false
    }
    depError.value = null
    return true
  }

  return { saving, depError, save }
}
