<script setup>
// Pixel ChoiceField: the same pick as a row of keys, one per option — a
// radiogroup, so the arrows move the choice and Tab leaves the group. Every
// option is on screen, which is the point: five priorities or two launch
// modes are read at a glance instead of behind a menu.
import { nextTick, ref } from 'vue'
import Button from '../../../../components/ui/Button.vue'
import PixelSprite from '../PixelSprite.vue'

const props = defineProps({
  modelValue: { type: [String, Number, Boolean, null], default: '' },
  options: { type: Array, default: () => [] },
})
const emit = defineEmits(['update:modelValue'])

const keys = ref([])
const checkedIndex = () => props.options.findIndex(o => o.value === props.modelValue)
// With nothing checked the first key takes the Tab stop, or the group would
// be unreachable from the keyboard.
const tabStop = i => (i === Math.max(0, checkedIndex()) ? 0 : -1)

async function step(from, delta) {
  const n = props.options.length
  if (!n) return
  const next = (from + delta + n) % n
  emit('update:modelValue', props.options[next].value)
  await nextTick()
  keys.value[next]?.$el?.focus()
}

function onKeydown(e, i) {
  if (e.key === 'ArrowRight' || e.key === 'ArrowDown') { e.preventDefault(); step(i, 1) }
  else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') { e.preventDefault(); step(i, -1) }
}
</script>

<template>
  <div role="radiogroup" data-pl-part="choice">
    <Button
      v-for="(o, i) in options"
      :key="String(o.value)"
      ref="keys"
      variant="ghost"
      size="sm"
      role="radio"
      data-pl-part="choice-key"
      :data-value="String(o.value)"
      :aria-checked="o.value === modelValue ? 'true' : 'false'"
      :tabindex="tabStop(i)"
      @click="emit('update:modelValue', o.value)"
      @keydown="onKeydown($event, i)"
    >
      <span data-pl-part="choice-line">
        <PixelSprite v-if="o.glyph" :name="o.glyph" :scale="2" />
        <span data-pl-part="choice-label">{{ o.label }}</span>
      </span>
      <span v-if="o.hint" data-pl-part="choice-hint">{{ o.hint }}</span>
    </Button>
  </div>
</template>
