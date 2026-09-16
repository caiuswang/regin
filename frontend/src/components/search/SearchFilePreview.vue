<script setup>
// A touched file, read as code rather than as flat lines: the same DiffBlock
// the trace renders an edit with, so the window is numbered, syntax-coloured
// and unreflowed.
import { computed } from 'vue'
import DiffBlock from '../DiffBlock.vue'
import { vHighlightText } from '../../directives/highlightText.js'

const props = defineProps({
  doc: { type: Object, required: true },
  query: { type: String, default: '' },
})

// DiffBlock consumes the first character of every line as its marker. A file
// body carries none, so each line is declared context rather than losing a
// character to the gutter.
const asDiff = computed(() => String(props.doc?.body || '')
  .split('\n')
  .map(line => ` ${line}`)
  .join('\n'))

const filePath = computed(() => props.doc?.sub || props.doc?.id || '')
</script>

<template>
  <div v-highlight-text="query" class="preview-diff">
    <DiffBlock numbered flush :diff="asDiff" :file-path="filePath" />
  </div>
</template>

<style scoped>
.preview-diff :deep(.diff-block) {
  background: var(--code-bg);
  border-radius: 11px;
  margin: 0;
}
</style>
