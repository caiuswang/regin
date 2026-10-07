// The `@` menu's preview pane: what the highlighted row says, read through
// `GET /api/refs/preview` (lib/refs/preview.py).
//
// One request per row the highlight settles on, not per row it passes: holding
// ↓ through twenty rows asks once, for the row it stops at. Answers are kept
// for the life of the open menu, so moving back over a row asks nothing, and
// dropped when it closes, so a reopened menu never shows a body since edited.
import { shallowRef, toValue, watch } from 'vue'
import api from '../api'

const SETTLE_MS = 120

/**
 * `target` is a getter of `{ ref, project }` or null. `state` is null with no
 * target, else `{ ref, loading, failed, data }`.
 */
export function useRefPreview(target) {
  const state = shallowRef(null)
  const answers = new Map()
  let timer = null
  let seq = 0

  const cacheKey = (t) => `${t.ref}|${t.project ?? ''}`

  async function fetchFor(t) {
    const mine = ++seq
    const params = new URLSearchParams({ ref: t.ref })
    if (t.project !== null && t.project !== undefined) params.set('project_id', String(t.project))
    let next
    try {
      next = { ref: t.ref, loading: false, failed: false, data: await api.get(`/refs/preview?${params}`) }
      answers.set(cacheKey(t), next)
    } catch {
      next = { ref: t.ref, loading: false, failed: true, data: null }
    }
    if (mine === seq) state.value = next
  }

  function show(t) {
    if (timer) {
      clearTimeout(timer)
      timer = null
    }
    seq++
    if (!t) {
      state.value = null
      return
    }
    const known = answers.get(cacheKey(t))
    if (known) {
      state.value = known
      return
    }
    state.value = { ref: t.ref, loading: true, failed: false, data: null }
    timer = setTimeout(() => {
      timer = null
      fetchFor(t)
    }, SETTLE_MS)
  }

  function reset() {
    show(null)
    answers.clear()
  }

  watch(() => {
    const t = toValue(target)
    return t ? cacheKey(t) : ''
  }, () => show(toValue(target)))

  return { state, reset }
}
