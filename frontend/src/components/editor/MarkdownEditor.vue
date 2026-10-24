<script setup>
// A markdown body edited as source beside a live render of the same bytes.
//
// Source-first, not WYSIWYG: these columns are read and written by agents, and
// a CommonMark round-trip of 544 stored bodies rewrote 89% of them (tables
// re-aligned, list markers and emphasis normalised, comments dropped). So the
// textarea's value is what goes up, verbatim, and the preview only reads it —
// through the same `MarkdownContent` the saved view uses, so what the preview
// shows is what the page shows after Save.
//
// The host's attributes and listeners (test id, aria-label, paste, drop) land
// on the textarea, and the textarea is exposed as `textarea` for
// `utils/caret.js`, so a host swaps this in for a bare `RefTextarea`.
import { computed, onBeforeUnmount, ref } from 'vue'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import MarkdownContent from '../MarkdownContent.vue'
import RefTextarea from '../refs/RefTextarea.vue'
import { resolveImageRefs } from '../../utils/taskBodyImages'

defineOptions({ inheritAttrs: false })
const props = defineProps({
  modelValue: { type: String, default: '' },
  project: { type: Number, default: null },
  feature: { type: Number, default: null },
  images: { type: Array, default: () => [] },
  // Declared so a host's uploader never falls through onto the textarea as
  // an attribute; the host's own paste/drop handlers call it.
  attach: { type: Function, default: null },
  previewTestid: { type: String, default: null },
})
const emit = defineEmits(['update:modelValue'])

const MODES = [
  { id: 'source', label: 'Source', icon: 'pencil' },
  { id: 'split', label: 'Split', icon: 'board-columns' },
  { id: 'preview', label: 'Preview', icon: 'eye' },
]
const STORAGE_KEY = 'regin_editor_mode'
const NARROW = '(max-width: 767.98px)'

function readStored() {
  try {
    const value = window.localStorage.getItem(STORAGE_KEY)
    return MODES.some((m) => m.id === value) ? value : null
  } catch {
    return null
  }
}

// Two halves do not fit a phone, so a narrow viewport edits source and its
// choices stay with this edit: writing them back would replace the
// desktop's remembered mode with one made on a screen it never applied to.
const viewport = window.matchMedia?.(NARROW) ?? null
const narrow = ref(viewport?.matches ?? false)
const remembered = ref(readStored())
const narrowMode = ref('source')
const mode = computed(() => (narrow.value ? narrowMode.value : remembered.value ?? 'split'))
const choices = computed(() => (narrow.value ? MODES.filter((m) => m.id !== 'split') : MODES))

function choose(id) {
  if (narrow.value) {
    narrowMode.value = id
    return
  }
  remembered.value = id
  try {
    window.localStorage.setItem(STORAGE_KEY, id)
  } catch {
    // Storage refused (private mode, quota): the choice still holds here.
  }
}

function onViewport(event) {
  narrow.value = event.matches
  narrowMode.value = 'source'
}
viewport?.addEventListener?.('change', onViewport)
onBeforeUnmount(() => viewport?.removeEventListener?.('change', onViewport))

const source = ref(null)
const textarea = computed(() => source.value?.textarea ?? null)
defineExpose({ textarea })

const rendered = computed(() => resolveImageRefs(props.modelValue, props.images))
const blank = computed(() => !props.modelValue?.trim())

function fieldAttrs(all) {
  const { class: _class, style: _style, ...rest } = all
  return rest
}
</script>

<template>
  <div
    :class="['md-editor', `is-${mode}`, $attrs.class]" :style="$attrs.style"
    :data-mode="mode" data-testid="markdown-editor"
  >
    <div class="me-bar">
      <div class="me-tools">
        <slot v-if="mode !== 'preview'" name="toolbar" />
      </div>
      <div class="me-modes" role="group" aria-label="Editor mode">
        <Button
          v-for="m in choices" :key="m.id"
          variant="ghost" size="sm"
          :class="{ 'is-active': mode === m.id }"
          :aria-pressed="mode === m.id ? 'true' : 'false'"
          :data-testid="`editor-mode-${m.id}`"
          @click="choose(m.id)"
        >
          <Icon :name="m.icon" :size="13" />
          <span>{{ m.label }}</span>
        </Button>
      </div>
    </div>
    <div class="me-panes">
      <RefTextarea
        v-show="mode !== 'preview'"
        ref="source"
        v-bind="fieldAttrs($attrs)"
        class="me-source"
        :model-value="modelValue" :project="project" :feature="feature"
        @update:model-value="emit('update:modelValue', $event)"
      />
      <div v-if="mode !== 'source'" class="me-preview" :data-testid="previewTestid">
        <MarkdownContent v-if="!blank" :markdown="rendered" refs :project="project" />
        <p v-else class="me-blank">Nothing to preview yet.</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.md-editor { margin-top: 0.75rem; }
.me-bar {
    display: flex; align-items: center; justify-content: space-between;
    gap: 0.5rem; margin-bottom: 0.375rem;
}
.me-tools { display: flex; align-items: center; gap: 0.125rem; min-width: 0; }
.me-modes {
    display: flex; gap: 0.125rem; padding: 2px;
    border-radius: var(--radius-md); background: var(--color-surface-2);
}
.me-modes .is-active { background: var(--color-surface); color: var(--color-fg); }
.me-panes { display: grid; gap: 0.75rem; }
.is-split .me-panes { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); }
.me-source {
    font-family: var(--font-mono, ui-monospace, monospace);
    font-size: 0.8125rem; line-height: 1.5;
}
.me-source :deep(textarea) { display: block; }
.me-preview {
    min-width: 0; font-size: 0.8125rem; color: var(--color-fg-muted);
    overflow-wrap: anywhere;
}
/* Sized to nothing so the textarea alone sets the row's height; the preview
   stretches to it and scrolls, rather than a long render growing both panes. */
.is-split .me-preview {
    contain: size; overflow: auto;
    padding: 0.5rem 0.75rem;
    border: 1px solid var(--color-border-subtle); border-radius: var(--radius-md);
}
.me-preview :deep(img) {
    max-width: 100%; height: auto; border-radius: var(--radius-md);
}
.me-blank { font-size: 0.75rem; color: var(--color-fg-faint); }
</style>
