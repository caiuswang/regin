<script setup>
// The rich face of `MarkdownEditor`: the body edited on its own rendering.
//
// What goes up is still source bytes. `parse` remembers each top-level block
// with the bytes it came from, and ProseMirror keeps every block a transaction
// did not touch as the same object, so `serialize` writes untouched blocks
// back verbatim and re-renders only the ones that changed (`editor/
// markdownDoc.js`). Opening a body and saving it is therefore the identity.
//
// The parsed blocks go in by a replace step rather than as Tiptap `content`:
// Tiptap would copy them into new nodes and every block would read as edited,
// while a replace step places the very nodes it is given. The step stays out
// of the undo history and emits no update, so loading writes nothing.
// (`view.updateState` would skip the transaction too, but `@tiptap/vue-3`
// keeps its own copy of the state and puts that one back on mount.)
import { onBeforeUnmount, shallowRef, watch } from 'vue'
import { Editor, EditorContent } from '@tiptap/vue-3'
import { Selection } from '@tiptap/pm/state'
import { Slice } from '@tiptap/pm/model'
import { parse, serialize } from '../../editor/markdownDoc.js'
import { richExtensions } from '../../editor/extensions.js'

const props = defineProps({
  modelValue: { type: String, default: '' },
  images: { type: Array, default: () => [] },
  testid: { type: String, default: null },
  ariaLabel: { type: String, default: null },
  placeholder: { type: String, default: '' },
})
const emit = defineEmits(['update:modelValue', 'submit', 'link'])

let segments = []
let retained = new WeakMap()
let emitted = null

function imageUrl(attrs) {
  if (attrs.idx === null || attrs.idx === undefined) return attrs.src
  return props.images.find((item) => item.idx === attrs.idx)?.url ?? null
}

// Plain text from outside is markdown source, and lands as the blocks it
// spells (Linear's paste). HTML keeps Tiptap's own parse, files go on to the
// host's paste handler, and a code block takes the text as typed.
function handlePaste(view, event) {
  const data = event.clipboardData
  if (!data || data.files?.length || data.types.includes('text/html')) return false
  const text = data.getData('text/plain')
  if (!text || view.state.selection.$from.parent.type.spec.code) return false
  const { doc } = parse(text, { schema: view.state.schema })
  const inline = doc.childCount === 1 && doc.firstChild.type.name === 'paragraph'
  view.dispatch(view.state.tr.replaceSelection(new Slice(doc.content, inline ? 1 : 0, inline ? 1 : 0)).scrollIntoView())
  return true
}

const attributes = { class: 'pattern-content rich-face', spellcheck: 'false', 'data-owns-mod-k': '' }
if (props.testid) attributes['data-testid'] = props.testid
if (props.ariaLabel) attributes['aria-label'] = props.ariaLabel

const editor = shallowRef(new Editor({
  extensions: richExtensions({
    placeholder: props.placeholder,
    resolveImage: imageUrl,
    onSubmit: () => emit('submit'),
    onLink: () => emit('link'),
  }),
  editorProps: { attributes, handlePaste },
  onUpdate: ({ editor: ed }) => {
    const out = serialize(ed.state.doc, segments, retained)
    if (out === emitted) return
    emitted = out
    emit('update:modelValue', out)
  },
}))

function load(src) {
  const ed = editor.value
  const parsed = parse(src ?? '', { schema: ed.schema })
  segments = parsed.segments
  retained = parsed.retained
  emitted = src ?? ''
  const at = ed.state.selection.from
  const tr = ed.state.tr.replaceWith(0, ed.state.doc.content.size, parsed.doc.content)
  tr.setSelection(Selection.near(tr.doc.resolve(Math.min(at, tr.doc.content.size))))
  tr.setMeta('addToHistory', false).setMeta('preventUpdate', true)
  ed.view.dispatch(tr)
}

load(props.modelValue)
// A change that did not come from this face — a host splicing in an image
// reference, a reset — is a new source, parsed afresh.
watch(() => props.modelValue, (value) => { if ((value ?? '') !== emitted) load(value) })

onBeforeUnmount(() => editor.value?.destroy())

defineExpose({ editor })
</script>

<template>
  <EditorContent :editor="editor" class="rich-editor" data-testid="rich-editor" />
</template>

<style scoped>
.rich-editor :deep(.rich-face) {
    min-height: 8rem; padding: 0.5rem 0.75rem;
    font-size: 0.8125rem; color: var(--color-fg);
    border: 1px solid var(--color-border-subtle); border-radius: var(--radius-md);
    overflow-wrap: anywhere; outline: none;
}
.rich-editor :deep(.rich-face:focus-visible) { border-color: var(--color-primary); }
.rich-editor :deep(.rich-face > :first-child) { margin-top: 0; }
.rich-editor :deep(p.is-editor-empty:first-child::before) {
    content: attr(data-placeholder); float: left; height: 0;
    color: var(--color-fg-faint); pointer-events: none;
}
.rich-editor :deep(img) { max-width: 100%; height: auto; border-radius: var(--radius-md); }
.rich-editor :deep(.rich-opaque pre) {
    margin: 0.5rem 0; padding: 0.375rem 0.5rem; white-space: pre-wrap;
    font-size: 0.75rem; color: var(--color-fg-muted);
    background: var(--color-surface-2); border-radius: var(--radius-md);
}
.rich-editor :deep(.rich-ref) {
    padding: 0 0.25rem; border-radius: var(--radius-sm, 0.25rem);
    background: var(--color-surface-2); color: var(--color-primary);
}
.rich-editor :deep(.rich-image-missing) { font-style: italic; color: var(--color-fg-faint); }
.rich-editor :deep(ul[data-type="taskList"]) { list-style: none; padding-left: 0.25rem; }
.rich-editor :deep(li[data-type="taskItem"]) { display: flex; gap: 0.375rem; align-items: baseline; }
.rich-editor :deep(li[data-type="taskItem"] > div) { flex: 1; min-width: 0; }
.rich-editor :deep(table td), .rich-editor :deep(table th) { position: relative; min-width: 3rem; }
.rich-editor :deep(.selectedCell::after) {
    content: ''; position: absolute; inset: 0; pointer-events: none;
    background: color-mix(in srgb, var(--color-primary) 12%, transparent);
}
</style>
