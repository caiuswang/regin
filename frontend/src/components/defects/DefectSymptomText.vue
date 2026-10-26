<script setup>
// The symptom as typed, with each `![](image:N)` drawn as the picture it cites.
import { computed } from 'vue'
import RefInlineText from '../refs/RefInlineText.vue'
import { splitImageRefs } from '../../utils/taskBodyImages'

const props = defineProps({
  text: { type: String, default: '' },
  // `useAttachedImages` entries; null where the row draws no pictures (a
  // clamped first line), which leaves every reference as typed.
  items: { type: Array, default: null },
  // The `idx` the defect carries, so a picture still loading is not called
  // detached.
  attached: { type: Array, default: () => [] },
})

const segments = computed(() => (props.items ? splitImageRefs(props.text) : []))
const urlOf = (idx) => props.items?.find(item => item.idx === idx)?.url
</script>

<template>
  <template v-if="segments.some(seg => seg.idx !== undefined)">
    <template v-for="(seg, i) in segments" :key="i">
      <RefInlineText v-if="seg.idx === undefined" :text="seg.text" />
      <img
        v-else-if="urlOf(seg.idx)" class="dst-img" :src="urlOf(seg.idx)"
        :alt="seg.alt || `image ${seg.idx}`" :data-idx="seg.idx"
        data-testid="defect-symptom-image"
      >
      <em v-else class="dst-missing">{{ attached.includes(seg.idx) ? `image:${seg.idx}` : `(image:${seg.idx} — no longer attached)` }}</em>
    </template>
  </template>
  <RefInlineText v-else :text="text" />
</template>

<style scoped>
.dst-img {
    display: block; max-width: min(100%, 32rem); max-height: 20rem; height: auto;
    margin: 0.25rem 0; border: 1px solid var(--color-border); border-radius: var(--radius-md);
}
.dst-missing { color: var(--color-fg-muted); }
</style>
