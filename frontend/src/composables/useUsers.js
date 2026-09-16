import { ref } from 'vue'
import api from '../api'

// The people a task can be assigned to. Module-scoped for the same reason as
// `useProjects`: the assignee filter and any row chip naming the same handful
// of users should not be two requests.
//
// A failed read is a value, never a throw: an assignee filter the registry
// could not name still has to render the id it is filtering on, or the list
// appears unfiltered while it is not.
const items = ref([])
const loading = ref(false)
const error = ref('')
let loaded = false

export function useUsers() {
  async function load() {
    loading.value = true
    error.value = ''
    try {
      const data = await api.get('/users')
      const rows = Array.isArray(data) ? data : (data?.users || [])
      items.value = rows.map(u => ({
        id: u.id,
        name: u.display_name || u.username || `user ${u.id}`,
      }))
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
    if (id == null || id === '') return null
    return items.value.find(u => String(u.id) === String(id)) || null
  }

  return { items, loading, error, load, ensure, byId }
}
