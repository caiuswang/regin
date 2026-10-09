<script setup>
defineProps({
  src: { type: String, required: true },
  alt: { type: String, required: true },
  cap: { type: String, default: '' },
  width: { type: Number, default: 2880 },
  height: { type: Number, default: 1800 },
})
</script>

<template>
  <figure class="block-shot">
    <img :src="src" :alt="alt" :width="width" :height="height" loading="lazy" />
    <figcaption v-if="cap || $slots.default"><slot>{{ cap }}</slot></figcaption>
  </figure>
</template>
