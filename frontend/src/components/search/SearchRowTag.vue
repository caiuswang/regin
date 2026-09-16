<script setup>
import { computed } from 'vue'
import { KIND_META, categoryTint, diffTagParts } from '../../utils/searchDocs.js'

const props = defineProps({
  kind: { type: String, default: 'span' },
  tag: { type: String, default: '' },
})

const style = computed(() => (KIND_META[props.kind] || KIND_META.span).tagStyle)

// A file row whose diff stats are missing has no ± to split, so it degrades to
// the plain tag rather than rendering a meaningless "+0 −0".
const diff = computed(() => (style.value === 'diff' ? diffTagParts(props.tag) : null))
</script>

<template>
  <span v-if="diff" class="tag-diff">
    <span class="tag-add">{{ diff.adds }}</span>
    <span class="tag-del">{{ diff.dels }}</span>
  </span>

  <span
    v-else-if="style === 'category'"
    class="tag-chip is-category"
    :style="{ '--cat': categoryTint(tag) }"
  >{{ tag }}</span>

  <span v-else-if="style === 'chip'" class="tag-chip is-space">{{ tag }}</span>

  <span v-else class="tag-plain">{{ tag }}</span>
</template>

<style scoped>
.tag-diff,
.tag-chip,
.tag-plain {
  flex-shrink: 0;
  font-size: 0.65625rem;
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
  white-space: nowrap;
}

.tag-diff { display: inline-flex; gap: 0.375rem; font-weight: 600; }

.tag-add { color: var(--search-diff-add); }

.tag-del { color: var(--search-diff-del); }

.tag-chip {
  display: inline-block;
  font-weight: 600;
  border-radius: 6px;
  padding: 1px 6px;
  max-width: 7rem;
  overflow: hidden;
  text-overflow: ellipsis;
}

.is-category {
  color: var(--cat);
  background: color-mix(in srgb, var(--cat) 12%, transparent);
}

.is-space {
  color: var(--search-accent-pattern);
  background: color-mix(in srgb, var(--search-accent-pattern) 12%, transparent);
}

.tag-plain {
  color: var(--color-fg-subtle);
  max-width: 7rem;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
