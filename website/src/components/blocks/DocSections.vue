<script setup>
import DocBlock from './DocBlock.vue'
import { sectionNumber, useDocSections } from '../docs/sections.js'

// sections: [title, DocBlock[]][] — the prototype's CONTENT[<id>] shape.
const props = defineProps({
  sections: { type: Array, required: true },
})

function anchor(title) {
  return `s-${title.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')}`
}

useDocSections(() => props.sections.map(([title]) => ({ id: anchor(title), label: title })))
</script>

<template>
  <div class="doc-sections">
    <section v-for="([title, blocks], i) in sections" :id="anchor(title)" :key="title" class="doc-section">
      <h2 class="doc-section-title"><span class="doc-section-n">{{ sectionNumber(i) }}</span>{{ title }}</h2>
      <DocBlock v-for="(block, bi) in blocks" :key="bi" :block="block" />
    </section>
  </div>
</template>
