import { ref, watchEffect, onUnmounted } from 'vue'
import api from '../api'

/**
 * Object URLs for the images attached to one prompt span, keyed by the
 * 1-indexed `[Image #N]` number.
 *
 * The image-bytes endpoint requires an Authorization header an `<img>` tag
 * cannot attach, so every image is pulled through the authenticated api client
 * and handed to the tag as a blob URL — and revoked again, or a surface that
 * walks from span to span leaks one per image it passes.
 *
 * `source` is a getter returning `{ traceId, spanId, indices }` so a caller
 * whose span CHANGES (the presentation reader) gets the same cache discipline
 * as one whose span is fixed (a feed card).
 */
export function useSpanImages(source) {
  // The bookkeeping map is deliberately raw: reading the ref inside the
  // fetching effect would make that effect depend on its own writes.
  let live = new Map()
  const blobUrls = ref(new Map())

  function revokeAll() {
    for (const url of live.values()) URL.revokeObjectURL(url)
    live = new Map()
    blobUrls.value = new Map()
  }

  let key = ''
  // An in-flight fetch outlives the component that started it: the reader is
  // closed, or walked to another span, while a multi-megabyte screenshot is
  // still downloading. Without this the resolved URL lands in a Map nobody will
  // ever revoke.
  let dead = false

  watchEffect(async () => {
    const { traceId, spanId, indices } = source() || {}
    // Keyed by the span it was filled for: one reader instance steps between
    // prompts, and index 1 of the next prompt is a different image wearing the
    // same number.
    const nextKey = `${traceId || ''}::${spanId || ''}`
    if (nextKey !== key) {
      key = nextKey
      revokeAll()
    }
    if (!traceId || !spanId || !indices?.length) {
      revokeAll()
      return
    }

    for (const idx of indices) {
      if (live.has(idx)) continue
      try {
        const url = await api.getBlobUrl(
          `/sessions/${encodeURIComponent(traceId)}/prompts/${encodeURIComponent(spanId)}/images/${idx}`,
        )
        // Re-read the source after the await: the caller may have moved on, and
        // a stale fetch must not repopulate a cache that was just cleared.
        // `live.has` is re-checked too — the guard above it ran before the
        // await, so two effect runs can race the same index, and the loser's
        // URL has to be released rather than silently overwritten.
        const now = dead ? {} : (source() || {})
        if (now.spanId === spanId && now.indices?.includes(idx) && !live.has(idx)) {
          live.set(idx, url)
          blobUrls.value = new Map(live)
        } else {
          URL.revokeObjectURL(url)
        }
      } catch {
        // Best-effort: a failed fetch falls back to the `[Image #N]`
        // placeholder rendering, not a broken img tag.
      }
    }
  })

  onUnmounted(() => { dead = true; revokeAll() })

  return { blobUrls }
}
