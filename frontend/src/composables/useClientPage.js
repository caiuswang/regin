import { ref, computed, watch, unref } from 'vue'

/**
 * useClientPage — client-side search + pagination over an in-memory array.
 *
 * For endpoints that return a bounded set (the memory topic/exemplar APIs cap
 * at ~200 rows and support neither an offset nor a `q` filter). Filters by a
 * free-text query against a per-item searchable string, then slices into
 * fixed-size pages. Resets to page 0 whenever the query changes, and clamps the
 * page in range whenever the source shrinks (e.g. after a reload).
 *
 *   const { paged, query, page, ... } = useClientPage(rows, {
 *     searchText: (r) => `${r.title} ${r.id}`, size: 25,
 *   })
 *
 * Pair `paged` with a v-for and the navigation helpers with <PageControls>.
 */
export function useClientPage(source, { searchText = () => '', size = 25 } = {}) {
  const query = ref('')
  const page = ref(0)
  const pageSize = ref(size)

  const rawCount = computed(() => (unref(source) || []).length)

  const filtered = computed(() => {
    const rows = unref(source) || []
    const q = query.value.trim().toLowerCase()
    if (!q) return rows
    return rows.filter((it) => String(searchText(it) || '').toLowerCase().includes(q))
  })

  const total = computed(() => filtered.value.length)
  const pageCount = computed(
    () => (pageSize.value > 0 ? Math.max(1, Math.ceil(total.value / pageSize.value)) : 1)
  )
  const paged = computed(() => {
    const start = page.value * pageSize.value
    return filtered.value.slice(start, start + pageSize.value)
  })
  const hasPrev = computed(() => page.value > 0)
  const hasNext = computed(() => page.value < pageCount.value - 1)

  // A shrinking result set (filter applied, or rows removed on reload) can leave
  // `page` past the last page — pull it back into range.
  watch([total, pageSize], () => {
    if (page.value > pageCount.value - 1) page.value = pageCount.value - 1
  })
  watch(query, () => { page.value = 0 })

  function goto(n) { page.value = Math.max(0, Math.min(pageCount.value - 1, n)) }
  function next() { if (hasNext.value) page.value += 1 }
  function prev() { if (hasPrev.value) page.value -= 1 }
  function setSize(s) { pageSize.value = s; page.value = 0 }

  return {
    query, page, pageSize, rawCount, total, pageCount, paged, filtered,
    hasNext, hasPrev, goto, next, prev, setSize,
  }
}
