<script setup>
// Markdown marks for the source field of `MarkdownEditor`. Each button writes
// one replacement through the field's `replaceRange`, the browser's own
// insert, so one undo takes the source and its selection back to before the
// press; rewriting the model instead would empty the undo history.
//
// The buttons keep the focus in the field (`mousedown.prevent`), so the
// selection a press acts on is the one the writer made.
import { nextTick, ref } from 'vue'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import Input from '../ui/Input.vue'
import Popover from '../ui/Popover.vue'
import { linkEdit, markEdit } from '../../utils/markdownMarks.js'

const props = defineProps({
  // The `RefTextarea` instance: `textarea`, `replaceRange`, `setSelection`.
  field: { type: Object, default: null },
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

// `from` is the selection the press acted on, which one undo puts back.
function write(edit, from) {
  props.field.replaceRange(edit.start, edit.end, edit.text, from)
  props.field.setSelection(...edit.select)
}

function mark(kind) {
  const el = props.field?.textarea
  if (!el) return
  const { selectionStart: start, selectionEnd: end } = el
  write(markEdit(kind, el.value, start, end), [start, end])
}

// The link's range is read when the popover opens: typing the url moves the
// focus, and the field's selection is what the link is made of.
const linkOpen = ref(false)
// Plain text, not `type="url"`: the browser's check refuses the relative
// paths and anchors a spec links to (`docs/a.md`, `#rules`), and refuses
// them silently, by not submitting.
const url = ref('')
const urlInput = ref(null)
let range = null
let pending = null

function onLinkOpen(open) {
  linkOpen.value = open
  if (!open) return
  const el = props.field?.textarea
  range = el ? { start: el.selectionStart, end: el.selectionEnd } : null
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

// The edit waits for the close, not the Enter: Reka returns the focus to the
// trigger as the panel goes, and the field must hold it for the insert to be
// an undo step. A close without a url (Escape, a click away) puts the
// selection back untouched, unless the click away landed in the field.
function onLinkClosed(event) {
  event.preventDefault()
  const el = props.field?.textarea
  if (!el || !range) return
  if (pending) write(linkEdit(el.value, range.start, range.end, pending), [range.start, range.end])
  else if (document.activeElement !== el) props.field.setSelection(range.start, range.end)
  pending = null
  range = null
}
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
  </div>
</template>

<style scoped>
.editor-toolbar { display: flex; align-items: center; gap: 0.125rem; flex-wrap: wrap; }
.et-btn { padding-inline: 0.375rem; }
</style>

<!-- Not scoped: the popover is portalled to <body>, beyond the scope id. -->
<style>
.et-link { display: grid; gap: 0.375rem; width: 16rem; }
.et-link-label { font-size: 0.75rem; color: var(--color-fg-muted); }
</style>
