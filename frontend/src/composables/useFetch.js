import { ref, shallowRef } from 'vue'
import api from '../api'

/**
 * useFetch — one-shot async fetch with loading/error state.
 *
 * For views that hit an endpoint once on mount (e.g. PatternDetailView,
 * ExperimentDetailView). Use `usePage` or `useCursor` instead when the
 * endpoint is paginated.
 *
 * @param {Object} options
 * @param {string|() => string} options.path — endpoint, or a thunk
 *   returning the endpoint when it depends on route params / reactive
 *   state read at fetch time.
 * @param {boolean} [options.immediate=true] — call `load()` automatically
 *   on first access. Set false when you want to gate the fetch on a
 *   user action.
 * @param {(raw: any) => any} [options.transform] — optional response
 *   transform (e.g. flatten an envelope, normalize timestamps).
 *
 * Returns: `{ data, loading, error, load, refresh }`.
 *   - `data` — shallowRef holding the most recent successful response
 *     (or `null` until the first load completes).
 *   - `loading` — true while a request is in flight.
 *   - `error` — last error message, or null on success.
 *   - `load()` — fetch and replace `data`. Resolves once the request
 *     finishes (success or failure).
 *   - `refresh()` — alias for `load()`; kept separate so callers can
 *     wire it to a refresh button without confusing it with the initial
 *     mount load.
 */
export function useFetch({ path, immediate = true, transform = null } = {}) {
  const data = shallowRef(null)
  const loading = ref(false)
  const error = ref(null)

  function resolvePath() {
    return typeof path === 'function' ? path() : path
  }

  async function load() {
    loading.value = true
    error.value = null
    try {
      const raw = await api.get(resolvePath())
      data.value = transform ? transform(raw) : raw
    } catch (e) {
      error.value = e?.message || String(e)
    } finally {
      loading.value = false
    }
  }

  if (immediate) {
    load()
  }

  return {
    data, loading, error,
    load, refresh: load,
  }
}
