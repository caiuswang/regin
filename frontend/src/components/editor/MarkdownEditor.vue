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
//
// A spec runs to thousands of lines, so neither face scrolls inside itself:
// both grow with their text and the page scrolls. What the writer needs at
// hand rides along instead — the head (toolbar, mode switch, Shortcuts, Expand and the
// host's Save/Cancel in the `actions` slot) sticks to the top of the page's
// scroller while the editor is on screen. Expand (or ⌘⇧F) opens the focus
// layout, Collapse or Esc closes it (`composables/useEditorLayout`).
//
// The rich face's `/` Image item opens a file picker owned here and hands the
// chosen files to the host's `attach`, which resolves either the attached
// `idx` list or `{ taken, error }`. What arrived is placed at the caret; a
// failure is the host's to show, reported as `attach-error`.
//
// A host passing `outline` gets the rich face's headings listed (`Outline`):
// a rail in the focus layout's left margin when that margin is wide enough
// to hold one (measured, since the sidebar's width is the page's to decide),
// otherwise an Outline button in the head once there are enough headings to
// need one. The rail sits beside the 84ch column, never inside it.
import { computed, defineAsyncComponent, onBeforeUnmount, onMounted, ref, useSlots } from 'vue'
import Button from '../ui/Button.vue'
import EditorToolbar from './EditorToolbar.vue'
import Icon from '../ui/Icon.vue'
import Outline from './Outline.vue'
import RefTextarea from '../refs/RefTextarea.vue'
import ShortcutsPopover from './ShortcutsPopover.vue'
import { useEditorLayout } from '../../composables/useEditorLayout.js'
import { OUTLINE_MIN } from '../../editor/outline.js'
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
  // The focus layout's title line: what is being edited.
  title: { type: String, default: '' },
  outline: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue', 'submit', 'attach-error'])

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
const root = ref(null)
const head = ref(null)
const textarea = computed(() => source.value?.textarea ?? null)
const editor = computed(() => rich.value?.editor ?? null)

const slots = useSlots()
const hasHead = computed(() => !props.verbatim || !!slots.actions)
const { layout, setLayout } = useEditorLayout({
  root, head,
  editor: () => editor.value,
  source: () => source.value,
  enabled: () => !props.verbatim,
})

// The rail is stuck just under the stuck head, whose height wraps with the
// toolbar, and is as wide as the column's left margin allows (the pane's own
// padding excluded). Both are measured as they change rather than guessed.
const SCROLLER = '.content-scroll'
const RAIL_MIN = 160
const RAIL_MAX = 224
const RAIL_GAP = 16
const headHeight = ref(0)
const railRoom = ref(0)
const railWidth = computed(() => Math.min(RAIL_MAX, railRoom.value - RAIL_GAP))

const headings = computed(() => rich.value?.headings ?? [])
const outlineAs = computed(() => {
  if (!props.outline || mode.value !== 'rich') return null
  if (layout.value === 'focus' && railRoom.value >= RAIL_MIN + RAIL_GAP) return 'rail'
  return headings.value.length >= OUTLINE_MIN ? 'dropdown' : null
})
const headingAt = (entry) => editor.value?.view.nodeDOM(entry.pos) ?? null
// Where the head's bottom sits once it is stuck — where a heading lands, even
// while the head itself is still further down the page in its flow position.
function headEdge() {
  const el = head.value
  const scroller = root.value?.closest(SCROLLER)
  if (!el || !scroller) return el?.getBoundingClientRect().bottom ?? 0
  const pad = parseFloat(getComputedStyle(scroller).paddingTop) || 0
  const stuck = scroller.getBoundingClientRect().top + scroller.clientTop + pad + (parseFloat(getComputedStyle(el).top) || 0)
  return stuck + el.offsetHeight
}
const pickHeading = (entry) => rich.value?.placeAtHeading(entry.pos)

let sizes = null

function measure() {
  headHeight.value = head.value?.offsetHeight ?? 0
  const scroller = root.value?.closest(SCROLLER)
  if (!scroller || layout.value !== 'focus') {
    railRoom.value = 0
    return
  }
  const pad = parseFloat(getComputedStyle(scroller).paddingLeft) || 0
  railRoom.value = root.value.getBoundingClientRect().left - scroller.getBoundingClientRect().left - scroller.clientLeft - pad
}

onMounted(() => {
  if (typeof ResizeObserver === 'undefined') return
  sizes = new ResizeObserver(measure)
  for (const el of [root.value, head.value, root.value?.closest(SCROLLER)]) if (el) sizes.observe(el)
})
onBeforeUnmount(() => sizes?.disconnect())

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

function refocusFace() {
  if (mode.value === 'rich') editor.value?.view.focus()
  else textarea.value?.focus({ preventScroll: true })
}

const picker = ref(null)

function pickImage() {
  const input = picker.value
  if (!input || !props.attach) return
  input.value = ''
  input.click()
}

async function onPicked() {
  const input = picker.value
  if (!input?.files?.length) return
  let result
  try {
    result = await props.attach(input)
  } catch (e) {
    emit('attach-error', e?.message || 'could not attach the image')
    return
  }
  const taken = Array.isArray(result) ? result : result?.taken ?? []
  insertImages(taken)
  if (result?.error || !taken.length) emit('attach-error', result?.error || 'could not attach the image')
}

