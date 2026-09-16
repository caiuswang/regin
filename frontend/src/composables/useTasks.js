import { computed, ref, watch } from 'vue'
import api from '../api'
import { useCursor } from './useCursor.js'
import { useFilterState } from './useFilterState.js'
import { useFlash } from './useFlash.js'
import { GROUPS, SORTS, STATUS_FILTERS } from '../utils/taskMeta.js'

export const TITLE_MAX = 200

export { STATUS_FILTERS }

export function emptyDraft() {
  return { title: '', body: '', tags: '', priority: 'none', project_id: '',
           parent_id: '' }
}

function parseTags(raw) {
  return String(raw || '')
    .split(',')
    .map(t => t.trim())
    .filter(Boolean)
    .slice(0, 10)
}

// Fills a draft from a resolved issue, and reports back what it wrote so the
// next call can tell its own text apart from the operator's. A field still
// holding exactly what the tracker last put there is the tracker's, so a
// second issue replaces it; a field the operator touched outranks the tracker
// and stands. Without the `filled` baseline the first issue's text is
// indistinguishable from typed words, and every later ref reads as stale.
export function fillFromIssue(draft, issue, filled) {
  // Only a field this call actually wrote advances the baseline. Claiming one
  // it skipped would hand the operator's own words back as the tracker's, and
  // the next ref would overwrite them.
  const next = { ...filled }
  const title = (issue.title || '').slice(0, TITLE_MAX)
  const body = issue.description || ''
  if (!draft.title.trim() || draft.title === filled.title) {
    draft.title = title
    next.title = title
  }
  if (!draft.body.trim() || draft.body === filled.body) {
    draft.body = body
    next.body = body
  }
  return next
}

export function noneFilled() {
  return { title: '', body: '' }
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
  // A run captured already attached, rather than created and then moved. The
  // capture route already accepts it, and the grain refuses the same way.
  if (draft.parent_id) body.parent_id = Number(draft.parent_id)
  return body
}

// Long enough that a fast typist issues one request per word, short enough
// that a pause feels answered — the same trade `useFullTextSearch` makes for
// its remote leg.
const SEARCH_SETTLE_MS = 140

/**
 * useTaskFilters — every axis the feed can be narrowed on, as query params.
 *
 * The whole set goes to the server. Nothing here is ever applied to the loaded
 * rows: a filter evaluated over one page answers a different question than the
 * control claims to ask, and the counts beside it would then describe the page
 * instead of the backlog.
 *
 * The durable axes persist; `q` does not — returning to the page under a
 * search someone typed yesterday reads as an empty backlog.
 */
export function useTaskFilters(initial = {}) {
  const status = useFilterState('regin.tasks.status', '',
    v => STATUS_FILTERS.some(s => s.key === v))
  const sort = useFilterState('regin.tasks.sort', 'created_desc',
    v => SORTS.some(s => s.value === v))
  const groupBy = useFilterState('regin.tasks.group', 'status',
    v => GROUPS.some(g => g.value === v))
  const projectId = useFilterState('regin.tasks.project', '')
  const priority = useFilterState('regin.tasks.priority', '')
  const includeArchived = useFilterState('regin.tasks.archived', '')
  // Off by default: the backlog is features, and a run of one is reached by
  // expanding it. The server's own default is the opposite (every task), so
  // the narrowing is a parameter this surface sends, never a server change.
  const showRuns = useFilterState('regin.tasks.runs', '')
  const tag = ref('')
  const search = ref('')
  const q = ref('')

  // A link that names a filter wins over the persisted one — arriving from
  // "12 open" on a project card and landing in yesterday's saved view would
  // ignore the thing that was clicked.
  for (const [key, value] of Object.entries(initial)) {
    const target = { status, projectId, priority, sort, groupBy }[key]
    if (target && value != null && value !== '') target.value = String(value)
  }

  let settle = null
  watch(search, (value) => {
    clearTimeout(settle)
    settle = setTimeout(() => { q.value = value.trim() }, SEARCH_SETTLE_MS)
  })

  // Applied immediately, so Enter never waits out the settle it was meant to
  // pre-empt.
  function submitSearch() {
    clearTimeout(settle)
    q.value = search.value.trim()
  }

  const params = computed(() => ({
    status: status.value,
    sort: sort.value,
    q: q.value,
    tag: tag.value,
    priority: priority.value,
    project_id: projectId.value,
    include_archived: includeArchived.value,
    root_only: showRuns.value === '1' ? '' : '1',
  }))

  const active = computed(() => [
    status.value, q.value, tag.value, priority.value, projectId.value,
    includeArchived.value,
  ].filter(Boolean).length)

  function reset() {
    status.value = ''
    projectId.value = ''
    priority.value = ''
    includeArchived.value = ''
    tag.value = ''
    search.value = ''
    q.value = ''
  }

  return {
    status, sort, groupBy, projectId, priority, includeArchived, showRuns,
    tag, search, q, params, active, reset, submitSearch,
  }
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
  const loading = ref(false)

  async function load() {
    loading.value = true
    try {
      const qs = new URLSearchParams(
        Object.entries(params.value).filter(([, v]) => v !== '' && v != null))
      counts.value = await api.get(`/tasks/counts?${qs}`)
    } catch {
      // A list whose headers could not be counted still lists. The headers
      // render without a number rather than the page failing whole.
      counts.value = null
    } finally {
      loading.value = false
    }
  }

  return { counts, loading, load }
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
