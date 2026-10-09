<script setup>
defineProps({
  t: { type: String, required: true },
})
</script>

<template>
  <h3 class="block-h3">{{ t }}</h3>
</template>
