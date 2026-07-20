<script setup>
defineProps({
  src: { type: String, required: true },
  webp: { type: String, required: true },
  width: { type: Number, required: true },
  height: { type: Number, required: true },
  alt: { type: String, required: true },
})
</script>

<template>
  <figure class="shot-frame shot-frame-sm">
    <picture>
      <source type="image/webp" :srcset="webp" :width="width" :height="height" />
      <img :src="src" :width="width" :height="height" loading="lazy" :alt="alt" />
    </picture>
    <figcaption v-if="$slots.default"><slot /></figcaption>
  </figure>
</template>
