// Pulling image files out of the three gestures that can carry one, and
// getting their bytes into the shape the api client sends.
//
// Shared because two composers already needed it and a third would have
// forked it: the bridge's `useImagePaste` (bytes staged against a live trace)
// and the tasks surface's `useAttachedImages` (bytes attached to an idea row).

/**
 * The image files in `source` — a ClipboardEvent's `clipboardData`, a
 * DataTransfer, or a file input element.
 *
 * A paste carries `items`, a drop and a file input carry `files`. Reading both
 * keeps one extractor for all three triggers. Empty is how a caller knows to
 * leave a text paste alone.
 */
export function imageFilesFrom(source) {
  const out = []
  for (const item of source?.items || []) {
    if (item.kind === 'file' && item.type?.startsWith('image/')) {
      const file = item.getAsFile()
      if (file) out.push(file)
    }
  }
  if (out.length) return out
  for (const file of source?.files || []) {
    if (file.type?.startsWith('image/')) out.push(file)
  }
  return out
}

// Bare base64 — the api client sends JSON, and both ingest endpoints speak
// base64 rather than multipart.
export function toBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onerror = () => reject(new Error('could not read the image'))
    // `result` is a data: URL; everything after the comma is the payload.
    reader.onload = () => resolve(String(reader.result).split(',')[1] || '')
    reader.readAsDataURL(file)
  })
}
