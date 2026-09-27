import { ref } from 'vue'
import api from '../api'

// The grouped reads (next step, board) own the status axis and never show
// archived, so those two parameters are not sent: a status filter there would
// be a second, contradicting answer to "which column is this task in".
const GROUPED_DROPS = ['status', 'sort', 'include_archived']

export function groupedQuery(params, extra = {}) {
  const out = new URLSearchParams()
  for (const [key, value] of Object.entries({ ...params, ...extra })) {
    if (GROUPED_DROPS.includes(key)) continue
    if (value !== '' && value != null) out.set(key, String(value))
  }
  return out.toString()
}

// "Show more" asks for the page that follows what is loaded. A preview shorter
// than a page (Ideas opens on three) is re-read from page 0, replacing itself.
function nextWindow(rows, size) {
  const page = Math.floor(rows.length / size)
  return { page, keep: rows.slice(0, page * size) }
}

/**
 * Each request is numbered, and an answer that is no longer the latest is
 * dropped: a filter changed while it was in flight, and its rows describe a
 * predicate nobody is looking at any more.
 */
function useLatest() {
  let issued = 0
  return {
    next: () => ++issued,
    current: () => issued,
    stale: mine => mine !== issued,
  }
}

/**
 * A "show more" is one request per key at a time, and its rows are dropped if
 * a reload landed while it was in flight — they would be the old filter's
 * rows appended under the new one. A second click on the same key while the
 * first is pending would append the same page twice.
 */
function usePager(latest) {
  const pending = new Set()
  return async (key, read, apply) => {
    if (pending.has(key)) return
    pending.add(key)
    const mine = latest.current()
    try {
      const fresh = await read()
      if (!latest.stale(mine)) apply(fresh)
    } finally {
      pending.delete(key)
    }
  }
}

/** `GET /api/tasks?group=next_step` — Ready / Running / Blocked / Ideas. */
export function useNextStep(params, size = 50) {
  const body = ref(null)
  const error = ref('')
  const loading = ref(false)
  const latest = useLatest()

  async function load() {
    const mine = latest.next()
    loading.value = true
    try {
      const data = await api.get(
        `/tasks?${groupedQuery(params.value, { group: 'next_step', size })}`)
      if (latest.stale(mine)) return
      body.value = data
      error.value = ''
    } catch (e) {
      if (!latest.stale(mine)) error.value = e?.message || String(e)
    } finally {
      if (!latest.stale(mine)) loading.value = false
    }
  }

  const pager = usePager(latest)

  function more(key) {
    const group = body.value?.groups.find(g => g.key === key)
    if (!group) return Promise.resolve()
    const { page, keep } = nextWindow(group.rows, size)
    return pager(key,
      async () => (await api.get(`/tasks?${groupedQuery(params.value,
        { group: 'next_step', only: key, page, size })}`)).groups[0],
      (fresh) => {
        group.rows = [...keep, ...fresh.rows]
        group.more = Math.max(0, fresh.n - group.rows.length)
      })
  }

  /** Every Ready task id, across pages — "Run all N" means all N. */
  async function readyIds() {
    const ids = []
    for (let page = 0; ; page++) {
      const data = await api.get(`/tasks?${groupedQuery(params.value,
        { group: 'next_step', only: 'ready', page, size: 200 })}`)
      const rows = data.groups[0]?.rows || []
      ids.push(...rows.map(r => r.id))
      if (rows.length < 200) return ids
    }
  }

  return { body, error, loading, load, more, readyIds }
}

/** `GET /api/tasks/board` — five status columns, each paged on its own. */
export function useTaskBoard(params, size = 20) {
  const body = ref(null)
  const failed = ref(null)
  const error = ref('')
  const loading = ref(false)
  const latest = useLatest()

  async function load() {
    const mine = latest.next()
    loading.value = true
    try {
      const data = await api.get(`/tasks/board?${groupedQuery(params.value, { size })}`)
      if (latest.stale(mine)) return
      body.value = data
      failed.value = null
      error.value = ''
    } catch (e) {
      if (!latest.stale(mine)) error.value = e?.message || String(e)
    } finally {
      if (!latest.stale(mine)) loading.value = false
    }
  }

  async function page(status, have) {
    const { page: next } = nextWindow(have, size)
    const data = await api.get(`/tasks/board?${groupedQuery(params.value,
      { column: status, page: next, size })}`)
    return data.columns[0]
  }

  const pager = usePager(latest)

  function more(status) {
    const column = body.value?.columns.find(c => c.status === status)
    if (!column) return Promise.resolve()
    const have = column.cards
    return pager(status, () => page(status, have), (fresh) => {
      column.cards = [...have, ...fresh.cards]
      column.more = fresh.more
    })
  }

  function showFailed() {
    const have = failed.value?.cards || []
    return pager('failed', () => page('failed', have), (fresh) => {
      failed.value = { cards: [...have, ...fresh.cards], more: fresh.more }
    })
  }

  return { body, failed, error, loading, load, more, showFailed }
}

/** `GET /api/tasks/graph?lanes=1` — the DAG as feature lanes. */
export function useLaneGraph(scope, cap = 150) {
  const body = ref(null)
  const error = ref('')
  const loading = ref(false)
  const latest = useLatest()

  async function load() {
    const mine = latest.next()
    loading.value = true
    const { project, open } = scope.value
    const qs = new URLSearchParams({ lanes: '1', open: open ? '1' : '0', cap: String(cap) })
    if (project) qs.set('project_id', project)
    try {
      const data = await api.get(`/tasks/graph?${qs}`)
      if (latest.stale(mine)) return
      body.value = data
      error.value = ''
    } catch (e) {
      if (!latest.stale(mine)) error.value = e?.message || String(e)
    } finally {
      if (!latest.stale(mine)) loading.value = false
    }
  }

  return { body, error, loading, load }
}
