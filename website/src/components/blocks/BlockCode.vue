<script setup>
defineProps({
  t: { type: String, required: true },
})
</script>

<template>
  <pre class="block-code">{{ t }}</pre>
</template>
