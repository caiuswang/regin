import { ref } from 'vue'
import api from '../api'
import { useConfirm } from './useConfirm.js'
import { useFlash } from './useFlash.js'

export const KEY_SCOPES = ['task:capture']

/**
 * useApiKeys — admin CRUD over `/api/api-keys`.
 *
 * `minted` holds the one plaintext copy of a freshly minted key. The server
 * stores only its SHA-256, so once this ref is cleared the key is gone for
 * good and the only remedy is to mint another — the view says so where it
 * shows it.
 */
export function useApiKeys() {
  const { flash } = useFlash()
  const { confirm } = useConfirm()
  const keys = ref([])
  const loading = ref(true)
  const saving = ref(false)
  const formError = ref('')
  const minted = ref(null)

  async function load() {
    loading.value = true
    try {
      keys.value = (await api.get('/api-keys')).keys || []
    } catch (e) {
      flash(e.message || 'Could not load API keys', 'error')
      keys.value = []
    } finally {
      loading.value = false
    }
  }

  async function mint(name) {
    const label = (name || '').trim()
    if (!label) {
      formError.value = 'A name is required.'
      return false
    }
    saving.value = true
    const result = await api.post('/api-keys', { name: label, scopes: KEY_SCOPES })
    saving.value = false
    if (result.ok === false) {
      formError.value = result.msg || 'The server refused this key.'
      return false
    }
    formError.value = ''
    minted.value = { key: result.key, name: result.api_key?.name || label }
    await load()
    return true
  }

  async function revoke(key) {
    const ok = await confirm(
      'Revoke API key',
      `Revoke "${key.name}"? Anything using it stops working immediately.`, true)
    if (!ok) return false
    const result = await api.del(`/api-keys/${key.id}`)
    if (result.ok === false) {
      flash(result.msg || 'Could not revoke this key', 'error')
      return false
    }
    flash('API key revoked')
    await load()
    return true
  }

  function dismissMinted() {
    minted.value = null
  }

  return { keys, loading, saving, formError, minted, load, mint, revoke, dismissMinted }
}
