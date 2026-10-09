<script setup>
import { levelLabel } from '../../content/docs.js'
import WorldMap from './WorldMap.vue'
import DocCard from './DocCard.vue'
import PrevNext from './PrevNext.vue'
import { provideDocSections } from './sections.js'

defineProps({
  page: { type: Object, default: null },
  path: { type: String, required: true },
})

const sections = provideDocSections()
</script>

<template>
  <div class="docs-wrap">
    <WorldMap :current="page?.path" :section="path" />
    <div class="docs-main">
      <DocCard
        v-if="page" :key="page.path"
        :level="levelLabel(page)" :hud="page.hud" :title="page.title" :lead="page.lead" :sections="sections"
      />
      <div class="docs-content">
        <slot />
      </div>
      <PrevNext v-if="page" :page="page" />
    </div>
  </div>
</template>
