<script setup>
import { computed } from 'vue'
import { worldMap } from '../../content/docs.js'

const props = defineProps({
  current: { type: String, default: null },
  section: { type: String, default: '' },
})

const groups = computed(() => worldMap(props.section))
</script>

<template>
  <aside class="world-map" aria-label="World map">
    <div class="world-map-title">WORLD MAP</div>
    <div v-for="group in groups" :key="group.label" class="world-map-group">
      <div class="world-map-label">{{ group.label }}</div>
      <RouterLink
        v-for="page in group.pages" :key="page.path" :to="page.path"
        class="world-map-entry" :class="{ 'is-sub': page.parent }"
        :aria-current="page.path === current ? 'page' : undefined"
      >
        <span class="world-map-code">{{ page.code }}</span>
        <span class="world-map-name">{{ page.title }}</span>
      </RouterLink>
    </div>
  </aside>
</template>
