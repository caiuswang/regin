<script setup>
// Renders one `{ pre, hit, post, has }` from utils/searchDocs.js.
//
// The `v-if="has"` is the whole point: a zero-length <mark> paints as a 2px
// amber sliver with no text in it, and at some query length every row would
// grow one. Gating here means no caller can forget.
defineProps({
  part: { type: Object, required: true },
  tone: { type: String, default: 'strong' },
})
</script>

<template>
  <span class="search-mark">{{ part.pre
    }}<mark v-if="part.has" :class="tone === 'soft' ? 'is-soft' : 'is-strong'">{{ part.hit }}</mark>{{ part.post }}</span>
</template>

<style scoped>
.search-mark { min-width: 0; }

mark {
  border-radius: 3px;
  padding: 0 1px;
  font: inherit;
}

mark.is-strong {
  background: var(--search-hit-bg);
  color: var(--search-hit-fg);
}

mark.is-soft {
  background: var(--search-hit-soft-bg);
  color: var(--search-hit-soft-fg);
}
</style>