defineExpose({ textarea, editor, insertImages, layout, setLayout })

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
    ref="root"
    :class="['md-editor', `is-${mode}`, `is-${layout}`, $attrs.class]"
    :style="[$attrs.style, { '--me-head-h': `${headHeight}px`, '--me-rail-w': `${railWidth}px` }]"
    :data-mode="mode" :data-layout="layout" data-testid="markdown-editor"
  >
    <div v-if="hasHead" ref="head" class="me-head" data-testid="editor-head">
      <div v-if="layout === 'focus'" class="me-titlebar">
        <span class="me-title" data-testid="editor-focus-title">{{ title }}</span>
        <Button
          variant="ghost" size="sm" data-testid="editor-collapse"
          title="Collapse (Esc)" @click="setLayout('inline')"
        >
          <Icon name="minimize-2" :size="13" />
          <span>Collapse</span>
        </Button>
      </div>
      <div class="me-bar">
        <div v-if="!verbatim" class="me-tools">
          <slot name="toolbar">
            <EditorToolbar ref="toolbar" :mode="mode" :field="source" :editor="editor" />
          </slot>
        </div>
        <div class="me-end">
          <Outline
            v-if="outlineAs === 'dropdown'" variant="dropdown"
            :entries="headings" :element-for="headingAt" :edge="headEdge" :edge-key="headHeight"
            @pick="pickHeading"
          />
          <div v-if="!verbatim" class="me-modes" role="group" aria-label="Editor mode">
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
          <ShortcutsPopover v-if="!verbatim" @closed="refocusFace" />
          <Button
            v-if="!verbatim && layout === 'inline'"
            variant="ghost" size="sm" data-testid="editor-expand"
            aria-label="Expand" title="Expand (⌘⇧F)" @click="setLayout('focus')"
          >
            <Icon name="maximize-2" :size="13" />
          </Button>
          <div v-if="$slots.actions" class="me-actions">
            <slot name="actions" />
          </div>
        </div>
      </div>
    </div>
    <div v-if="outlineAs === 'rail'" class="me-rail">
      <Outline
        variant="rail" :entries="headings" :element-for="headingAt" :edge="headEdge" :edge-key="headHeight"
        @pick="pickHeading"
      />
    </div>
    <RichEditor
      v-if="mode === 'rich'"
      ref="rich"
      v-bind="listeners($attrs)"
      :model-value="modelValue" :images="images" :project="project" :feature="feature"
      :testid="previewTestid" :can-attach="!!attach"
      :aria-label="$attrs['aria-label'] ?? null" :placeholder="$attrs.placeholder ?? ''"
      @update:model-value="emit('update:modelValue', $event)"
      @submit="emit('submit')"
      @link="toolbar?.openLink()"
      @pick-image="pickImage"
    />
    <RefTextarea
      v-else
      ref="source"
      v-bind="fieldAttrs($attrs)"
      class="me-source" autogrow
      :model-value="modelValue" :project="project" :feature="feature"
      @update:model-value="emit('update:modelValue', $event)"
    />
    <input
      v-if="attach && mode === 'rich'"
      ref="picker" type="file" accept="image/*" multiple hidden
      data-testid="editor-image-picker" @change="onPicked"
    >
  </div>
</template>

<style scoped>
.md-editor { margin-top: 0.75rem; }
.md-editor.is-focus { position: relative; max-width: 84ch; margin-inline: auto; }
/* In the focus column's left margin, stuck under the stuck head. */
.me-rail {
    position: absolute; top: var(--me-head-h, 0px); bottom: 0;
    right: calc(100% + 16px); width: var(--me-rail-w, 0px);
}
.me-rail > :deep(.ol-rail) {
    position: sticky; top: calc(var(--me-head-h, 0px) - 1.5rem + 0.5rem);
    max-height: calc(100vh - var(--me-head-h, 0px) - 6rem); overflow-y: auto;
}
/* Pinned by the `.content-scroll` padding (1.5rem, 1rem on phones), like the
   feature page's section index, and drawn over that index: while the editor
   is on screen its head is what the writer needs. A host may widen the band
   over its own padding with `--me-head-bleed`. */
.me-head {
    position: sticky; top: -1.5rem; z-index: 21;
    margin-inline: calc(-1 * var(--me-head-bleed, 0px));
    padding: 0.375rem var(--me-head-bleed, 0px);
    margin-bottom: 0.375rem;
    background: var(--color-surface);
    border-bottom: 1px solid var(--color-border-subtle);
}
@media (max-width: 767px) {
    .me-head { top: -1rem; }
}
.me-titlebar {
    display: flex; align-items: center; justify-content: space-between;
    gap: 0.5rem; padding-bottom: 0.375rem;
}
.me-title {
    min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    font-size: 0.9375rem; font-weight: 600; color: var(--color-fg);
}
.me-bar {
    display: flex; align-items: center; justify-content: space-between;
    flex-wrap: wrap; gap: 0.5rem;
}
.me-tools { display: flex; flex-wrap: wrap; align-items: center; gap: 0.125rem; min-width: 0; }
.me-end {
    display: flex; flex-wrap: wrap; align-items: center; justify-content: flex-end;
    gap: 0.375rem; margin-left: auto; min-width: 0;
}
.me-modes {
    display: flex; gap: 0.125rem; padding: 2px; flex-shrink: 0;
    border-radius: var(--radius-md); background: var(--color-surface-2);
}
.me-modes .is-active { background: var(--color-surface); color: var(--color-fg); }
.me-actions { display: flex; align-items: center; gap: 0.375rem; }
.me-source {
    font-family: var(--font-mono, ui-monospace, monospace);
    font-size: 0.8125rem; line-height: 1.5;
}
.me-source :deep(textarea) { display: block; }
</style>
