<script setup>
// The formatting toolbar of `MarkdownEditor`, for whichever face is showing.
//
// Source: each button writes one replacement through the field's
// `replaceRange`, the browser's own insert, so one undo takes the source and
// its selection back to before the press; rewriting the model instead would
// empty the undo history.
//
// Rich: each button is one Tiptap command on the editor's selection, and its
// own undo step: history is closed before the command and again after it, or
// the typing that follows within the history's time window would join the
// press and one undo would take both. (`newUndoStep` lives in
// `editor/extensions.js` with the other commands that need ProseMirror, so
// none of it loads before the rich face.)
//
// The buttons keep the focus in the face (`mousedown.prevent`), so the
// selection a press acts on is the one the writer made.
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import Input from '../ui/Input.vue'
import Popover from '../ui/Popover.vue'
import { linkEdit, markEdit } from '../../utils/markdownMarks.js'

const props = defineProps({
  mode: { type: String, default: 'source' },
  // Source: the `RefTextarea` instance — `textarea`, `replaceRange`, `setSelection`.
  field: { type: Object, default: null },
  // Rich: the Tiptap editor.
  editor: { type: Object, default: null },
})

const MARKS = [
  { kind: 'bold', label: 'Bold', icon: 'bold' },
  { kind: 'italic', label: 'Italic', icon: 'italic' },
  { kind: 'heading', label: 'Heading', icon: 'heading' },
  { kind: 'bullet_list', label: 'Bulleted list', icon: 'list' },
  { kind: 'numbered_list', label: 'Numbered list', icon: 'list-ordered' },
  { kind: 'checkbox', label: 'Checkbox', icon: 'list-checks' },
  { kind: 'code_block', label: 'Code block', icon: 'code' },
]

const MAX_HEADING = 4

function deeperHeading(chain, ed) {
  const level = ed.getAttributes('heading').level
  if (!ed.isActive('heading')) return chain.setHeading({ level: 2 })
  return chain.setHeading({ level: Math.min(level + 1, MAX_HEADING) })
}

const RICH_MARKS = {
  bold: (chain) => chain.toggleBold(),
  italic: (chain) => chain.toggleItalic(),
  heading: deeperHeading,
  bullet_list: (chain) => chain.toggleBulletList(),
  numbered_list: (chain) => chain.toggleOrderedList(),
  checkbox: (chain) => chain.toggleTaskList(),
  code_block: (chain) => chain.toggleCodeBlock(),
}

function command(build) {
  const ed = props.editor
  if (!ed) return
  build(ed.chain().focus().newUndoStep(), ed).run()
  ed.commands.newUndoStep()
}

// `from` is the selection the press acted on, which one undo puts back.
function write(edit, from) {
  props.field.replaceRange(edit.start, edit.end, edit.text, from)
  props.field.setSelection(...edit.select)
}

function mark(kind) {
  if (props.mode === 'rich') return command(RICH_MARKS[kind])
  const el = props.field?.textarea
  if (!el) return
  const { selectionStart: start, selectionEnd: end } = el
  write(markEdit(kind, el.value, start, end), [start, end])
}

// ---- tables (rich only) ----

const inTable = ref(false)
const syncTable = () => { inTable.value = Boolean(props.editor?.isActive('table')) }
watch(() => props.editor, (ed, old) => {
  old?.off('transaction', syncTable)
  ed?.on('transaction', syncTable)
  syncTable()
}, { immediate: true })
onBeforeUnmount(() => props.editor?.off('transaction', syncTable))

const TABLE_TOOLS = [
  { kind: 'row_after', label: 'Insert row', icon: 'row-insert', run: () => command((c) => c.addRowAndLand()) },
  { kind: 'column_after', label: 'Insert column', icon: 'column-insert', run: () => command((c) => c.addColumnAndLand()) },
  { kind: 'row_delete', label: 'Delete row', icon: 'row-remove', run: () => command((c) => c.deleteRow()) },
  { kind: 'column_delete', label: 'Delete column', icon: 'column-remove', run: () => command((c) => c.deleteColumn()) },
  { kind: 'table_delete', label: 'Delete table', icon: 'trash', run: () => command((c) => c.deleteTable()) },
]

function insertTable() {
  command((chain) => chain.insertTable({ rows: 3, cols: 2, withHeaderRow: true }))
}

// ---- link ----

