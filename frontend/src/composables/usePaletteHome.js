import { ref } from 'vue'
import api from '../api'
import { rowDoc, rowKey } from '../utils/searchDocs.js'

// Desk order is R1's. Needs me is the only group drawn when empty: its calm
// line is the answer to "is anything waiting on me", which an absent group
// would leave unsaid.
const DESK = [
  { key: 'needs_me', noun: 'waiting', tone: 'needs', aside: 'sessions first · oldest wait first',
    calm: 'Nothing is waiting on you. Active work is below.' },
  { key: 'active_now', noun: 'active', aside: 'running tasks fold into their session' },
  { key: 'active_features', noun: 'features' },
  { key: 'sessions', noun: 'sessions', aside: 'latest not shown above' },
]

function markHere(row, traceId) {
  const here = row.here || (row.kind === 'session' && !!traceId && row.id === traceId)
  return here ? { ...row, here: true } : row
}

const toEntry = row => ({ type: 'doc', key: rowKey(rowDoc(row)), doc: rowDoc(row), hits: 0 })

export function deskSections(data, traceId) {
  return DESK.map((spec) => {
    const group = data?.[spec.key]
    if (!group) return null
    const rows = (group.items || []).map(r => toEntry(markHere(r, traceId)))
    if (!rows.length && !spec.calm) return null
    const more = group.total > rows.length ? {
      type: 'more', key: `more:home:${spec.key}`, home: spec.key,
      total: group.total, label: `Show all ${group.total} ${spec.noun} →`,
    } : null
    return {
      key: spec.key, label: group.label, total: group.total, rows, more,
      tone: spec.tone || null, aside: rows.length ? spec.aside || '' : '',
      calm: rows.length ? '' : spec.calm,
    }
  }).filter(Boolean)
}

export function chainSection(data) {
  const rows = (data?.chain || []).map(r => toEntry(r))
  return { key: 'chain', label: "This session's chain", total: rows.length, rows, more: null }
}

// The empty query's desk (R1 Home), from `/api/palette/home`. Module state,
// like the palette's own: the palette and the shortcut layer share one
// instance. `session` names the question — '' for the Everywhere desk, a
// trace id for that session's chain — so an answer to the other one is
// dropped by comparing it, and `epoch` drops an older asking of the same.
const home = ref({ session: null, status: 'idle', data: null })
let epoch = 0

export function usePaletteHome() {
  async function load(session) {
    epoch += 1
    const asked = epoch
    home.value = { session, status: 'pending', data: null }
    const params = session ? `?session=${encodeURIComponent(session)}` : ''
    try {
      const data = await api.get(`/palette/home${params}`)
      if (asked === epoch) home.value = { session, status: 'ok', data }
    } catch {
      if (asked === epoch) home.value = { session, status: 'failed', data: null }
    }
  }

  function reset() {
    epoch += 1
    home.value = { session: null, status: 'idle', data: null }
  }

  return { home, load, reset }
}
