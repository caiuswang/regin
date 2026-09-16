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

/**
 * useProjects — the registered repos a task can be filed under.
 *
 * `ensure()` is the mount-time call: it fetches once and is a no-op
 * afterwards, so a row rendering a project chip never triggers a request.
 * `load()` is the explicit refetch.
 */
export function useProjects() {
  async function load() {
    loading.value = true
    error.value = ''
    try {
      const data = await api.get('/repos')
      items.value = (data.repos || []).map(r => ({ id: r.id, name: r.name }))
      loaded = true
    } catch (e) {
      error.value = e.message || String(e)
    } finally {
      loading.value = false
    }
  }

  async function ensure() {
    if (loaded || loading.value) return
    await load()
  }

  function byId(id) {
    if (id == null) return null
    return items.value.find(p => p.id === id) || null
  }

  return { items, loading, error, load, ensure, byId }
}
