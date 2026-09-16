// Images on a captured idea, for both surfaces that edit one.
//
// The two differ in exactly one way, and it is the reason this is not
// `useImagePaste`: the capture composer has no task id yet. Bytes pasted there
// are HELD in the browser and uploaded by `flush(id)` once the row exists, so
// nothing is staged server-side for an idea that is abandoned mid-typing and
// there is no orphan to sweep. The edit sheet binds a saved task and every
// gesture is an immediate write, because there the row is already there to
// hang the bytes off.
//
// An entry is `{ key, idx, url, name, pending }`. `url` is an object URL
// either way — a held file already has its bytes in the browser, and a saved
// image is pulled through the authenticated api client because an `<img>` tag
// cannot attach the Authorization header itself.
import { onUnmounted, ref } from 'vue'
import api from '../api'
import { imageFilesFrom, toBase64 } from '../utils/imageFiles'

export function useTaskImages() {
  const items = ref([])
  const busy = ref(false)
  const error = ref('')

  let taskId = null
  let seq = 0
  // Bumped by every `bind`, so an adopt still in flight when the sheet is
  // rebound (or torn down) drops its URL instead of repopulating a list that
  // now belongs to a different task.
  let generation = 0

  function reset() {
    for (const item of items.value) URL.revokeObjectURL(item.url)
    items.value = []
    error.value = ''
  }

  function entry(url, { idx = null, name = '', pending = false, file = null }) {
    seq += 1
    return { key: `i${seq}`, idx, url, pending, file,
             name: name || (idx ? `image ${idx}` : 'pasted image') }
  }

  /** Point the composable at a saved task, or at nothing for the composer. */
  async function bind(task) {
    reset()
    taskId = task?.id ?? null
    generation += 1
    const mine = generation
    for (const img of task?.images || []) {
      // Best-effort per image: one that cannot be fetched is left out rather
      // than breaking the strip.
      const url = await api.getBlobUrl(`/tasks/${taskId}/images/${img.idx}`)
        .catch(() => '')
      if (mine !== generation) { if (url) URL.revokeObjectURL(url); return }
      if (url) items.value = [...items.value, entry(url, { idx: img.idx })]
    }
  }

  async function upload(file) {
    const data_b64 = await toBase64(file)
    const res = await api.post(`/tasks/${taskId}/images`, { data_b64 })
    if (res.ok === false) throw new Error(res.msg || 'could not attach the image')
    return res.image
  }

  /**
   * Take every image in `source` (clipboardData, a DataTransfer, or a file
   * input). Returns whether anything was claimed, so a caller can leave a text
   * paste to the browser.
   */
  async function add(source) {
    const files = imageFilesFrom(source)
    if (!files.length) return false
    error.value = ''
    if (taskId == null) {
      items.value = [...items.value, ...files.map(
        file => entry(URL.createObjectURL(file), { name: file.name, pending: true, file }))]
      return true
    }
    busy.value = true
    try {
      for (const file of files) {
        const img = await upload(file)
        items.value = [...items.value, entry(URL.createObjectURL(file),
                                             { idx: img.idx, name: file.name })]
      }
    } catch (e) {
      // Partial success is kept: the images already uploaded are on the task,
      // and dropping their thumbnails would hide rows that really exist.
      error.value = e?.message || 'could not attach the image'
    } finally {
      busy.value = false
    }
    return true
  }

  async function remove(item) {
    if (item.pending) {
      drop(item)
      return true
    }
    const res = await api.del(`/tasks/${taskId}/images/${item.idx}`)
    if (res.ok === false) {
      error.value = res.msg || 'could not remove this image'
      return false
    }
    drop(item)
    return true
  }

  function drop(item) {
    URL.revokeObjectURL(item.url)
    items.value = items.value.filter(i => i !== item)
  }

  /**
   * Upload everything held for a task that did not exist when it was pasted.
   * False when one upload was refused — the task is already saved by then, so
   * the caller reports the images rather than the capture as the failure.
   */
  async function flush(id) {
    taskId = id
    const held = items.value.filter(i => i.pending)
    if (!held.length) return true
    busy.value = true
    try {
      for (const item of held) {
        const img = await upload(item.file)
        Object.assign(item, { idx: img.idx, pending: false, file: null })
      }
      return true
    } catch (e) {
      error.value = e?.message || 'the idea was saved, but an image was not'
      return false
    } finally {
      busy.value = false
    }
  }

  // Object URLs outlive the component that made them, so a sheet closed with
  // thumbnails on screen would leak one blob per image for the life of the
  // page.
  onUnmounted(() => { generation += 1; reset() })

  return { items, busy, error, bind, add, remove, flush, reset }
}
