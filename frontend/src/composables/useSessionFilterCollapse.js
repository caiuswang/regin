import { ref, computed, watch } from 'vue'

const FILTERS_OPEN_KEY = 'regin_sessions_filters_open'

// Collapse state for the Sessions facet row plus a live count of how many
// facets are narrowing the list. Extracted from SessionsView so that view
// stays under the vue-complexity surface-area threshold.
export function useSessionFilterCollapse(facets) {
  // Seeded collapsed on phones (where rows are the priority) and open on
  // wider screens, then remembered per-user. Hiding the pills never changes
  // the active query — the values live in `facets`' own refs.
  const filtersOpen = ref(
    localStorage.getItem(FILTERS_OPEN_KEY) != null
      ? localStorage.getItem(FILTERS_OPEN_KEY) === '1'
      : !window.matchMedia('(max-width: 639px)').matches
  )
  watch(filtersOpen, (v) => localStorage.setItem(FILTERS_OPEN_KEY, v ? '1' : '0'))

  // Predicates mirror the `facet-pill--active` template bindings one-for-one
  // so the badge count matches exactly which pills would read as active.
  const activeFilterCount = computed(() => {
    let n = 0
    if (facets.range.value !== 'today') n++
    if (facets.kind.value !== 'real') n++
    if (facets.tagFilter.value !== '') n++
    if (facets.activeFilter.value !== 'all') n++
    if (facets.repoFilter.value !== 'all') n++
    if (facets.activeTraceId.value) n++
    return n
  })

  return { filtersOpen, activeFilterCount }
}
