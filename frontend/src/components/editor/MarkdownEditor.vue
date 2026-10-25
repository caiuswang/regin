<script setup>
// A markdown body edited in one of two faces over the same source bytes.
//
// Rich (the default) edits the rendering itself; Source is the textarea, the
// escape hatch. Either way what goes up is source: the rich face re-renders
// only the blocks the writer touched and hands every other block back as the
// bytes it was given (`editor/markdownDoc.js` says how), because these columns
// are read and written by agents and a whole-document round-trip rewrote 89%
// of 544 stored bodies. Rich parses with the same marked lexer
// `MarkdownContent` renders with, so the face shows what the page shows after
// Save. Tiptap is loaded with the face, not with the page.
//
// A host whose saved view is quoted text rather than rendered markdown (a
// defect's symptom, drawn by `RefInlineText`) passes `verbatim`: source only,
// no mode switch and no toolbar, since a mark it wrote would display as typed.
//
// The host's attributes and listeners (test id, aria-label, paste, drop) land
// on the textarea, and the textarea is exposed as `textarea` for
// `utils/caret.js`, so a host swaps this in for a bare `RefTextarea`. On the
// rich face only the listeners carry over, and the face takes `previewTestid`.
import { computed, defineAsyncComponent, ref } from 'vue'
import Button from '../ui/Button.vue'
import EditorToolbar from './EditorToolbar.vue'
import Icon from '../ui/Icon.vue'
import RefTextarea from '../refs/RefTextarea.vue'
import { caretTarget, withCaretInsert } from '../../utils/caret'
import { imageRefBlock } from '../../utils/taskBodyImages'

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
  verbatim: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue', 'submit'])

const RichEditor = defineAsyncComponent(() => import('./RichEditor.vue'))

const MODES = [
  { id: 'rich', label: 'Rich', icon: 'eye' },
  { id: 'source', label: 'Source', icon: 'code' },
]
const STORAGE_KEY = 'regin_editor_mode'
// The first editor's split and preview both showed the rendering; it is what
// the rich face now edits.
const STORED = { rich: 'rich', split: 'rich', preview: 'rich', source: 'source' }

function readStored() {
  try {
    return STORED[window.localStorage.getItem(STORAGE_KEY)] ?? null
  } catch {
    return null
  }
}

const remembered = ref(readStored())
const mode = computed(() => (props.verbatim ? 'source' : remembered.value ?? 'rich'))

function choose(id) {
  remembered.value = id
  try {
    window.localStorage.setItem(STORAGE_KEY, id)
  } catch {
    // Storage refused (private mode, quota): the choice still holds here.
  }
}

const source = ref(null)
const rich = ref(null)
const toolbar = ref(null)
const textarea = computed(() => source.value?.textarea ?? null)
const editor = computed(() => rich.value?.editor ?? null)

// The images a host's paste or drop attached, written where the writer is:
// image nodes at the rich face's caret, or `![](image:N)` lines at the
// textarea's when `event` landed in it (appended otherwise).
function insertImages(idxs, event = null) {
  if (!idxs?.length) return
  if (mode.value === 'rich') {
    rich.value?.insertImages(idxs, event)
    return
  }
  emit('update:modelValue', withCaretInsert(caretTarget(event, textarea.value), props.modelValue, imageRefBlock(idxs)))
}

defineExpose({ textarea, editor, insertImages })

function fieldAttrs(all) {
  const { class: _class, style: _style, ...rest } = all
  return rest
}

function listeners(all) {
  return Object.fromEntries(Object.entries(all).filter(([key]) => /^on[A-Z]/.test(key)))
}
</script>

<template>
  <div
    :class="['md-editor', `is-${mode}`, $attrs.class]" :style="$attrs.style"
    :data-mode="mode" data-testid="markdown-editor"
  >
    <div v-if="!verbatim" class="me-bar">
      <div class="me-tools">
        <slot name="toolbar">
          <EditorToolbar ref="toolbar" :mode="mode" :field="source" :editor="editor" />
        </slot>
      </div>
      <div class="me-modes" role="group" aria-label="Editor mode">
        <Button
          v-for="m in MODES" :key="m.id"
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
    <RichEditor
      v-if="mode === 'rich'"
      ref="rich"
      v-bind="listeners($attrs)"
      :model-value="modelValue" :images="images" :project="project" :feature="feature"
      :testid="previewTestid"
      :aria-label="$attrs['aria-label'] ?? null" :placeholder="$attrs.placeholder ?? ''"
      @update:model-value="emit('update:modelValue', $event)"
      @submit="emit('submit')"
      @link="toolbar?.openLink()"
    />
    <RefTextarea
      v-else
      ref="source"
      v-bind="fieldAttrs($attrs)"
      class="me-source"
      :model-value="modelValue" :project="project" :feature="feature"
      @update:model-value="emit('update:modelValue', $event)"
    />
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
    display: flex; gap: 0.125rem; padding: 2px; flex-shrink: 0;
    border-radius: var(--radius-md); background: var(--color-surface-2);
}
.me-modes .is-active { background: var(--color-surface); color: var(--color-fg); }
.me-source {
    font-family: var(--font-mono, ui-monospace, monospace);
    font-size: 0.8125rem; line-height: 1.5;
}
.me-source :deep(textarea) { display: block; }
</style>