// The link's range is read when the popover opens: typing the url moves the
// focus, and the face's selection is what the link is made of.
const linkOpen = ref(false)
// Plain text, not `type="url"`: the browser's check refuses the relative
// paths and anchors a spec links to (`docs/a.md`, `#rules`), and refuses
// them silently, by not submitting.
const url = ref('')
const urlInput = ref(null)
let range = null
let pending = null

function currentRange() {
  if (props.mode === 'rich') {
    const sel = props.editor?.state.selection
    return sel ? { start: sel.from, end: sel.to } : null
  }
  const el = props.field?.textarea
  return el ? { start: el.selectionStart, end: el.selectionEnd } : null
}

function onLinkOpen(open) {
  linkOpen.value = open
  if (!open) return
  range = currentRange()
  pending = null
  url.value = ''
  nextTick(() => urlInput.value?.focus())
}

function submitLink() {
  const value = url.value.trim()
  if (!value) return
  pending = value
  linkOpen.value = false
}

function richLink(href) {
  const { start, end } = range
  if (!href) return props.editor.chain().focus().setTextSelection({ from: start, to: end }).run()
  return command((chain) => {
    const at = chain.setTextSelection({ from: start, to: end })
    if (start !== end) return at.setLink({ href })
    return at.insertContent({ type: 'text', text: href, marks: [{ type: 'link', attrs: { href } }] })
  })
}

// The edit waits for the close, not the Enter: Reka returns the focus to the
// trigger as the panel goes, and the face must hold it for the insert to be
// an undo step. A close without a url (Escape, a click away) puts the
// selection back untouched, unless the click away landed in the field.
function onLinkClosed(event) {
  event.preventDefault()
  if (!range) return
  if (props.mode === 'rich') {
    if (props.editor) richLink(pending)
  } else {
    const el = props.field?.textarea
    if (el && pending) write(linkEdit(el.value, range.start, range.end, pending), [range.start, range.end])
    else if (el && document.activeElement !== el) props.field.setSelection(range.start, range.end)
  }
  pending = null
  range = null
}

defineExpose({ openLink: () => onLinkOpen(true) })
</script>

<template>
  <div class="editor-toolbar" role="toolbar" aria-label="Formatting" data-testid="editor-toolbar">
    <Button
      v-for="m in MARKS" :key="m.kind"
      variant="ghost" size="sm" class="et-btn"
      :aria-label="m.label" :title="m.label" :data-testid="`editor-tool-${m.kind}`"
      @mousedown.prevent @click="mark(m.kind)"
    >
      <Icon :name="m.icon" :size="14" />
    </Button>
    <Popover
      :open="linkOpen" side="bottom" align="start"
      @update:open="onLinkOpen" @close-auto-focus="onLinkClosed"
    >
      <template #trigger>
        <Button
          variant="ghost" size="sm" class="et-btn"
          aria-label="Link" title="Link" data-testid="editor-tool-link"
          @mousedown.prevent
        >
          <Icon name="link" :size="14" />
        </Button>
      </template>
      <form class="et-link" @submit.prevent="submitLink">
        <label class="et-link-label" for="editor-link-url">Link URL</label>
        <Input
          id="editor-link-url" ref="urlInput" v-model="url"
          placeholder="https://" data-testid="editor-link-url"
        />
      </form>
    </Popover>
    <template v-if="mode === 'rich'">
      <span class="et-sep" aria-hidden="true" />
      <Button
        variant="ghost" size="sm" class="et-btn"
        aria-label="Insert table" title="Insert table" data-testid="editor-tool-table"
        @mousedown.prevent @click="insertTable"
      >
        <Icon name="table" :size="14" />
      </Button>
      <template v-if="inTable">
        <Button
          v-for="t in TABLE_TOOLS" :key="t.kind"
          variant="ghost" size="sm" class="et-btn"
          :aria-label="t.label" :title="t.label" :data-testid="`editor-tool-${t.kind}`"
          @mousedown.prevent @click="t.run"
        >
          <Icon :name="t.icon" :size="14" />
        </Button>
      </template>
    </template>
  </div>
</template>

<style scoped>
.editor-toolbar { display: flex; align-items: center; gap: 0.125rem; flex-wrap: wrap; }
.et-btn { padding-inline: 0.375rem; }
.et-sep {
    width: 1px; height: 1rem; margin-inline: 0.25rem;
    background: var(--color-border-subtle);
}
</style>

<!-- Not scoped: the popover is portalled to <body>, beyond the scope id. -->
<style>
.et-link { display: grid; gap: 0.375rem; width: 16rem; }
.et-link-label { font-size: 0.75rem; color: var(--color-fg-muted); }
</style>
