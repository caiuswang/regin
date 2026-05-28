import { reactive } from 'vue'

const state = reactive({ message: '', type: 'success' })
let timer = null

function flash(message, type = 'success') {
  state.message = message
  state.type = type
  clearTimeout(timer)
  timer = setTimeout(() => { state.message = '' }, 4000)
}

function clear() {
  state.message = ''
  clearTimeout(timer)
}

export function useFlash() {
  return { state, flash, clear }
}
