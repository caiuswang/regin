import { ref, shallowRef } from 'vue'

// Module-scoped so any surface can open the one drawer the app mounts.
const open = ref(false)
const source = shallowRef(null)
const anchor = ref('')

export function openFilePreview(nextSource, nextAnchor = '') {
  source.value = { ...nextSource }
  anchor.value = nextAnchor
  open.value = true
}

export function closeFilePreview() {
  open.value = false
}

export function useFilePreview() {
  return { open, source, anchor, openFilePreview, closeFilePreview }
}
