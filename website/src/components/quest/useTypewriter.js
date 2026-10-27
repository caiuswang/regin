import { ref, onBeforeUnmount } from 'vue'

export function useTypewriter(msPerChar = 18) {
  const typed = ref('')
  let timer = null

  function type(text) {
    clearInterval(timer)
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      typed.value = text
      return
    }
    let i = 0
    typed.value = ''
    timer = setInterval(() => {
      typed.value = text.slice(0, ++i)
      if (i >= text.length) clearInterval(timer)
    }, msPerChar)
  }

  onBeforeUnmount(() => clearInterval(timer))
  return { typed, type }
}
