<script setup>
import Button from '../ui/Button.vue'
import { jumpTo, sectionNumber } from './sections.js'

defineProps({
  level: { type: String, required: true },
  hud: { type: String, required: true },
  title: { type: String, required: true },
  lead: { type: String, default: '' },
  sections: { type: Array, default: null },
})
</script>

<template>
  <article class="doc-card">
    <div class="hud-bar doc-card-hud">
      <span class="doc-card-label"><span class="hud-accent">{{ level }}</span> · {{ hud }}</span>
      <span v-if="sections" class="doc-card-status">STAGES <b>{{ sections.length }}</b> · READY</span>
    </div>
    <div class="doc-card-body">
      <h1 class="doc-card-title">{{ title }}</h1>
      <p v-if="lead" class="doc-card-lead">{{ lead }}</p>
      <slot />
      <nav v-if="sections?.length" class="stage-select" aria-label="On this page">
        <span class="stage-select-label">STAGE SELECT</span>
        <div class="stage-select-chips">
          <Button
            v-for="(section, i) in sections" :key="section.id"
            variant="bare" class="stage-chip" @click="jumpTo(section.id)"
          ><span class="stage-chip-n">{{ sectionNumber(i) }}</span>{{ section.label }}</Button>
        </div>
      </nav>
    </div>
  </article>
</template>
