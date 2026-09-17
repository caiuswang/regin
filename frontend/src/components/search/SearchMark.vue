<script setup>
// Renders one `{ pre, hit, post, has }` from utils/searchDocs.js.
//
// The `v-if="has"` is the whole point: a zero-length <mark> paints as a 2px
// sliver with no text in it, and at some query length every row would grow
// one. Gating here means no caller can forget.
defineProps({
  part: { type: Object, required: true },
  tone: { type: String, default: 'strong' },
  // The one hit the preview's stepper is parked on. Exactly one mark on the
  // pane may carry this, or "current" stops meaning anything.
  current: { type: Boolean, default: false },
})
</script>

<template>
  <span class="search-mark">{{ part.pre
    }}<mark
      v-if="part.has"
      :class="[tone === 'soft' ? 'is-soft' : 'is-strong', { 'is-current': current }]"
    >{{ part.hit }}</mark>{{ part.post }}</span>
</template>

<style scoped>
.search-mark { min-width: 0; }

mark {
  border-radius: 2px;
  padding: 0 1px;
  font: inherit;
  font-weight: 650;
}

mark.is-strong {
  background: var(--search-hit-bg);
  color: var(--search-hit-fg);
  box-shadow: inset 0 -1.5px 0 var(--search-hit-rule);
}

mark.is-soft {
  background: var(--search-hit-soft-bg);
  color: var(--search-hit-soft-fg);
}

/* The stepper's position, not just another hit: a ring, because the tint
   alone is already spent distinguishing hit from prose. */
mark.is-current {
  background: var(--search-hit-cur-bg);
  color: var(--color-fg);
  box-shadow: 0 0 0 2px var(--search-hit-cur-ring);
}
</style>
