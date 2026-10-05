<script setup>
// A touched file, read as code rather than as flat lines: the same DiffBlock
// the trace renders an edit with, so the window is numbered, syntax-coloured
// and unreflowed.
import { computed } from 'vue'
import DiffBlock from '../DiffBlock.vue'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import { vHighlightText } from '../../directives/highlightText.js'

const props = defineProps({
  doc: { type: Object, required: true },
  query: { type: String, default: '' },
  // The line the palette's hit cursor is parked on; 0 when there is no hit.
  hitLine: { type: Number, default: 0 },
})
const emit = defineEmits(['open-file'])

// DiffBlock consumes the first character of every line as its marker. A file
// body carries none, so each line is declared context rather than losing a
// character to the gutter.
const asDiff = computed(() => String(props.doc?.body || '')
  .split('\n')
  .map(line => ` ${line}`)
  .join('\n'))

const filePath = computed(() => props.doc?.sub || props.doc?.id || '')

function openFile() {
  emit('open-file', props.doc.source, props.hitLine ? `#L${props.hitLine}` : '')
}
</script>

<template>
  <div class="preview-file">
    <div v-if="doc.source" class="preview-file-bar">
      <Button variant="secondary" size="sm" data-testid="search-open-file" @click="openFile">
        <Icon name="file" :size="13" />Open file<span v-if="hitLine" class="preview-file-line">at line {{ hitLine }}</span>
      </Button>
    </div>
    <div v-highlight-text="query" class="preview-diff">
      <DiffBlock numbered flush :diff="asDiff" :file-path="filePath" />
    </div>
  </div>
</template>

<style scoped>
.preview-file-bar {
  display: flex;
  justify-content: flex-end;
  margin-bottom: 0.5rem;
}
.preview-file-line { color: var(--color-fg-subtle); font-weight: 400; }
.preview-diff :deep(.diff-block) {
  background: var(--code-bg);
  border-radius: 11px;
  margin: 0;
}
</style>
