// Images hung off a saved row — a task, a feature, or a defect. `resource` is
// the api segment (`tasks` | `features` | `defects`); everything else is
// identical, because all three serve `{id, images:[{idx}]}` and the same three
// image routes under it.
//
// The surfaces differ in exactly one way, and it is the reason this is not
// `useImagePaste`: a capture composer has no row id yet. Bytes pasted there
// are HELD in the browser and uploaded by `flush(id)` once the row exists, so
// nothing is staged server-side for an idea that is abandoned mid-typing and
// there is no orphan to sweep. An edit sheet binds a saved row and every
// gesture is an immediate write, because there the row is already there to
// hang the bytes off.
//
// An entry is `{ key, idx, url, name, pending }`. `url` is an object URL
// either way — a held file already has its bytes in the browser, and a saved
// image is pulled through the authenticated api client because an `<img>` tag
// cannot attach the Authorization header itself.
//
// A HELD entry carries an `idx` too, and that is what lets a composer write
// `![](image:N)` into the body at the moment of the paste rather than after
// the row exists. The index is allocated here, monotonically and never
// reissued — remove the first of two held images and the second keeps the
// index its reference already names — and `flush` asks the server for it. The
// server honours a free index, and for a row minted seconds ago every index
// is free.
import { onUnmounted, ref } from 'vue'
import api from '../api'
import { imageFilesFrom, toBase64 } from '../utils/imageFiles'

export function useAttachedImages(resource = 'tasks') {
  const items = ref([])
  const busy = ref(false)
  const error = ref('')

  let ownerId = null
  let seq = 0
  // High-water mark for the indices this composable hands to held files.
  // Monotonic, so a removal never renumbers an index some prose already cites.
  let heldIdx = 0
  // Bumped by every `bind`, so an adopt still in flight when the sheet is
  // rebound (or torn down) drops its URL instead of repopulating a list that
  // now belongs to a different task.
  let generation = 0

  function reset() {
    for (const item of items.value) URL.revokeObjectURL(item.url)
    items.value = []
    heldIdx = 0
    error.value = ''
    // The owner goes with the list, and that is the whole point. `flush` binds
    // this instance to the row it just created, and a composer is mounted for
    // the life of the page: leave the id behind and the NEXT thing pasted into
    // the same composer is written straight onto the row before it — a second
    // bug report's screenshot filed as evidence for the first.
    ownerId = null
  }

  function entry(url, { idx = null, name = '', pending = false, file = null }) {
    seq += 1
    return { key: `i${seq}`, idx, url, pending, file,
             name: name || (idx ? `image ${idx}` : 'pasted image') }
  }

  /** Point the composable at a saved row, or at nothing for a composer. */
  async function bind(row) {
    reset()
    ownerId = row?.id ?? null
    generation += 1
    const mine = generation
    for (const img of row?.images || []) {
      // Best-effort per image: one that cannot be fetched is left out rather
      // than breaking the strip.
      const url = await api.getBlobUrl(`/${resource}/${ownerId}/images/${img.idx}`)
        .catch(() => '')
      if (mine !== generation) { if (url) URL.revokeObjectURL(url); return }
      if (url) items.value = [...items.value, entry(url, { idx: img.idx })]
    }
  }

  async function upload(file, wantIdx = null) {
    const data_b64 = await toBase64(file)
    const res = await api.post(`/${resource}/${ownerId}/images`,
                               wantIdx ? { data_b64, idx: wantIdx } : { data_b64 })
    if (res.ok === false) throw new Error(res.msg || 'could not attach the image')
    return res.image
  }

  /**
   * Take every image in `source` (clipboardData, a DataTransfer, or a file
   * input).
   *
   * Resolves to the `idx` of each image it took, in order — which is what the
   * `![](image:N)` reference the caller writes at the caret is made of. Empty
   * is how a caller knows nothing was claimed and a text paste should be left
   * to the browser.
   */
  async function add(source) {
    const files = imageFilesFrom(source)
    if (!files.length) return []
    error.value = ''
    if (ownerId == null) {
      const held = files.map((file) => {
        heldIdx += 1
        return entry(URL.createObjectURL(file),
                     { idx: heldIdx, name: file.name, pending: true, file })
      })
      items.value = [...items.value, ...held]
      return held.map(item => item.idx)
    }
    busy.value = true
    const taken = []
    try {
      for (const file of files) {
        const img = await upload(file)
        taken.push(img.idx)
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
    return taken
  }

  async function remove(item) {
    if (item.pending) {
      drop(item)
      return true
    }
    const res = await api.del(`/${resource}/${ownerId}/images/${item.idx}`)
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
   * Upload everything held for a row that did not exist when it was pasted.
   * False when one upload was refused — the row is already saved by then, so
   * the caller reports the images rather than the capture as the failure.
   */
  async function flush(id) {
    ownerId = id
    const held = items.value.filter(i => i.pending)
    if (!held.length) return true
    busy.value = true
    try {
      for (const item of held) {
        const img = await upload(item.file, item.idx)
        // The server's index, not the one asked for: they agree for a row
        // this composer just created, and when they do not the reference in
        // the prose is stale — which `resolveImageRefs` renders as visible
        // text rather than as a broken picture.
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
