import { ref, shallowRef } from 'vue'

// Module-scoped so any surface can open the one drawer the app mounts. The
// drawer is not URL-addressed: its view, layout and anchor live here, and
// "Open page" carries them to /files, where they become the URL.
const open = ref(false)
const source = shallowRef(null)
const anchor = ref('')
const view = ref('')
const layout = ref('')

export function openFilePreview(nextSource, nextAnchor = '') {
  source.value = { ...nextSource }
  anchor.value = nextAnchor
  view.value = ''
  layout.value = ''
  open.value = true
}

export function closeFilePreview() {
  open.value = false
}

export function useFilePreview() {
  return { open, source, anchor, view, layout, openFilePreview, closeFilePreview }
}
