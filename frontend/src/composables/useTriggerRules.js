import { ref, shallowRef, computed, watch, onMounted } from 'vue'
import api from '../api'

/**
 * useTriggerRules — drive the Option-B per-rule triggers list.
 *
 * Filter state is mirrored into the route's query string so refresh and
 * back-button survive: `?range=7d&status=noisy&sort=rate&search=entity`.
 *
 * Unlike the keyset endpoints, /api/triggers/rules returns the full set
 * in one shot (bounded — usually well under 100 rules) so no cursor.
 */
export function useTriggerRules(route, router) {
  const kpis = ref({ configured: 0, active: 0, noisy: 0, dead: 0 })
  const rules = shallowRef([])
  const thresholds = ref(null)
  const loading = ref(false)
  const error = ref(null)

  const filters = computed(() => ({
    range:    route.query.range    || '7d',
    status:   route.query.status   || 'all',
    sort:     route.query.sort     || 'rate',
    search:   route.query.search   || '',
    severity: route.query.severity || '',
    engine:   route.query.engine   || '',
    marks:    route.query.marks    || '',
  }))

  async function load() {
    loading.value = true
    error.value = null
    try {
      const f = filters.value
      const qs = new URLSearchParams()
      for (const [k, v] of Object.entries(f)) {
        if (v) qs.set(k, v)
      }
      const data = await api.get(`/triggers/rules?${qs.toString()}`)
      kpis.value = data.kpis || { configured: 0, active: 0, noisy: 0, dead: 0 }
      rules.value = data.rules || []
      thresholds.value = data.thresholds || null
    } catch (e) {
      error.value = e?.message || String(e)
    } finally {
      loading.value = false
    }
  }

  function setFilter(key, value) {
    const q = { ...route.query }
    if (value === null || value === '' || value === undefined) {
      delete q[key]
    } else {
      q[key] = value
    }
    router.push({ query: q })
  }

  function clearFilters() {
    router.push({ query: {} })
  }

  onMounted(load)
  watch(() => route.query, load, { deep: true })

  return {
    kpis, rules, thresholds, loading, error,
    filters,
    load, setFilter, clearFilters,
  }
}
