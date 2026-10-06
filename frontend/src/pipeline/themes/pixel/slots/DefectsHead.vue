<script setup>
// Pixel head of the All-defects list (3-3): a ◀ FEATURES key back to the tab
// it lights, the chapter, and the server's match count. Classic has no head —
// its count is the toolbar's.
defineProps({
  total: { type: Number, default: null },
  backTo: { type: [String, Object], default: '/features' },
})
</script>

<template>
  <div data-pl-part="panel-head" data-testid="defects-head">
    <router-link :to="backTo" data-pl-part="panel-link" data-pl-back="" data-testid="defects-back">Features</router-link>
    <span data-pl-part="panel-chapter">3-3</span>
    <h2 data-pl-part="panel-title">All defects</h2>
    <span data-pl-part="panel-meta">Open or closed, triaged or not</span>
    <span
      v-if="total != null"
      data-pl-part="panel-meta"
      data-pl-end=""
      data-testid="defects-head-count"
    >{{ total }} {{ total === 1 ? 'defect' : 'defects' }}</span>
  </div>
</template>
