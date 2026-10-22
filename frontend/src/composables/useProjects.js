import { ref } from 'vue'
import api from '../api'

// A task's *project* is a registered repo. regin already has this concept —
// `pattern_deployments.project_id` is a repo id, `web/blueprints/skills.py`
// resolves one against `Repo`, and the skills UI calls it "Project" — so the
// task surface reads that registry rather than growing a second one to
// curate. Managing them stays where it already lives: /repos.
//
// Module-scoped so the list, the composer's picker and the row chips share one
// copy: three components naming the same eight repos should not be three
// requests.
const items = ref([])
const loading = ref(false)
const error = ref('')
let loaded = false
let inflight = null

/**
 * useProjects — the registered repos a task can be filed under.
 *
 * `ensure()` is the mount-time call: it fetches once and is a no-op
 * afterwards, so a row rendering a project chip never triggers a request.
 * `load()` is the explicit refetch.
 */
export function useProjects() {
  async function fetchRepos() {
    loading.value = true
    error.value = ''
    try {
      const data = await api.get('/repos')
      items.value = (data.repos || []).map(r => ({ id: r.id, name: r.name, path: r.path }))
      loaded = true
    } catch (e) {
      error.value = e.message || String(e)
    } finally {
      loading.value = false
      inflight = null
    }
  }

  function load() {
    inflight = inflight || fetchRepos()
    return inflight
  }

  // Awaits a load already in flight rather than returning early: a form that
  // pre-selects a project from this list must not read it while it is empty.
  async function ensure() {
    if (loaded) return
    await load()
  }

  function byId(id) {
    if (id == null) return null
    return items.value.find(p => p.id === id) || null
  }

  // After a write: a load already in flight may have been answered before the
  // write landed, so wait it out and read again.
  async function refresh() {
    if (inflight) await inflight
    await load()
  }

  return { items, loading, error, load, ensure, refresh, byId }
}

/**
 * The select value a creation form starts at, carried from a known endpoint.
 *
 * `raw` is a repo id (a number, or a scope string such as `?project=3`); any
 * other value — `all`, `none`, empty — or an id no listed repo answers to
 * starts at "No project" (`''`), so a dropped repo is never sent.
 */
export function carriedProject(raw, projects) {
  if (raw == null || raw === '') return ''
  const id = Number(raw)
  if (!Number.isInteger(id)) return ''
  return (projects || []).some(p => p.id === id) ? String(id) : ''
}

/** Select options for a project picker: "No project", then every repo. */
export function projectOptions(projects) {
  return [{ value: '', label: 'No project' },
          ...(projects || []).map(p => ({ value: String(p.id), label: p.name }))]
}
