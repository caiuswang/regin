import { ref, shallowRef, computed } from 'vue'
import api from '../api'
import { followNewest } from '../utils/followNewest.js'

/**
 * usePage — consume an offset-limit paginated endpoint.
 *
 * Backend envelope expected (the Pipeline lists add `population`/`applied`):
 *   { items, pagination: { total, page, size, has_next, has_prev },
 *     population?, applied?, ...extras }
 *
 * `loaded` turns true after the first read settles, so a reload can keep the
 * rows on screen (dimmed via `loading`) instead of blanking them.
 *
 * Exposes `page`, `size`, and navigation helpers (`next`, `prev`, `goto`).
 * Any change to `page` or `size` triggers a re-fetch via `load()`.
 */
export function usePage({ path, buildQuery = () => ({}), size = 50 }) {
  const items = shallowRef([])
  const extras = ref({})
  const page = ref(0)
  const pageSize = ref(size)
  const total = ref(0)
  const loading = ref(false)
  const error = ref(null)
  const hasNext = ref(false)
  const hasPrev = ref(false)
  const population = ref(null)
  const applied = ref(null)
  const loaded = ref(false)
  // Which read the rows belong to: two reads can be in flight (a write's
  // reload and a control change), and the one that answers last must not win.
  let issued = 0
  const pageCount = computed(
    () => (pageSize.value > 0 ? Math.max(1, Math.ceil(total.value / pageSize.value)) : 1)
  )

  function buildQs() {
    const params = new URLSearchParams()
    const filters = buildQuery() || {}
    for (const [k, v] of Object.entries(filters)) {
      if (v === undefined || v === null || v === '') continue
      params.set(k, v)
    }
    params.set('page', String(page.value))
    params.set('size', String(pageSize.value))
    return params.toString()
  }

  const load = followNewest(loadOnce)
  async function loadOnce() {
    const mine = ++issued
    loading.value = true
    error.value = null
    try {
      const data = await api.get(`${path}?${buildQs()}`)
      if (mine !== issued) return
      const { items: rows = [], pagination = {}, population: pop,
              applied: app, ...rest } = data
      population.value = pop ?? null
      applied.value = app ?? null
      items.value = rows
      total.value = pagination.total ?? 0
      hasNext.value = !!pagination.has_next
      hasPrev.value = !!pagination.has_prev
      // Server may have clamped the page index (e.g. after a filter
      // change shrinks the result set). Trust the server value.
      if (pagination.page != null) page.value = pagination.page
      if (pagination.size != null) pageSize.value = pagination.size
      extras.value = rest.extras && typeof rest.extras === 'object' ? rest.extras : rest
    } catch (e) {
      if (mine !== issued) return
      error.value = e.message || String(e)
    } finally {
      if (mine === issued) {
        loading.value = false
        loaded.value = true
      }
    }
  }

  async function goto(n) {
    const target = Math.max(0, Math.min(pageCount.value - 1, n))
    if (target === page.value) return
    page.value = target
    await load()
  }
  async function next() { if (hasNext.value) await goto(page.value + 1) }
  async function prev() { if (hasPrev.value) await goto(page.value - 1) }
  async function setSize(s) {
    pageSize.value = s
    page.value = 0
    await load()
  }
  async function refresh() {
    page.value = 0
    await load()
  }

  return {
    items, extras, loading, error, loaded, population, applied,
    page, pageSize, total, pageCount, hasNext, hasPrev,
    load, goto, next, prev, setSize, refresh,
  }
}
