// One task's links to another tracker's issues.
//
// Reads never open a socket: the server answers from the snapshot it cached,
// and `refresh` is the only thing that spends a round trip. That is why this
// composable has no interval — a link's age is displayed, not polled away.
// A link to a LIVE tracker has no age to display and no Refresh at all.
import { computed, ref } from 'vue'
import api from '../api'

export function useTaskLinks() {
  const linksList = ref([])
  const providers = ref([])
  const loading = ref(false)
  const error = ref('')
  const busyId = ref(0)

  // Which platforms can actually resolve a paste right now.
  const configured = computed(() => providers.value.filter(p => p.configured))
  const placeholder = computed(() => configured.value.length
    ? `Paste ${configured.value.map(p => p.ref_hint).join(' or ')}`
    : 'No issue tracker is configured yet')

  async function loadProviders() {
    try {
      const { providers: rows } = await api.get('/task-integrations')
      providers.value = rows || []
    } catch (e) {
      error.value = e.message || String(e)
    }
  }

  // Reads an issue against no task: the composer needs its title before the
  // row that would hold the link exists, and a dialog the operator closes has
  // to leave nothing behind. Null on failure, with `error` carrying why.
  async function preview(ref_, provider = '') {
    error.value = ''
    busyId.value = -1
    try {
      return await api.post('/tasks/links/preview',
        { ref: ref_, ...(provider ? { provider } : {}) })
    } catch (e) {
      error.value = e.message || String(e)
      return null
    } finally {
      busyId.value = 0
    }
  }

  async function load(taskId) {
    loading.value = true
    error.value = ''
    try {
      const [linkData, providerData] = await Promise.all([
        api.get(`/tasks/${taskId}/links`),
        api.get('/task-integrations'),
      ])
      linksList.value = linkData.links || []
      providers.value = providerData.providers || []
    } catch (e) {
      error.value = e.message || String(e)
    } finally {
      loading.value = false
    }
  }

  function replace(link) {
    const at = linksList.value.findIndex(l => l.id === link.id)
    if (at === -1) linksList.value = [...linksList.value, link]
    else linksList.value = linksList.value.map(l => l.id === link.id ? link : l)
  }

  // Every mutation returns the error text rather than throwing: the caller
  // renders it next to the field the operator is still looking at.
  async function attach(taskId, ref_, provider = '') {
    error.value = ''
    busyId.value = -1
    try {
      const { link } = await api.post(`/tasks/${taskId}/links`,
        { ref: ref_, ...(provider ? { provider } : {}) })
      replace(link)
      return ''
    } catch (e) {
      return e.message || String(e)
    } finally {
      busyId.value = 0
    }
  }

  async function refresh(taskId, linkId) {
    busyId.value = linkId
    try {
      const { link } = await api.post(`/tasks/${taskId}/links/${linkId}/refresh`)
      replace(link)
      return ''
    } catch (e) {
      return e.message || String(e)
    } finally {
      busyId.value = 0
    }
  }

  async function detach(taskId, linkId) {
    busyId.value = linkId
    try {
      await api.del(`/tasks/${taskId}/links/${linkId}`)
      linksList.value = linksList.value.filter(l => l.id !== linkId)
      return ''
    } catch (e) {
      return e.message || String(e)
    } finally {
      busyId.value = 0
    }
  }

  return { links: linksList, providers, configured, placeholder, loading,
           error, busyId, load, loadProviders, preview, attach, refresh,
           detach }
}
