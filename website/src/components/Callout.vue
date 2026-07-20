<script setup>
import SiteIcon from './SiteIcon.vue'

defineProps({
  tone: { type: String, default: 'info' },
})
</script>

<template>
  <div class="callout" :class="tone === 'warn' ? 'callout-warn' : 'callout-info'">
    <SiteIcon :name="tone === 'warn' ? 'alert-triangle' : 'info'" :size="18" />
    <p><slot /></p>
  </div>
</template>
