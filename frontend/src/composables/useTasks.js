import { computed, ref, shallowRef } from 'vue'
import api from '../api'
import { useFlash } from './useFlash.js'
import { STATUS_FILTERS } from '../utils/taskMeta.js'

export const TITLE_MAX = 200

export { STATUS_FILTERS }

export function emptyDraft() {
  return { title: '', body: '', tags: '', priority: 'none', project_id: '',
           feature_id: '', kind: 'task' }
}

function parseTags(raw) {
  return String(raw || '')
    .split(',')
    .map(t => t.trim())
    .filter(Boolean)
    .slice(0, 10)
}

export function draftError(draft) {
  const title = (draft.title || '').trim()
  if (!title) return 'A title is required.'
  if (title.length > TITLE_MAX) {
    return `Title must be ${TITLE_MAX} characters or fewer — this one is ${title.length}.`
  }
  return ''
}

export function draftBody(draft) {
  const body = { title: draft.title.trim() }
  const text = (draft.body || '').trim()
  if (text) body.body = text
  const tags = parseTags(draft.tags)
  if (tags.length) body.tags = tags
  if (draft.priority && draft.priority !== 'none') body.priority = draft.priority
  if (draft.project_id) body.project_id = Number(draft.project_id)
  // Captured already placed under its feature, rather than created and then
  // moved. The capture route accepts it, and it is one column either way.
  if (draft.feature_id) body.feature_id = Number(draft.feature_id)
  // A stage names which step of a feature this is; with no feature it names
  // nothing, so it is never sent alone.
  if (draft.feature_id && draft.kind && draft.kind !== 'task') body.kind = draft.kind
  return body
}

/**
 * useTaskFeed — one group's slice of the task list, paged by offset.
 *
 * A grouped list mounts one feed per group rather than loading everything and
 * partitioning it in the browser: a collapsed group costs nothing, and an open
 * one pages independently. "Load more" appends `page + 1` of the same offset
 * scheme the flat list pages with, so there is one paging contract.
 */
export function useTaskFeed(params, scope = () => ({}), size = 50) {
  const items = shallowRef([])
  const loading = ref(false)
  const loadingMore = ref(false)
  const error = ref(null)
  const page = ref(0)
  const total = ref(0)
  const hasNext = computed(() => items.value.length < total.value)
  // A generation per load. `loadMore` reads the generation it extends and
  // never bumps it, so it can neither cancel a load in flight nor append onto
  // a list a newer load replaced.
  let generation = 0

  function qs(index) {
    const filters = { ...params.value, ...scope() }
    const out = new URLSearchParams(
      Object.entries(filters).filter(([, v]) => v !== '' && v != null))
    out.set('page', String(index))
    out.set('size', String(size))
    return out.toString()
  }

  async function load() {
    const mine = ++generation
    loading.value = true
    error.value = null
    try {
      const data = await api.get(`/tasks?${qs(0)}`)
      if (mine !== generation) return
      items.value = data.items || []
      total.value = data.pagination?.total ?? items.value.length
      page.value = 0
    } catch (e) {
      if (mine === generation) error.value = e?.message || String(e)
    } finally {
      if (mine === generation) loading.value = false
    }
  }

  async function loadMore() {
    if (!hasNext.value || loadingMore.value || loading.value) return
    const extends_ = generation
    loadingMore.value = true
    try {
      const data = await api.get(`/tasks?${qs(page.value + 1)}`)
      if (extends_ !== generation) return
      items.value = [...items.value, ...(data.items || [])]
      total.value = data.pagination?.total ?? total.value
      page.value += 1
    } catch (e) {
      if (extends_ === generation) error.value = e?.message || String(e)
    } finally {
      loadingMore.value = false
    }
  }

  return { items, loading, loadingMore, error, hasNext, total, load, loadMore }
}

/**
 * useTaskCounts — the group headers' totals, from the server.
 *
 * Every axis is blind to its own filter, so a header reports how many tasks it
 * WOULD show if you clicked it.
 */
export function useTaskCounts(params) {
  const counts = ref(null)
  const error = ref('')
  const loading = ref(false)

  let issued = 0

  async function load() {
    const mine = ++issued
    loading.value = true
    try {
      const qs = new URLSearchParams(
        Object.entries(params.value).filter(([, v]) => v !== '' && v != null))
      const body = await api.get(`/tasks/counts?${qs}`)
      if (mine !== issued) return
      counts.value = body
      error.value = ''
    } catch (e) {
      if (mine !== issued) return
      // The counts are what build the group headers, so nulling them silently
      // rendered grouped mode as an empty page that claimed nothing was wrong.
      // The last good counts stay, and the failure becomes a value the list
      // can state and fall back from.
      error.value = e?.message || String(e)
    } finally {
      loading.value = false
    }
  }

  return { counts, error, loading, load }
}

/** The mutations the list offers, each returning whether it landed. */
export function useTaskMutations() {
  const { flash } = useFlash()
  const saving = ref(false)
  const formError = ref('')

  async function create(draft) {
    formError.value = draftError(draft)
    if (formError.value) return null
    saving.value = true
    const result = await api.post('/tasks', draftBody(draft))
    saving.value = false
    if (result.ok === false) {
      formError.value = result.msg || 'The server refused this idea.'
      return null
    }
    formError.value = ''
    return result.task
  }

  async function patch(task, fields) {
    const result = await api.patch(`/tasks/${task.id}`, fields)
    if (result.ok === false) {
      flash(result.msg || 'Could not update this task', 'error')
      return false
    }
    return true
  }

  /**
   * One edit across many tasks. Partial success is the normal outcome, so the
   * count that is flashed is what the server changed, never what was selected.
   */
  async function bulk(ids, fields) {
    const result = await api.post('/tasks/bulk', { ids, ...fields })
    if (result.ok === false) {
      flash(result.msg || 'Could not update these tasks', 'error')
      return null
    }
    const changed = result.changed?.length || 0
    const rejected = result.rejected?.length || 0
    flash(rejected
      ? `${changed} updated, ${rejected} could not be`
      : `${changed} updated`, rejected ? 'error' : 'success')
    return result
  }

  /**
   * Remove a task for good. Distinct from archiving, which is a PATCH the
   * same surface can undo — so the caller confirms first and this only
   * reports what the server did.
   */
  async function remove(task) {
    const result = await api.del(`/tasks/${task.id}`)
    if (result.ok === false) {
      flash(result.msg || 'Could not delete this task', 'error')
      return false
    }
    flash('Task deleted', 'success')
    return true
  }

  /** Many at once. Partial success is normal — a live task is refused. */
  async function bulkRemove(ids) {
    const result = await api.del('/tasks/bulk', { ids })
    if (result.ok === false) {
      flash(result.msg || 'Could not delete these tasks', 'error')
      return null
    }
    const deleted = result.deleted?.length || 0
    const rejected = result.rejected?.length || 0
    flash(rejected
      ? `${deleted} deleted, ${rejected} could not be`
      : `${deleted} deleted`, rejected ? 'error' : 'success')
    return result
  }

  return { saving, formError, create, patch, bulk, remove, bulkRemove }
}
