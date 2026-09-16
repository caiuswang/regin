<script setup>
// What you are looking at, when you arrived here by following a key.
//
// `fix_item` rendered nothing at all: the list narrowed to one tracker item
// and the page said so nowhere, so a reader saw four defects under the word
// "Defects" and had no way to know the other two hundred were being withheld.
// A scope with no banner is a filtered list wearing an unfiltered heading.
defineProps({
  featureId: { type: String, default: '' },
  fixItem: { type: String, default: '' },
  // Read off the rows, never composed from the id: `features.title` is the
  // authority for what a feature is called, and a banner must not invent a
  // second name for it.
  featureTitle: { type: String, default: '' },
})
</script>

<template>
  <p v-if="featureId" class="scoped" data-testid="defects-scoped-to">
    Scoped to
    <router-link :to="`/features/${featureId}`"><strong>{{
      featureTitle || `#${featureId}` }}</strong></router-link> ·
    <router-link to="/defects">every defect</router-link>
  </p>
  <p v-else-if="fixItem" class="scoped" data-testid="defects-scoped-to-fix-item">
    Scoped to the defects
    <router-link :to="`/tasks/${fixItem}`"><strong>task #{{ fixItem }}</strong></router-link>
    is meant to close ·
    <router-link to="/defects">every defect</router-link>
  </p>
</template>

<style scoped>
.scoped { font-size: 11.5px; color: var(--color-fg-muted); }
.scoped strong { font-family: var(--font-mono, monospace); }
</style>
