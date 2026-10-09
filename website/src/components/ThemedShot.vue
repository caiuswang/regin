<script setup>
defineProps({
  light: { type: Object, required: true },
  dark: { type: Object, required: true },
  width: { type: Number, required: true },
  height: { type: Number, required: true },
  alt: { type: String, required: true },
})
</script>

<template>
  <figure class="shot-frame shot-frame-sm">
    <picture v-for="(shot, theme) in { light, dark }" :key="theme" :class="`shot-${theme}`">
      <source type="image/webp" :srcset="shot.webp" :width="width" :height="height" />
      <img :src="shot.png" :width="width" :height="height" loading="lazy" :alt="alt" />
    </picture>
    <figcaption v-if="$slots.default"><slot /></figcaption>
  </figure>
</template>
