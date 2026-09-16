import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { GROUPS, PRIORITIES, SORTS, STATUS_FILTERS } from '../utils/taskMeta.js'
import { persistScope, SCOPE_ALL } from './usePipelineScope.js'

// The task list's whole state, held in `location.search` and nowhere else.
//
// A list is a predicate plus a position, and both are facts about what is on
// screen — so both have to be in the URL or the screen is not linkable. The
// localStorage keys survive as *defaults*: what a bare `/tasks` is restored to
// on arrival, never a second opinion the URL has to be reconciled against.
//
// `project` is not one of the axes below: it is the one the three Pipeline
// tabs share, so `usePipelineScope` owns what it means and where it is
// remembered. This composable only reads it, and writes it through that
// module's own `persistScope` when a control on this tab changes it.

export const DEFAULT_SORT = 'created_desc'
export const DEFAULT_GROUP = 'status'

const DEFAULTS = {
  status: '', priority: '', sort: DEFAULT_SORT, group: DEFAULT_GROUP,
  q: '', tag: '', source: '', feature_id: '', assignee: '',
  archived: '', runs: '',
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
  runs: 'regin.tasks.runs',
}

// Only the axes this surface can decide about. An unknown *facet* value is the
// server's to refuse — `lib/tasks/query.py` answers 400 and names the
// parameter, and a filter silently dropped here would show a list that does
// not match its own controls. An unknown *sort* or *group* has no server to
// refuse it and no rows riding on it, so it falls back to the default.
const LEGAL = {
  sort: v => SORTS.some(s => s.value === v),
  group: v => GROUPS.some(g => g.value === v),
  status: v => STATUS_FILTERS.some(s => s.key === v),
  priority: v => v === '' || PRIORITIES.some(p => p.value === v),
  archived: v => v === '' || v === '1',
  runs: v => v === '' || v === '1',
}

const CORRECTED = ['sort', 'group']

// The stored axes that reach the server. `group` is not one: it decides how
// the loaded rows are split into feeds, never which rows those are.
const PARAMS = { status: 1, priority: 1, sort: 1, archived: 1, runs: 1 }

// The axes a reader ARRIVES in rather than chooses: a feature's task count
// links to `?feature_id=`, and the number clicked is the whole of that
// feature's tasks. Restoring yesterday's `status=done` on top of it would open
// a list shorter than the count that opened it.
const ARRIVAL = ['feature_id', 'assignee']

// …and what such an arrival suppresses. `sort` and `group` are not narrowings:
// they decide how the same rows are ordered and split, so a link that scopes
// the list has no quarrel with them.
const NARROWING = ['status', 'priority', 'archived']

function readAxis(query, axis) {
  const raw = String(query[axis] ?? '').trim()
  if (!raw) return DEFAULTS[axis]
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
      // Off by default: the backlog is features, and a run of one is reached
      // by opening it. The server's own default is the opposite, so the
      // narrowing is a parameter this surface sends, never a server change.
      root_only: s.runs === '1' ? '' : '1',
    }
  })

  const active = computed(() => {
    const s = state.value
    return [s.status, s.q, s.tag, s.source, s.priority, s.feature_id,
            s.assignee, s.archived].filter(Boolean).length
  })

  /**
   * Move one or more axes. `replace`, not `push`: a filter is an adjustment to
   * where you already are, and a history entry per control makes Back a
   * rewind of the toolbar rather than a way out of the page.
   */
  function patch(next) {
    const merged = { ...state.value, ...next }
    const query = { ...route.query }
    // The shell owns what a scope MEANS; this writes it so a control that
    // changes a facet and the scope together is one navigation, not two —
    // two would race, and the second would spread the first's stale query.
    if ('project' in next) {
      const scope = next.project || SCOPE_ALL
      persistScope(scope)
      if (scope === SCOPE_ALL) delete query.project
      else query.project = scope
      delete query.cursor
    }
    for (const axis of AXES) {
      const value = String(merged[axis] ?? '')
      if (axis in STORED) store(axis, value)
      if (value && value !== DEFAULTS[axis]) query[axis] = value
      else delete query[axis]
    }
    router.replace({ path: route.path, query })
  }

  function reset() {
    patch(Object.fromEntries(AXES.map(a => [a, DEFAULTS[a]])))
  }

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
    return Object.keys(restored).some(axis => axis in PARAMS)
  }

  return { state, project, params, active, patch, reset, restoreStored }
}
