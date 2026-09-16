// Image attachments for the bridge composers (`/live` NOW zone, the
// session-detail follow-up).
//
// Mirrors useFileMentions.js's shape — one composable, owned state, the draft
// text left to the caller — because both composers already wire that contract
// and a second one would fork it.
//
// Three triggers, one path. Paste is the desktop gesture, drop is its
// neighbour, and the attach button exists because `/live` is a phone surface
// where a long-press "Paste" fires the paste event unreliably and the thing
// people actually want is the camera roll. All three end at `stage()`.
//
// Nothing is inserted into the draft here. `stage()` returns the server's
// `[Image #N]` marker and the caller splices it at the caret, the same way an
// accepted `@` mention is applied — that marker is the whole contract: it is
// what Claude Code writes for a terminal paste, what the delivered text
// carries to the agent, and what binds the stored bytes to the prompt span so
// the trace UI renders a thumbnail.
import { ref, onUnmounted } from 'vue'
import api from '../api'
import { imageFilesFrom, toBase64 } from '../utils/imageFiles'

// Re-exported because both composers reach for the extractor next to the
// composable that uses it.
export { imageFilesFrom }

export function useImagePaste() {
  // One entry per staged image: { idx, marker, url, name }. `url` is an object
  // URL for the local thumbnail — the bytes are already in the browser, so the
  // preview never round-trips to the server.
  const staged = ref([])
  const uploading = ref(false)
  const error = ref('')

  let sessionId = ''
  // Switching sessions must drop the staged chips, not just retarget the
  // uploads: an `[Image #2]` staged against the old trace means a different
  // picture on the new one, and the thumbnails would sit above a fresh draft
  // that never carried them.
  function bind(id) {
    const next = id || ''
    if (next !== sessionId) clear()
    sessionId = next
  }

  function revokeAll() {
    for (const item of staged.value) URL.revokeObjectURL(item.url)
    staged.value = []
  }

  // Called once the draft has been sent: the markers left with it, so these
  // entries now describe images that belong to a prompt rather than to the
  // composer. The server rows are claimed by the span ingest, not here.
  function clear() {
    revokeAll()
    error.value = ''
  }

  async function stageOne(file) {
    const data_b64 = await toBase64(file)
    const res = await api.post(
      `/sessions/${encodeURIComponent(sessionId)}/bridge-attachments`,
      { data_b64 },
    )
    if (!res || res.error) throw new Error(res?.error || 'upload failed')
    staged.value = [...staged.value, {
      idx: res.idx,
      marker: res.marker,
      url: URL.createObjectURL(file),
      name: file.name || `image ${res.idx}`,
    }]
    return res.marker
  }

  /**
   * Upload every image in `source` (a ClipboardEvent's clipboardData, a
   * DataTransfer, or a file input's element). Resolves to the markers to
   * splice into the draft, in order — empty when there was nothing to take,
   * which is how a caller knows to leave a text paste alone.
   */
  async function stage(source) {
    const files = imageFilesFrom(source)
    if (!files.length || !sessionId) return []
    uploading.value = true
    error.value = ''
    const markers = []
    try {
      for (const file of files) markers.push(await stageOne(file))
    } catch (e) {
      // Partial success is kept: the markers already handed back are in the
      // draft and their rows are staged, so discarding them would strand the
      // bytes and blank text the operator can see on screen.
      error.value = e?.message || 'could not attach the image'
    } finally {
      uploading.value = false
    }
    return markers
  }

  function remove(idx) {
    const hit = staged.value.find(s => s.idx === idx)
    if (hit) URL.revokeObjectURL(hit.url)
    staged.value = staged.value.filter(s => s.idx !== idx)
  }

  // Object URLs outlive the component that made them, so a composer that is
  // torn down (a session switch, a collapsed follow-up) would leak one blob
  // per staged image for the life of the page.
  onUnmounted(clear)

  return { staged, uploading, error, bind, stage, remove, clear }
}
