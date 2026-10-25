import { onBeforeUnmount, watch } from 'vue'

// A textarea as tall as its text, so a long body scrolls with the page rather
// than inside a box. Height follows the events that change it: typing (the
// `input` the caller wires to `fit`), a value set from outside, and the
// field's width changing — a wider column re-wraps the same text into fewer
// lines without any input event.
//
// Measuring means collapsing the field to `auto` for a moment. Collapsed, the
// page would get shorter and the scroller would clamp its scrollTop, so the
// field's parent holds its current height while the field is measured.
export function useAutogrow(field, value) {
  let width = 0
  let observer = null

  function fit() {
    const el = field.value
    if (!el) return
    const holder = el.parentElement
    const held = holder?.style.minHeight ?? ''
    if (holder) holder.style.minHeight = `${holder.offsetHeight}px`
    const style = getComputedStyle(el)
    const border = parseFloat(style.borderTopWidth) + parseFloat(style.borderBottomWidth)
    el.style.height = 'auto'
    el.style.height = `${el.scrollHeight + border}px`
    if (holder) holder.style.minHeight = held
  }

  function onResize(entries) {
    const next = entries[0]?.contentRect.width ?? 0
    if (next === width) return
    width = next
    fit()
  }

  watch(field, (el, old) => {
    observer?.disconnect()
    observer = null
    if (old) old.style.height = ''
    if (!el) return
    width = el.clientWidth
    fit()
    observer = new ResizeObserver(onResize)
    observer.observe(el)
  }, { flush: 'post', immediate: true })

  watch(value, (next) => {
    if (field.value && field.value.value === next) fit()
  }, { flush: 'post' })

  onBeforeUnmount(() => observer?.disconnect())

  return { fit }
}
