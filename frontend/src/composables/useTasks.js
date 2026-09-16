import { ref } from 'vue'
import api from '../api'
import { useCursor } from './useCursor.js'
import { useFlash } from './useFlash.js'
import { STATUS_FILTERS } from '../utils/taskMeta.js'

export const TITLE_MAX = 200

export { STATUS_FILTERS }

export function emptyDraft() {
  return { title: '', body: '', tags: '', priority: 'none', project_id: '',
           feature_id: '' }
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
  return body
}

/**
 * useTaskFeed — one keyset-paginated slice of the task list.
 *
 * `scope` is a getter for the extra params that pin this feed to one group
 * (`{ status: 'idea' }`). A grouped list mounts one feed per group rather than
 * loading everything and partitioning it in the browser: the group that is
 * collapsed costs nothing, and the one that is open pages independently.
 */
export function useTaskFeed(params, scope = () => ({}), size = 50) {
  return useCursor({
    path: '/tasks',
    buildQuery: () => ({ ...params.value, ...scope() }),
    size,
  })
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

  async function load() {
    loading.value = true
    try {
      const qs = new URLSearchParams(
        Object.entries(params.value).filter(([, v]) => v !== '' && v != null))
      counts.value = await api.get(`/tasks/counts?${qs}`)
      error.value = ''
    } catch (e) {
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
