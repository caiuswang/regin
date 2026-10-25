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
//
// References, pictures and opaque blocks are node views: the chip the saved
// page draws, the attachment the host lists, and a card of the block as the
// page renders it. `@` runs the source face's menu (`useRefMentions`) over the
// token `@tiptap/suggestion` finds, and an accepted row is parsed by the same
// `parse` that reads the body, so a reference is told apart by `parseRefs`
// alone. A pasted or dropped image is the host's to attach and report on; the
// host hands back what arrived and `insertImages` places it at the caret.
// `/` opens the block menu (`editor/slash.js`); its Image item asks the host
// for a file (`pick-image`) and its Reference item hands over to `@`. A text
// selection raises the mark bar (`EditorBubble`); Esc while it is up drops the
// selection to its end, so the bar goes before anything else Esc does.
import { onBeforeUnmount, ref, shallowRef, useId, watch } from 'vue'
import { Editor, EditorContent, VueNodeViewRenderer } from '@tiptap/vue-3'
import { NodeSelection, Selection, TextSelection } from '@tiptap/pm/state'
import { Slice } from '@tiptap/pm/model'
import api from '../../api'
import RefMenu from '../refs/RefMenu.vue'
import RichCodeBlock from './nodes/RichCodeBlock.vue'
import RichImage from './nodes/RichImage.vue'
import RichOpaque from './nodes/RichOpaque.vue'
import RichRef from './nodes/RichRef.vue'
import EditorBubble from './EditorBubble.vue'
import SlashMenu from './SlashMenu.vue'
import { parse, serialize } from '../../editor/markdownDoc.js'
import { richExtensions } from '../../editor/extensions.js'
import { SLASH_MENU } from '../../editor/slash.js'
import { exitSuggestion } from '@tiptap/suggestion'
import { refForUrl, useRefMentions } from '../../composables/useRefMentions.js'
import { useRefPreview } from '../../composables/useRefPreview.js'
import { useRefResolve } from '../../composables/useRefResolve.js'
import { imageFilesFrom } from '../../utils/imageFiles'
import { imageRefBlock } from '../../utils/taskBodyImages'

