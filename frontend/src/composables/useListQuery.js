import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'

// A Pipeline list's whole state, held in `location.search` and nowhere else.
//
// A list is a predicate plus a position, and both are facts about what is on
// screen — so both live in the URL, and the screen is a link. `project` is the
// shell's axis: a list reads it through `route.query` and never writes it, so
// every patch starts from the current query and carries it through untouched.

export const DEFAULT_SIZE = 50

/**
 * @param {object} opts
 * @param {Record<string,string>} opts.defaults  every axis this list owns, with
 *   its default. `q` and `sort` are expected; the rest are facets or a view.
 * @param {string[]} [opts.facets]  axes that narrow the rows — what "Clear N"
 *   counts. `q` is always one.
 * @param {string} [opts.viewKey]  the axis the view switcher writes. A view is
 *   a reading of the rows, not a narrowing, so Clear keeps it.
 * @param {string[]} [opts.kept]  keys a reader arrived on (`feature_id`) —
 *   Clear keeps them too, because a link's scope is not a filter to undo.
 */
export function useListQuery({ defaults, facets = [], viewKey = '', kept = [] }) {
  const route = useRoute()
  const router = useRouter()
  const axes = Object.keys(defaults)

  const state = computed(() => {
    const out = {}
    for (const axis of axes) {
      const raw = String(route.query[axis] ?? '').trim()
      out[axis] = raw || defaults[axis]
    }
    out.page = Math.max(0, parseInt(route.query.page, 10) || 0)
    out.size = Math.max(1, parseInt(route.query.size, 10) || DEFAULT_SIZE)
    return out
  })

  const sortKey = computed(() => String(state.value.sort || '').split(':')[0])
  const sortDir = computed(() =>
    (String(state.value.sort || '').split(':')[1] === 'asc' ? 'asc' : 'desc'))

  const dirtyCount = computed(() => ['q', ...facets]
    .filter(axis => state.value[axis] && state.value[axis] !== defaults[axis])
    .length)

  /**
   * Move one or more axes. `replace`, not `push`: a control is an adjustment
   * to where you already are, and a history entry per change makes Back a
   * rewind of the toolbar rather than a way out of the page. Anything but an
   * explicit `page` changes the predicate, so the position drops.
   */
  function patch(next) {
    const query = { ...route.query }
    for (const [key, value] of Object.entries(next)) {
      const text = value == null ? '' : String(value)
      if (!text || text === defaults[key]
          || (key === 'page' && text === '0')
          || (key === 'size' && text === String(DEFAULT_SIZE))) delete query[key]
      else query[key] = text
    }
    if (!('page' in next)) delete query.page
    router.replace({ path: route.path, query })
  }

  function reset() {
    const query = {}
    for (const key of ['project', viewKey, ...kept]) {
      if (key && route.query[key] != null) query[key] = route.query[key]
    }
    router.replace({ path: route.path, query })
  }

  function setSort(key, dir) { patch({ sort: `${key}:${dir}` }) }
  function toggleDir() { setSort(sortKey.value, sortDir.value === 'asc' ? 'desc' : 'asc') }

  return { state, sortKey, sortDir, dirtyCount, patch, reset, setSort, toggleDir }
}

/**
 * Which of ListStatus's states a list is in. One function so every tab tells
 * "nothing exists", "nothing matches" and "past the end" apart the same way.
 */
export function listStatusState({ loaded, error, items, total, population }) {
  if (error) return 'error'
  if (!loaded) return 'loading'
  if (items?.length) return 'rows'
  if (!population) return 'empty'
  if (!total) return 'no-match'
  return 'past-end'
}
