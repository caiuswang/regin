<script setup>
// The roster's preview body. A session previews the same way wherever it is
// highlighted — relations, waiting card, latest prompts, tail — so this is the
// search palette's entity preview fed by the walk the palette started from the
// roster row, plus the one thing the roster says that search does not: what an
// empty roster means.
import SearchEntityPreview from './SearchEntityPreview.vue'

defineProps({
  stack: { type: Array, default: () => [] },
  pickKey: { type: String, default: null },
  actions: { type: Object, default: null },
  copied: { type: Boolean, default: false },
})
defineEmits(['follow', 'pop-to', 'act', 'go', 'retry'])
</script>

<template>
  <SearchEntityPreview
    v-if="stack.length"
    :stack="stack"
    :pick-key="pickKey"
    :actions="actions"
    :copied="copied"
    @follow="k => $emit('follow', k)"
    @pop-to="d => $emit('pop-to', d)"
    @act="w => $emit('act', w)"
    @go="h => $emit('go', h)"
    @retry="$emit('retry')"
  />
  <div v-else class="search-preview switcher-preview">
    <div class="switcher-explainer">
      <h2>Session roster</h2>
      <p>
        Every session regin has traced recently, with what it is doing right
        now. Moving the selection previews it; <strong>↵</strong> attaches.
      </p>
    </div>
  </div>
</template>

<style scoped>
.switcher-preview {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  background: var(--color-background);
}

.switcher-explainer { padding: 1.5rem 1.375rem; }

.switcher-explainer h2 {
  font-size: var(--sp-t-body);
  font-weight: 700;
  color: var(--color-fg);
  margin: 0 0 0.375rem;
}

.switcher-explainer p {
  font-size: var(--sp-t-body);
  line-height: var(--sp-lh-body);
  color: var(--color-fg-subtle);
}
</style>