const props = defineProps({
  modelValue: { type: String, default: '' },
  images: { type: Array, default: () => [] },
  project: { type: [Number, String], default: null },
  feature: { type: [Number, String], default: null },
  testid: { type: String, default: null },
  ariaLabel: { type: String, default: null },
  placeholder: { type: String, default: '' },
  canAttach: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue', 'submit', 'link', 'pick-image'])

let segments = []
let retained = new WeakMap()
let emitted = null

// Every reference in the document, for the chips' live items.
const targets = shallowRef([])

function collectTargets(doc) {
  const found = []
  doc.descendants((node) => {
    if (node.type.name === 'ref') found.push(`${node.attrs.kind}:${node.attrs.key}`)
  })
  const next = [...new Set(found)]
  if (next.join('\n') !== targets.value.join('\n')) targets.value = next
}

const { items } = useRefResolve(
  () => targets.value,
  () => (targets.value.some((t) => t.startsWith('file:')) ? props.project : null))

const menu = useRefMentions({ project: () => props.project, feature: () => props.feature })
const { open, sections, rows, activeIndex, stale, loading, note, selected, context, query } = menu
const preview = useRefPreview(() => {
  const row = open.value ? rows.value[activeIndex.value] : null
  return row?.type === 'item' ? { ref: row.item.ref, project: selected.value } : null
})
const listId = `ref-menu-${useId()}`
const anchor = ref(null)
// The suggestion the menu is open over: its range and where it is drawn.
let token = null

function imageUrl(attrs) {
  if (attrs.idx === null || attrs.idx === undefined) return attrs.src
  return props.images.find((item) => item.idx === attrs.idx)?.url ?? null
}

// The inline content `text` parses to — a reference written the way the
// source face writes it — or null when it is not one paragraph.
function inlineOf(text) {
  const { doc } = parse(text, { schema: editor.value.schema })
  const only = doc.childCount === 1 ? doc.firstChild : null
  return only?.type.name === 'paragraph' && only.childCount ? only.content : null
}

function insertInline(range, text) {
  const ed = editor.value
  const content = inlineOf(text.trimEnd())
  const tr = ed.state.tr
  if (content) tr.replaceWith(range.from, range.to, content).insertText(' ')
  else tr.insertText(text, range.from, range.to)
  ed.view.dispatch(tr.scrollIntoView())
  ed.view.focus()
}

async function resolveRef(target) {
  try {
    const res = await api.get(`/refs/resolve?ref=${encodeURIComponent(target)}`)
    return res?.refs?.[target] ?? null
  } catch {
    return null
  }
}

// A pasted URL of a feature, task, defect or session becomes its reference,
// as in the source face; one that resolves to nothing, or to a session this
// viewer may not read, is pasted as it was. Typed over while the lookup was
// out, the paste lands at the caret rather than the remembered range.
async function pasteUrl(view, target, text) {
  const at = { from: view.state.selection.from, to: view.state.selection.to, doc: view.state.doc }
  const item = await resolveRef(target)
  const ed = editor.value
  if (!ed || ed.isDestroyed) return
  const range = ed.state.doc === at.doc ? at : ed.state.selection
  if (item && !item.restricted) insertInline(range, `[${item.label}](${item.ref}) `)
  else insertInline(range, text)
}

// Plain text from outside is markdown source, and lands as the blocks it
// spells (Linear's paste). HTML keeps Tiptap's own parse and a code block
// takes the text as typed. Image files are the host's: the paste is claimed
// here only so ProseMirror does not divert it, and still bubbles to the host.
function handlePaste(view, event) {
  const data = event.clipboardData
  if (!data) return false
  if (imageFilesFrom(data).length) return true
  if (data.files?.length) return false
  const text = data.getData('text/plain')
  if (!text || view.state.selection.$from.parent.type.spec.code) return false
  const target = refForUrl(text, window.location.origin)
  if (target) {
    pasteUrl(view, target, text)
    return true
  }
  if (data.types.includes('text/html')) return false
  const { doc } = parse(text, { schema: view.state.schema })
  const inline = doc.childCount === 1 && doc.firstChild.type.name === 'paragraph'
  view.dispatch(view.state.tr.replaceSelection(new Slice(doc.content, inline ? 1 : 0, inline ? 1 : 0)).scrollIntoView())
  return true
}

const attributes = { class: 'pattern-content rich-face', spellcheck: 'false', 'data-owns-mod-k': '' }
if (props.testid) attributes['data-testid'] = props.testid
if (props.ariaLabel) attributes['aria-label'] = props.ariaLabel

// A dropped image lands where it was dropped: the caret goes there, and the
// drop goes on to the host, which attaches it and calls `insertImages`.
function handleDrop(view, event, _slice, moved) {
  if (moved || !imageFilesFrom(event.dataTransfer).length) return false
  const at = view.posAtCoords({ left: event.clientX, top: event.clientY })
  if (at) view.dispatch(view.state.tr.setSelection(TextSelection.near(view.state.doc.resolve(at.pos))))
  return true
}

function anchorFor(rect) {
  const box = editor.value.view.dom.getBoundingClientRect()
  return { left: rect.left, lineTop: rect.top, lineBottom: rect.bottom, boxLeft: box.left, boxWidth: box.width }
}

// The suggestion's text is `@query`; the character before it goes in front so
// the menu's rule about an `@` inside a word reads what the source face reads.
function syncMenu(found) {
  token = found
  const { state } = found.editor
  const { from } = found.range
  const before = state.doc.resolve(from).parentOffset > 0 ? state.doc.textBetween(from - 1, from, '', '\ufffc') : ''
  const text = `${before}${found.text}`
  menu.sync(text, text.length)
  const rect = found.clientRect?.()
  if (open.value && rect) anchor.value = anchorFor(rect)
}

function accept(row) {
  const edit = menu.editFor(row)
  if (!edit || !token) return
  const range = token.range
  if (row.type === 'kind') {
    const ed = editor.value
    ed.view.dispatch(ed.state.tr.insertText(edit.text, range.from, range.to))
    return
  }
  insertInline(range, edit.text)
}

const mention = () => ({
  onStart: syncMenu,
  onUpdate: syncMenu,
  onExit: () => {
    token = null
    menu.close()
  },
  onKeyDown: ({ event }) => {
    if (event.isComposing || event.keyCode === 229) return false
    const res = menu.handleKeydown(event)
    if (res.row) accept(res.row)
    return res.handled
  },
})

// The `/` menu while it is open: what it lists, the highlighted row, where it
// is drawn, and the suggestion's own `command` that runs a picked item.
const slash = shallowRef(null)
const slashIndex = ref(0)

function slashAnchor(clientRect, fallback = null) {
  const rect = clientRect?.()
  return rect ? { left: rect.left, lineTop: rect.top, lineBottom: rect.bottom } : fallback
}

function syncSlash(found) {
  const queryChanged = slash.value?.query !== found.query
  slash.value = {
    query: found.query,
    items: found.items ?? [],
    command: found.command,
    clientRect: found.clientRect,
    anchor: slashAnchor(found.clientRect, slash.value?.anchor ?? null),
  }
  if (queryChanged || slashIndex.value >= slash.value.items.length) slashIndex.value = 0
}

function pickSlash(item) {
  slash.value?.command(item)
}

const SLASH_STEP = { ArrowDown: 1, ArrowUp: -1 }

function slashKey({ event }) {
  const open = slash.value
  if (!open || event.isComposing || event.keyCode === 229) return false
  const count = open.items.length
  if (event.key in SLASH_STEP) {
    if (count) slashIndex.value = (slashIndex.value + SLASH_STEP[event.key] + count) % count
    return true
  }
  if ((event.key === 'Enter' || event.key === 'Tab') && count) {
    pickSlash(open.items[slashIndex.value])
    return true
  }
  return false
}

const slashRender = () => ({
  onStart: syncSlash,
  onUpdate: syncSlash,
  onExit: () => { slash.value = null },
  onKeyDown: slashKey,
})

function closeOverlays() {
  menu.close()
  const view = editor.value?.view
  if (view && SLASH_MENU.getState(view.state)?.active) exitSuggestion(view, SLASH_MENU)
}

const bubble = ref(null)

function handleKeyDown(view, event) {
  if (event.key !== 'Escape' || !bubble.value?.visible) return false
  const { to } = view.state.selection
  view.dispatch(view.state.tr.setSelection(TextSelection.create(view.state.doc, to)))
  return true
}

const editor = shallowRef(new Editor({
  extensions: richExtensions({
    placeholder: props.placeholder,
    resolveImage: imageUrl,
    refItem: (target) => items.value[target],
    images: () => props.images,
    onSubmit: () => emit('submit'),
    onLink: () => emit('link'),
    views: {
      ref: VueNodeViewRenderer(RichRef),
      image: VueNodeViewRenderer(RichImage),
      opaque: VueNodeViewRenderer(RichOpaque),
      codeBlock: VueNodeViewRenderer(RichCodeBlock),
    },
    mention,
    slash: {
      render: slashRender,
      canAttach: () => props.canAttach,
      pickImage: () => emit('pick-image'),
    },
  }),
  editorProps: { attributes, handlePaste, handleDrop, handleKeyDown },
  onBlur: closeOverlays,
  onUpdate: ({ editor: ed }) => {
    collectTargets(ed.state.doc)
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
  collectTargets(ed.state.doc)
}

// Through the browser's editing stack, so the box's own undo takes it back.
function insertIntoBox(box, text) {
  box.focus()
  let done = false
  try {
    done = document.execCommand('insertText', false, text)
  } catch {
    done = false
  }
  if (done) return
  box.setRangeText(text, box.selectionStart, box.selectionEnd, 'end')
  box.dispatchEvent(new Event('input', { bubbles: true }))
}

// The images the host attached, as one undo step. Pasted into an opaque
// block's open source box, they are references in that block's source.
// Anywhere else they go at the caret — after a selected chip, picture or
// block rather than over it: an attachment never replaces what was there.
function insertImages(idxs, event = null) {
  const ed = editor.value
  if (!ed || !idxs?.length) return
  const box = event?.target
  if (box instanceof HTMLTextAreaElement && box.isConnected && ed.view.dom.contains(box)) {
    insertIntoBox(box, imageRefBlock(idxs))
    return
  }
  const { image } = ed.schema.nodes
  const nodes = idxs.map((idx) => image.create({ idx, alt: '', src: null, title: null }).toJSON())
  const { selection } = ed.state
  const at = selection instanceof NodeSelection ? selection.to : { from: selection.from, to: selection.to }
  ed.chain().focus().newUndoStep().insertContentAt(at, nodes).run()
}

load(props.modelValue)
// A change that did not come from this face — a host splicing in an image
// reference, a reset — is a new source, parsed afresh.
watch(() => props.modelValue, (value) => { if ((value ?? '') !== emitted) load(value) })

function onViewportShift(event) {
  if (event?.target instanceof Element && event.target.closest('[data-ref-menu], [data-slash-menu]')) return
  const rect = token?.clientRect?.()
  if (open.value && rect) anchor.value = anchorFor(rect)
  if (slash.value) slash.value = { ...slash.value, anchor: slashAnchor(slash.value.clientRect, slash.value.anchor) }
}

function track(on) {
  const method = on ? 'addEventListener' : 'removeEventListener'
  window[method]('scroll', onViewportShift, true)
  window[method]('resize', onViewportShift)
}

// An Enter pressed into a stale list, now answered: accept the row it meant.
watch(menu.armedResolved, () => {
  const row = rows.value[activeIndex.value]
  if (row) accept(row)
})
watch(open, (isOpen) => {
  if (!isOpen) preview.reset()
})
watch(() => open.value || !!slash.value, track)

onBeforeUnmount(() => {
  track(false)
  menu.close()
  editor.value?.destroy()
})

defineExpose({ editor, insertImages })
</script>

<template>
  <div class="rich-editor" data-testid="rich-editor">
    <EditorContent :editor="editor" />
    <EditorBubble ref="bubble" :editor="editor" @link="emit('link')" />
    <Teleport to="body">
      <RefMenu
        v-if="open"
        :list-id="listId"
        :sections="sections"
        :active-index="activeIndex"
        :stale="stale"
        :loading="loading"
        :note="note"
        :project="selected"
        :feature="context"
        :preview="preview.state.value"
        :anchor="anchor"
        :data-query="query"
        @pick="accept"
        @hover="menu.setActive"
        @drill="(kind) => accept({ type: 'kind', kind })"
      />
      <SlashMenu
        v-if="slash"
        :items="slash.items"
        :active-index="slashIndex"
        :anchor="slash.anchor"
        @pick="pickSlash"
        @hover="(i) => (slashIndex = i)"
      />
    </Teleport>
  </div>
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
.rich-editor :deep(.rich-opaque) {
    margin: 0.5rem 0; padding: 0.375rem 0.625rem;
    border: 1px dashed var(--color-border); border-radius: var(--radius-md);
    background: var(--color-surface-2); cursor: default;
}
.rich-editor :deep(.rich-opaque.is-selected) { border-color: var(--color-primary); }
.rich-editor :deep(.rich-opaque-view > :first-child) { margin-top: 0; }
.rich-editor :deep(.rich-opaque-view > :last-child) { margin-bottom: 0; }
.rich-editor :deep(.rich-opaque-source) {
    display: block; width: 100%;
    font-family: var(--font-mono, ui-monospace, monospace); font-size: 0.75rem; line-height: 1.5;
}
.rich-editor :deep(.rich-ref.is-selected .ref-chip),
.rich-editor :deep(.rich-image.is-selected img) { outline: 2px solid var(--color-ring); outline-offset: 1px; }
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
