<script setup>
/**
 * WordDiffInline — a flowing word-level diff of two short text values
 * (intent, label, any scalar field). Shares the jsdiff-based word-diff
 * core with WikiContentDiff's Inline view: unchanged words render plain,
 * added words highlight green, removed words strike through red.
 */
import { computed } from 'vue'
import { wordSegments } from '../../utils/wordDiff'

const props = defineProps({
  before: { type: String, default: '' },
  after: { type: String, default: '' },
})

const segments = computed(() => wordSegments(props.before, props.after))
</script>

<template>
  <span class="wdiff" data-testid="word-diff-inline"><span
    v-for="(seg, i) in segments"
    :key="i"
    :class="seg.type === 'context' ? '' : `wdiff__seg wdiff__seg--${seg.type}`"
  >{{ seg.text }}</span></span>
</template>

<style scoped>
.wdiff { word-break: break-word; }
.wdiff__seg { border-radius: 0.1875rem; padding: 0 0.0625rem; }
.wdiff__seg--add {
  background: var(--color-emerald-200);
  color: var(--color-emerald-900);
}
.wdiff__seg--remove {
  background: var(--color-red-200);
  color: var(--color-red-900);
  text-decoration: line-through;
}
</style>
