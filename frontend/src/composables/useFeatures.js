import { reactive } from 'vue'
import api from '../api.js'

const features = reactive({
  experimental_conceal: false,
  experimental_dense_search: false,
})

let inflight = null

// JS string "false" is truthy. Settings persisted before the bool-aware
// POST coercion landed are still on disk as strings, so normalize here.
function coerce(v) {
  if (typeof v === 'boolean') return v
  if (typeof v === 'number') return v !== 0
  if (typeof v === 'string') return ['true', '1', 'yes', 'on'].includes(v.trim().toLowerCase())
  return Boolean(v)
}

function fetchFeatures() {
  return api.get('/settings')
    .then(rows => {
      if (!Array.isArray(rows)) return
      for (const r of rows) {
        if (r && r.key in features) features[r.key] = coerce(r.value)
      }
    })
    .catch(() => { /* keep defaults; nav/routes stay hidden */ })
}

function loadFeatures() {
  if (inflight) return inflight
  if (!api.getToken()) return Promise.resolve()
  inflight = fetchFeatures()
  return inflight
}

function refresh() {
  inflight = fetchFeatures()
  return inflight
}

export function useFeatures() {
  return { features, ready: loadFeatures(), refresh }
}
