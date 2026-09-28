import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { GROUPS, PRIORITIES, SORT_KEYS, STATUS_FILTERS } from '../utils/taskMeta.js'

// The task list's whole state, held in `location.search` and nowhere else.
//
// A list is a predicate plus a position, and both are facts about what is on
// screen — so both have to be in the URL or the screen is not linkable. The
// localStorage keys survive as *defaults*: what a bare `/tasks` is restored to
// on arrival, never a second opinion the URL has to be reconciled against.
//
// `project` is not one of the axes below: it is the one the Pipeline tabs
// share, and the shell's selector is its only writer (I4). This composable
// reads it and carries it through every patch untouched.
//
// `view` (board | list | graph) is an axis too, so a reload lands on the same
// view — but it is a reading of the rows, not a narrowing, so Clear leaves it
// alone. `open` is the Graph's "Open chains only" chip: on by default, so the
// URL carries only its absence (`open=0`).
//
// `priority` is a comma list (`high,urgent`), canonicalised by the server.

export const DEFAULT_SORT = 'created:desc'
export const DEFAULT_GROUP = 'next_step'
export const DEFAULT_VIEW = 'list'

const DEFAULTS = {
  status: '', priority: '', sort: DEFAULT_SORT, group: DEFAULT_GROUP,
  q: '', tag: '', source: '', feature_id: '', assignee: '',
  archived: '', unfeatured: '', view: DEFAULT_VIEW, open: '',
}

const AXES = Object.keys(DEFAULTS)

// The axes a bare path is restored from. `q` is excluded because returning to
// the page under a search someone typed yesterday reads as an empty backlog,
// and `feature_id`/`assignee`/`tag` because they are addresses someone arrived
// on rather than a view they chose to keep.
const STORED = {
  status: 'regin.tasks.status',
  priority: 'regin.tasks.priority',
  sort: 'regin.tasks.sort',
  group: 'regin.tasks.group',
  archived: 'regin.tasks.archived',
  unfeatured: 'regin.tasks.unfeatured',
}

// Only the axes this surface can decide about. An unknown *facet* value is the
// server's to refuse — `lib/tasks/query.py` answers 400 and names the
// parameter, and a filter silently dropped here would show a list that does
// not match its own controls. An unknown *sort* or *group* has no server to
// refuse it and no rows riding on it, so it falls back to the default.
const LEGAL = {
  sort: (v) => {
    const [key, dir] = v.split(':')
    return SORT_KEYS.some(s => s.value === key) && (dir === 'asc' || dir === 'desc')
  },
  view: v => VIEWS.includes(v),
  group: v => GROUPS.some(g => g.value === v),
  status: v => STATUS_FILTERS.some(s => s.key === v),
  priority: v => v === '' || v.split(',').every(
    part => PRIORITIES.some(p => p.value === part)),
  archived: v => v === '' || v === '1',
  unfeatured: v => v === '' || v === '1',
}

const CORRECTED = ['sort', 'group', 'view']

export const VIEWS = ['board', 'list', 'graph']

// `lib/tasks/next_step.py::GROUP_KEYS`. A `?group=` naming one of them is an
// ADDRESS, not a grouping: the next-step list, opened on that group — which is
// what a count on the Overview links to. The grouping reads as `next_step`.
export const NEXT_STEP_GROUPS = ['ready', 'running', 'blocked', 'failed', 'ideas']

// The stored axes that reach the server. `group` is not one: it decides how
// the loaded rows are split into feeds, never which rows those are.
const PARAMS = { status: 1, priority: 1, sort: 1, archived: 1, unfeatured: 1 }

// The axes a reader ARRIVES in rather than chooses: a feature's task count
// links to `?feature_id=`, and the number clicked is the whole of that
// feature's tasks. Restoring yesterday's `status=done` on top of it would open
// a list shorter than the count that opened it.
const ARRIVAL = ['feature_id', 'assignee']

// …and what such an arrival suppresses. `sort` and `group` are not narrowings:
// they decide how the same rows are ordered and split, so a link that scopes
// the list has no quarrel with them.
const NARROWING = ['status', 'priority', 'archived', 'unfeatured']

function readAxis(query, axis) {
  const raw = String(query[axis] ?? '').trim()
  if (!raw) return DEFAULTS[axis]
  if (axis === 'group' && NEXT_STEP_GROUPS.includes(raw)) return 'next_step'
  if (CORRECTED.includes(axis) && !LEGAL[axis](raw)) return DEFAULTS[axis]
  return raw
}

function stored(axis) {
  try { return localStorage.getItem(STORED[axis]) } catch { return null }
}

function store(axis, value) {
  const key = STORED[axis]
  if (!key) return
  try {
    if (value === DEFAULTS[axis]) localStorage.removeItem(key)
    else localStorage.setItem(key, value)
  } catch {
    // Quota exceeded / private browsing. The filter still holds for this
    // visit; only the default it would restore to is lost.
  }
}

