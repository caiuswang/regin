<script setup>
defineProps({
  stage: { type: Number, required: true },
  title: { type: String, required: true },
})
</script>

<template>
  <h3 class="pl-stage"><span class="pl-stage-n">STAGE {{ stage }}</span>{{ title }}</h3>
</template>
