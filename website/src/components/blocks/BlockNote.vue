<script setup>
import InlineText from './InlineText.vue'

defineProps({
  tone: { type: String, required: true, validator: (v) => v === 'WARN' || v === 'TIP' },
  t: { type: String, required: true },
})
</script>

<template>
  <div class="block-note" :class="tone === 'WARN' ? 'is-warn' : 'is-tip'">
    <span class="block-note-tone">{{ tone }}</span>
    <p class="block-note-text"><InlineText :text="t" /></p>
  </div>
</template>
