import { reactive } from 'vue'

// `link` is `{ to, label }` — the object the action just touched, so the
// feedback is a way to it rather than only a sentence about it — or
// `{ action, label }` when the way there is a step on this page, not a route.
const state = reactive({ message: '', type: 'success', link: null })
let timer = null

function flash(message, type = 'success', { link = null } = {}) {
  state.message = message
  state.type = type
  state.link = link
  clearTimeout(timer)
  timer = setTimeout(() => { state.message = ''; state.link = null }, link ? 8000 : 4000)
}

function clear() {
  state.message = ''
  state.link = null
  clearTimeout(timer)
}

export function useFlash() {
  return { state, flash, clear }
}
