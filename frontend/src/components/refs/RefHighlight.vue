<script setup>
// The references in a body textarea, tinted where they sit — what
// `ComposerHighlight` does for `@path` in the follow-up composer.
//
// A mirror of the text with every glyph transparent, laid on the field's
// padding box so it wraps where the field wraps; only the tint under each
// `[label](kind:key)` shows. It lies over the field rather than behind it,
// because the body fields keep their own opaque surface: the tint is
// translucent, so text, caret and selection read through it, and with no
// pointer events the field never knows it is there. A reference is what the
// grammar says it is, so one inside code is not tinted.
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { MIRRORED } from '../../utils/caretPoint.js'
import { parseRefs } from '../../utils/refs.js'

const props = defineProps({
  text: { type: String, default: '' },
  // The `<textarea>` this mirrors; a sibling in the same positioned box.
  field: { type: Object, default: null },
})

const mirror = ref(null)
let observer = null

const parts = computed(() => {
  const text = props.text
  const out = []
  let at = 0
  for (const found of parseRefs(text)) {
    if (found.start > at) out.push({ text: text.slice(at, found.start), ref: false })
    out.push({ text: text.slice(found.start, found.end), ref: true })
    at = found.end
  }
  // A trailing newline has no line box of its own here, but does in the
  // field; the space keeps the two scroll heights in step.
  out.push({ text: `${text.slice(at)} `, ref: false })
  return out
})

const hasRefs = computed(() => parts.value.some((part) => part.ref))

function follow() {
  const field = props.field
  const el = mirror.value
  if (!field || !el) return
  el.scrollTop = field.scrollTop
  el.scrollLeft = field.scrollLeft
}

// The field's padding box, less any scrollbar: `clientWidth` is the width
// its lines actually wrap in.
function measure() {
  const field = props.field
  const el = mirror.value
  if (!field || !el) return
  const style = window.getComputedStyle(field)
  for (const prop of MIRRORED) el.style[prop] = style[prop]
  Object.assign(el.style, {
    left: `${field.offsetLeft + field.clientLeft}px`,
    top: `${field.offsetTop + field.clientTop}px`,
    width: `${field.clientWidth}px`,
    height: `${field.clientHeight}px`,
    boxSizing: 'border-box',
    borderStyle: 'none',
    borderWidth: '0',
  })
  follow()
}

function watchField(field, old) {
  old?.removeEventListener('scroll', follow)
  observer?.disconnect()
  if (!field) return
  field.addEventListener('scroll', follow)
  observer = new ResizeObserver(measure)
  observer.observe(field)
}

// Text the field re-wrapped can move its scroll offset without a size
// change; the mirror re-reads both once the new text is laid out. A body
// with nothing to tint costs no layout read at all.
watch(() => props.text, () => { if (hasRefs.value) nextTick(measure) })
watch(() => props.field, watchField)
onMounted(() => {
  watchField(props.field, null)
  measure()
})
onBeforeUnmount(() => watchField(null, props.field))
</script>

<template>
  <div
    v-show="hasRefs"
    ref="mirror"
    class="ref-hl"
    aria-hidden="true"
    data-testid="ref-highlight"
  ><span
    v-for="(part, i) in parts"
    :key="i"
    :class="part.ref ? 'ref-hl-ref' : null"
  >{{ part.text }}</span></div>
</template>

<style scoped>
.ref-hl {
    position: absolute;
    overflow: hidden;
    pointer-events: none;
    user-select: none;
    color: transparent;
    white-space: pre-wrap;
}

/* Cloned so a reference that wraps keeps its fill on both lines instead of
   painting one box across the gap. */
.ref-hl-ref {
    border-radius: 3px;
    background: color-mix(in srgb, var(--color-primary) 16%, transparent);
    box-decoration-break: clone;
    -webkit-box-decoration-break: clone;
}
</style>
