<script setup>
defineProps({
  code: { type: String, required: true },
})
</script>

<template>
  <pre><code>{{ code }}</code></pre>
</template>
