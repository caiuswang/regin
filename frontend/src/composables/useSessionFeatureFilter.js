import { ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import api from '../api'

// The Feature axis of the sessions list. Unlike the other facets it lives in
// the URL (`?feature_id=`), not localStorage: a feature page links here to
// mean "this feature's sessions", and that address must survive a reload and
// a shared link without leaking into the next visit.
export function useSessionFeatureFilter() {
  const route = useRoute()
  const router = useRouter()
  const featureFilter = ref(String(route.query.feature_id || ''))
  const featureRow = ref(null)
  let asked = 0

  async function resolve(id) {
    const mine = ++asked
    if (!id) { featureRow.value = null; return }
    if (featureRow.value && String(featureRow.value.id) === id) return
    try {
      const row = await api.get(`/features/${id}`)
      if (mine === asked) featureRow.value = { id: row.id, title: row.title }
    } catch {
      if (mine === asked) featureRow.value = { id: Number(id), title: '' }
    }
  }

  function pickedFeature(row) {
    featureRow.value = row ? { id: row.id, title: row.title } : null
  }

  watch(() => route.query.feature_id, (v) => {
    const id = String(v || '')
    if (id !== featureFilter.value) featureFilter.value = id
  })
  watch(featureFilter, (id) => {
    resolve(id)
    if (String(route.query.feature_id || '') === id) return
    const next = { ...route.query }
    if (id) next.feature_id = id
    else delete next.feature_id
    router.replace({ query: next })
  }, { immediate: true })

  return { featureFilter, featureRow, pickedFeature }
}
