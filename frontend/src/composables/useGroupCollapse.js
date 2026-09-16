import { ref } from 'vue'

// Which task groups are collapsed, remembered across reloads, filter changes
// and tab switches.
//
// The state is *collapsed*, not *open*, so the default is open and an axis
// nobody has touched needs nothing stored. Keyed by `<groupBy>:<key>` because
// "Done" under the status axis and "Done" under some future one are different
// headers; an identifier, so a restore cannot land on the wrong group however
// the axes are re-ordered later.
//
// It is what stops the one-request-per-group-per-mount cost too: a group that
// is remembered collapsed never opens, and a closed group fetches nothing.

const STORAGE_KEY = 'regin.tasks.collapsed'

function read() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    const parsed = raw ? JSON.parse(raw) : []
    return new Set(Array.isArray(parsed) ? parsed.filter(
      v => typeof v === 'string') : [])
  } catch {
    return new Set()
  }
}

export function useGroupCollapse() {
  const collapsed = ref(read())

  function idOf(groupBy, key) {
    return `${groupBy}:${key}`
  }

  function isOpen(groupBy, key) {
    return !collapsed.value.has(idOf(groupBy, key))
  }

  function setOpen(groupBy, key, open) {
    const id = idOf(groupBy, key)
    if (collapsed.value.has(id) === !open) return
    const next = new Set(collapsed.value)
    if (open) next.delete(id)
    else next.add(id)
    collapsed.value = next
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify([...next]))
    } catch {
      // Quota exceeded / private browsing. The groups still collapse for this
      // visit; only the memory of it across reloads is lost.
    }
  }

  return { isOpen, setOpen }
}
