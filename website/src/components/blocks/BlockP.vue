<script setup>
import InlineText from './InlineText.vue'

defineProps({
  t: { type: String, required: true },
})
</script>

<template>
  <p class="block-p"><InlineText :text="t" /></p>
</template>
