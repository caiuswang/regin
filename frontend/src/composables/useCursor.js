import { ref, shallowRef, computed } from 'vue'
import api from '../api'

/**
 * useCursor — consume a keyset-paginated endpoint.
 *
 * Accepts a `path` and a `buildQuery()` that returns the current filter
 * query params as a plain object. Fetch is driven via `load()` (reset)
 * or `loadMore()` (append using the server-supplied cursor token).
 *
 * The backend envelope expected is:
 *   { items: [...], pagination: { next_cursor, size, has_next, ... }, ...extras }
 * Any keys outside `items` / `pagination` are exposed as `extras` — so
 * endpoints can still return sidecar data (stats, sessions summary) on
 * the first-page fetch without forcing a separate round trip.
 */
export function useCursor({ path, buildQuery = () => ({}), size = 100 }) {
  const items = shallowRef([])
  const extras = ref({})
  const nextCursor = ref(null)
  const loading = ref(false)
  const loadingMore = ref(false)
  const error = ref(null)
  const hasNext = computed(() => nextCursor.value != null)

  function buildQs(cursor) {
    const params = new URLSearchParams()
    const filters = buildQuery() || {}
    for (const [k, v] of Object.entries(filters)) {
      if (v === undefined || v === null || v === '') continue
      params.set(k, v)
    }
    params.set('size', String(size))
    if (cursor) params.set('cursor', cursor)
    return params.toString()
  }

  async function load() {
    loading.value = true
    error.value = null
    try {
      const qs = buildQs(null)
      const data = await api.get(`${path}?${qs}`)
      const { items: rows = [], pagination = {}, ...rest } = data
      items.value = rows
      nextCursor.value = pagination.next_cursor || null
      extras.value = rest
    } catch (e) {
      error.value = e.message || String(e)
    } finally {
      loading.value = false
    }
  }

  async function loadMore() {
    if (!nextCursor.value || loadingMore.value) return
    loadingMore.value = true
    error.value = null
    try {
      const qs = buildQs(nextCursor.value)
      const data = await api.get(`${path}?${qs}`)
      const { items: rows = [], pagination = {} } = data
      // Append; keep existing extras (stats/sessions) untouched since
      // they describe the whole filtered set, not this page.
      items.value = items.value.concat(rows)
      nextCursor.value = pagination.next_cursor || null
    } catch (e) {
      error.value = e.message || String(e)
    } finally {
      loadingMore.value = false
    }
  }

  async function refresh() {
    // Alias for load() but kept separate so callers can log/flash
    // differently on a manual refresh vs. the initial mount fetch.
    return load()
  }

  return {
    items, extras, loading, loadingMore, error,
    hasNext, nextCursor,
    load, loadMore, refresh,
  }
}
