import { ref } from 'vue'
import api from '../api'
import { DRILL_PAGE } from '../utils/searchDocs.js'

// One kind, every match, paged by `offset` (R1 Drill). Module state, like the
// palette's own: the palette and the shortcut layer share one instance.
//
// `id` names the question — kind, query, scope and open session — so a page
// answering an older question is dropped by comparing ids, and a page that
// lands twice is de-duplicated by row key rather than appended twice.
const drill = ref({ id: '', items: [], total: 0, status: 'idle' })
// Bumped by every reset. The id alone cannot tell two askings of one question
// apart: A → B → A leaves A's first page in flight beside the new one, and
// accepting both would de-dupe the second to nothing and end the list early.
let epoch = 0

function paramsFor(id, offset) {
  const [kind, q, scope, session] = JSON.parse(id)
  const params = new URLSearchParams({
    q, kinds: kind, scope, limit: String(DRILL_PAGE), offset: String(offset),
  })
  if (session) params.set('session', session)
  return { kind, params }
}

export function usePaletteDrill(toRow) {
  function reset(id) {
    epoch += 1
    drill.value = { id, items: [], total: 0, status: id ? 'pending' : 'idle' }
  }

  // `onPage` sees the rows the page added in the same tick they land: a
  // caller moving the cursor onto them after an `await` would be too late,
  // because the list's own re-pin runs first and the cursor is already gone.
  async function loadPage(onPage) {
    const start = drill.value
    if (!start.id || start.status === 'loading') return []
    const asked = epoch
    const { kind, params } = paramsFor(start.id, start.items.length)
    drill.value = { ...start, status: 'loading' }
    try {
      const data = await api.get(`/search?${params}`)
      if (asked !== epoch) return []
      const group = (data.groups || []).find(g => g.kind === kind)
      if (!group || (data.failed || []).includes(kind)) throw new Error('page failed')
      const seen = new Set(drill.value.items.map(r => r.key))
      const fresh = (group.items || []).map(toRow).filter(r => !seen.has(r.key))
      const items = [...drill.value.items, ...fresh]
      // An empty page short of `total` means the set shrank under the reader;
      // the list ends where the rows did instead of offering a page forever.
      const total = fresh.length ? group.total : items.length
      drill.value = { id: start.id, items, total, status: 'ok' }
      onPage?.(fresh)
      return fresh
    } catch {
      if (asked === epoch) drill.value = { ...drill.value, status: 'failed' }
      return []
    }
  }

  return { drill, reset, loadPage }
}
