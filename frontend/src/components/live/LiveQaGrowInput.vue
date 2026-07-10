<script setup>
// Auto-growing single-field text entry for the QA answer surfaces: renders as
// one line when empty and grows with the typed text (capped by CSS max-height,
// then scrolls) so a long answer stays fully visible before it is sent.
// A textarea, but Enter never inserts a newline — delivery flattens newlines
// to spaces anyway (sanitize_text), so WYSIWYG means keeping the text one
// logical line. A bare Enter is surfaced to the parent instead; a modified
// Enter (Shift/Ctrl/Cmd/Alt) is swallowed, and an IME composition commit is
// left alone entirely so CJK input can't fire a premature submit.
import { nextTick, onMounted, ref, watch } from 'vue'

const props = defineProps({
  modelValue: { type: String, default: '' },
  placeholder: { type: String, default: '' },
  ariaLabel: { type: String, default: '' },
  disabled: { type: Boolean, default: false },
  testid: { type: String, default: '' },
})
const emit = defineEmits(['update:modelValue', 'enter'])

const el = ref(null)
function resize() {
  const t = el.value
  if (!t) return
  t.style.height = 'auto'
  t.style.height = `${t.scrollHeight}px`
}
let fromInput = false
function onInput(e) {
  fromInput = true
  emit('update:modelValue', e.target.value)
  resize()
}
function onEnter(e) {
  if (e.isComposing || e.keyCode === 229) return
  e.preventDefault()
  if (!(e.shiftKey || e.ctrlKey || e.metaKey || e.altKey)) emit('enter')
}
// External writes (switching questions, restore-on-toggle) re-measure too;
// the round-trip of our own input event already resized, so skip it.
watch(() => props.modelValue, () => {
  if (fromInput) { fromInput = false; return }
  nextTick(resize)
})
onMounted(resize)
</script>

<template>
  <textarea
    ref="el"
    class="live-qa-free-input live-qa-grow"
    rows="1"
    :value="modelValue"
    :placeholder="placeholder"
    :aria-label="ariaLabel"
    :disabled="disabled"
    :data-testid="testid || undefined"
    @input="onInput"
    @keydown.enter="onEnter"
  ></textarea>
</template>
