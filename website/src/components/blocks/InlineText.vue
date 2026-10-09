<script setup>
import { computed } from 'vue'
import { segments } from './inline.js'

const props = defineProps({
  text: { type: String, required: true },
})

const segs = computed(() => segments(props.text))
</script>

<template>
  <template v-for="(seg, i) in segs" :key="i">
    <code v-if="seg.kind === 'code'" class="inline-code">{{ seg.t }}</code>
    <b v-else-if="seg.kind === 'bold'" class="inline-bold">{{ seg.t }}</b>
    <RouterLink v-else-if="seg.kind === 'link' && seg.href.startsWith('/')" :to="seg.href">{{ seg.t }}</RouterLink>
    <a v-else-if="seg.kind === 'link'" :href="seg.href">{{ seg.t }}</a>
    <template v-else>{{ seg.t }}</template>
  </template>
</template>
