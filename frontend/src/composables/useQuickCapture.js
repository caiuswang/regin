import { ref } from 'vue'
import api from '../api'
import { draftBody, draftError } from './useTasks.js'

// Broadcast rather than a shared store: the only listener is whichever task
// feed happens to be mounted, and a capture from the bubble must refresh it
// without the bubble knowing the feed exists.
export const TASK_CAPTURED_EVENT = 'regin:task-captured'

// Module-scoped, so an accidental Escape does not destroy what was typed: the
// dialog unmounts, the draft survives until a capture actually lands.
const open = ref(false)
// The draft is module-level so an Escape
// keeps the pasted reference with the words it belongs to. `draftBody` reads
// only the fields the capture route accepts, so it never reaches the server.
const draft = ref({ title: '', body: '' })
const error = ref('')
// Rides with the draft for the same reason the draft is module-scoped: the
// bubble unmounts on Escape, and a baseline lost there would make the issue's
// own text look like the operator's on the next open.

function reset() {
  draft.value = { title: '', body: '' }
  error.value = ''
}

// Both logout paths navigate with `window.location`, which takes this module
// with them — except the one guarded against a redirect loop on /login. Reset
// there too, so a draft cannot outlive the account that typed it.
api.onUnauthorized(() => {
  open.value = false
  reset()
})

/**
 * useQuickCapture — capture an idea from anywhere, without leaving the page.
 *
 * The whole point is that the surface you were on is never navigated away
 * from, so this owns no route and no feed: it POSTs to `/api/tasks` (the
 * browser's JWT route, never the machine-key `/capture` one) and announces
 * the new id on the window.
 */
export function useQuickCapture() {
  async function create() {
    error.value = draftError(draft.value)
    if (error.value) return null
    const result = await api.post('/tasks', draftBody(draft.value))
    if (result.ok === false) {
      error.value = result.msg || 'The server refused this idea.'
      return null
    }
    error.value = ''
    return result.task
  }

  function captured(task) {
    reset()
    open.value = false
    window.dispatchEvent(new CustomEvent(TASK_CAPTURED_EVENT, { detail: { id: task.id } }))
  }

  return { open, draft, error, create, captured, reset }
}
