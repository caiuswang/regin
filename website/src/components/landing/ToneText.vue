<script setup>
// A run of segments: a plain string, or a `[text, tone]` pair rendered in
// `tag` with the tone's colour class.
defineProps({
  segs: { type: Array, required: true },
  tag: { type: String, default: 'span' },
})
</script>

<template><template v-for="(seg, i) in segs" :key="i"><component :is="tag" v-if="Array.isArray(seg)" :class="`tone-${seg[1]}`">{{ seg[0] }}</component><template v-else>{{ seg }}</template></template></template>
