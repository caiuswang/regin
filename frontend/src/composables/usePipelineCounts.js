import { ref } from 'vue'
import api from '../api'

/**
 * usePipelineCounts — all three tab counts, from one request.
 *
 * The failure mode this exists to close: a counts call that fails must cost
 * the page its *numbers*, never its rows. So the error is a value the shell
 * renders, and the last good counts are kept rather than nulled — a tab strip
 * that silently empties is indistinguishable from three empty ledgers.
 */
export function usePipelineCounts() {
  const counts = ref(null)
  const error = ref('')
  const loading = ref(false)

  async function load(scope) {
    loading.value = true
    try {
      const qs = scope && scope !== 'all' ? `?project=${encodeURIComponent(scope)}` : ''
      const data = await api.get(`/pipeline/counts${qs}`)
      counts.value = data?.counts || null
      error.value = ''
    } catch (e) {
      error.value = e?.message || String(e)
    } finally {
      loading.value = false
    }
  }

  return { counts, error, loading, load }
}
