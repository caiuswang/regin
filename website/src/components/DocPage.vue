<script setup>
import { useDocSections } from './docs/sections.js'

// Adapter for the pre-pixel views until they become DocBlock lists: the page
// card already shows the title and lead, so only the body and the section
// list are taken from here.
const props = defineProps({
  title: { type: String, default: '' },
  lead: { type: String, default: '' },
  toc: { type: Array, default: () => [] },
})

useDocSections(() => props.toc)
</script>

<template>
  <div class="doc-legacy">
    <slot />
  </div>
</template>
