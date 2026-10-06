<script setup>
import { computed } from 'vue'
import { SPRITES, spriteRuns, spriteSize } from './sprites.js'

const props = defineProps({
  name: { type: String, required: true },
  // Screen pixels per sprite pixel. Integers only: crispEdges on a fractional
  // scale draws uneven columns.
  scale: { type: Number, default: 1 },
  // Unset, the sprite is decoration and hidden from assistive tech.
  label: { type: String, default: '' },
})

const sprite = computed(() => SPRITES[props.name])
const size = computed(() => (sprite.value ? spriteSize(sprite.value) : { w: 0, h: 0 }))
const runs = computed(() => (sprite.value ? spriteRuns(sprite.value) : []))
</script>

<template>
  <svg
    v-if="sprite"
    :width="size.w * scale"
    :height="size.h * scale"
    :viewBox="`0 0 ${size.w} ${size.h}`"
    shape-rendering="crispEdges"
    :role="label ? 'img' : undefined"
    :aria-label="label || undefined"
    :aria-hidden="label ? undefined : 'true'"
    :data-sprite="name"
  >
    <rect
      v-for="(r, i) in runs"
      :key="i"
      :x="r.x"
      :y="r.y"
      :width="r.w"
      height="1"
      :data-px="r.key"
      :style="{ fill: `var(--pl-px-${r.key})` }"
    />
  </svg>
</template>