export function useTaskQuery() {
  const route = useRoute()
  const router = useRouter()

  const state = computed(() => {
    const q = route.query
    const out = {}
    for (const axis of AXES) out[axis] = readAxis(q, axis)
    return out
  })

  // The shell's scope, read rather than kept: `all` is the absence of a
  // constraint, and `none` is a real one the server spells the same way.
  const project = computed(() => {
    const raw = String(route.query.project ?? '').trim()
    return !raw || raw === 'all' ? '' : raw
  })

  /** The next-step group a link arrived at (`?group=ready`), or `''`. */
  const anchor = computed(() => {
    const raw = String(route.query.group ?? '').trim()
    return NEXT_STEP_GROUPS.includes(raw) ? raw : ''
  })

  /** Exactly what the feed and the counts are asked for. */
  const params = computed(() => {
    const s = state.value
    return {
      status: s.status,
      sort: s.sort,
      q: s.q,
      tag: s.tag,
      source: s.source,
      priority: s.priority,
      project_id: project.value,
      feature_id: s.feature_id,
      assignee: s.assignee,
      include_archived: s.archived,
      // Off by default: a feature's steps and a defect's fix tasks are most of
      // the backlog, and the counts beside the feed describe all of it.
      root_only: s.unfeatured,
    }
  })

  // What "Clear N" counts. The arrival keys are not, since Clear keeps them.
  const active = computed(() => {
    const s = state.value
    return [s.status, s.q, s.tag, s.source, s.priority,
            s.archived, s.unfeatured].filter(Boolean).length
  })

  /**
   * Move one or more axes. `replace`, not `push`: a filter is an adjustment to
   * where you already are, and a history entry per control makes Back a
   * rewind of the toolbar rather than a way out of the page.
   */
  function patch(next) {
    const merged = { ...state.value, ...next }
    const query = { ...route.query }
    // Anything but an explicit page changes the predicate, and an offset that
    // survives a narrowing points into rows the new query does not have.
    if ('page' in next && String(next.page) !== '0') query.page = String(next.page)
    else delete query.page
    if ('size' in next) {
      if (String(next.size) === '50') delete query.size
      else query.size = String(next.size)
    }
    // The grouping is stored only when the reader picks one: a `?group=ready`
    // arrival reads as next_step without anyone choosing it, and storing that
    // would overwrite the grouping they keep. The anchor stays in the URL until
    // a grouping is picked, so it is still the address after a filter change.
    const keptAnchor = !('group' in next) && anchor.value
    for (const axis of AXES) {
      const value = String(merged[axis] ?? '')
      if (axis in STORED && (axis !== 'group' || 'group' in next)) store(axis, value)
      if (value && value !== DEFAULTS[axis]) query[axis] = value
      else delete query[axis]
    }
    if (keptAnchor) query.group = keptAnchor
    router.replace({ path: route.path, query })
  }

  /**
   * Clear the narrowings. A view is a reading, not a narrowing, and an arrival
   * key (a feature's task count links `?feature_id=`) is the list's address,
   * so Clear keeps both — `{ arrival: true }` also leaves the address, for a
   * caller that must reach a row the address itself hides.
   */
  function reset({ arrival = false } = {}) {
    patch(Object.fromEntries(AXES
      .filter(a => a !== 'view' && a !== 'group'
        && (arrival || !ARRIVAL.includes(a)))
      .map(a => [a, DEFAULTS[a]])))
  }

  const page = computed(() => Math.max(0, parseInt(route.query.page, 10) || 0))
  const size = computed(() => Math.max(1, parseInt(route.query.size, 10) || 50))

  /**
   * On a bare URL, restore the stored axes AND rewrite the URL, so what is on
   * screen is linkable. The URL always wins where it states an axis, and a
   * stored value that is no longer a legal option is dropped rather than
   * carried into a query the server would refuse.
   *
   * A URL that names an arrival scope suppresses the stored *narrowings* for
   * as long as it names one — suppresses, never clears: those are saved
   * preferences, and spending one to make a count match its list is a cost
   * the reader did not agree to. Leaving the scope restores them.
   *
   * Returns whether the restore changed what the SERVER is asked for — not
   * merely whether it navigated. `group` is the difference: it is rendered
   * client-side, so restoring it rewrites the URL while leaving `params`
   * identical, and a caller waiting on a params change for its first load
   * would wait forever.
   */
  function restoreStored() {
    const scoped = ARRIVAL.some(axis => String(route.query[axis] ?? '').trim())
    const restored = {}
    for (const axis of Object.keys(STORED)) {
      if (route.query[axis] != null) continue
      if (scoped && NARROWING.includes(axis)) continue
      const value = stored(axis)
      if (!value || value === DEFAULTS[axis]) continue
      if (!LEGAL[axis](value)) { store(axis, DEFAULTS[axis]); continue }
      restored[axis] = value
    }
    if (!Object.keys(restored).length) return false
    router.replace({ path: route.path,
                     query: { ...route.query, ...restored } })
    // `group` alone is client-side, except where it flips grouped ↔ flat:
    // those two read the server differently, so the caller must wait for it.
    const flipsMode = 'group' in restored
      && (restored.group === 'none') !== (state.value.group === 'none')
    return flipsMode || Object.keys(restored).some(axis => axis in PARAMS)
  }

  return { state, project, params, active, anchor, page, size, patch, reset, restoreStored }
}
