<script setup>
// A body textarea that takes `@` references: type `@`, search regin, pick a
// row, and `[label](kind:key)` is written over what was typed.
//
// A drop-in for `ui/Textarea` at the body editors, so it leaves what they
// already do alone: one element root, which they `v-show`; their attributes
// and listeners on the textarea itself; the textarea exposed for
// `utils/caret.js`; and paste or drop of images left to their own handlers.
//
// Every edit goes through the browser's editing stack rather than a rewrite
// of the model. A rewrite empties the undo history, and one undo is what
// takes an accept back to the typed `@query`.
import { computed, onBeforeUnmount, ref, useId, watch } from 'vue'
import api from '../../api'
import RefHighlight from './RefHighlight.vue'
import RefMenu from './RefMenu.vue'
import Textarea from '../ui/Textarea.vue'
import { refForUrl, useRefMentions } from '../../composables/useRefMentions.js'
import { useRefPreview } from '../../composables/useRefPreview.js'
import { caretPoint } from '../../utils/caretPoint.js'
import { cn } from '../../utils/cn'
import { imageFilesFrom } from '../../utils/imageFiles'
import { refEndingAt } from '../../utils/refs.js'

defineOptions({ inheritAttrs: false })
const props = defineProps({
  modelValue: { type: String, default: '' },
  // A repo id, or nothing. Recent work and file search are scoped to it.
  project: { type: [Number, String], default: null },
  // The feature this body belongs to, or nothing: it and its work lead the menu.
  feature: { type: [Number, String], default: null },
  error: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue'])
const field = ref(null)
const textarea = computed(() => field.value?.$el ?? null)
defineExpose({ textarea })

const menu = useRefMentions({ project: () => props.project, feature: () => props.feature })
const {
  open, sections, rows, activeIndex, stale, loading, note, selected, context, query, tokenStart,
} = menu
const preview = useRefPreview(() => {
  const row = open.value ? rows.value[activeIndex.value] : null
  return row?.type === 'item' ? { ref: row.item.ref, project: selected.value } : null
})
const listId = `ref-menu-${useId()}`
const anchor = ref(null)
let composing = false

// The parent's scoped styles key off the scope id Vue puts on this root, so
// its class and style stay here. Everything else describes the field. Split
// at render, since `$attrs` is not reactive state a computed could follow.
function fieldAttrs(all) {
  const { class: _class, style: _style, ...rest } = all
  return rest
}

// The root is positioned: the highlight is laid over the field inside it.
// Unstyled, it stands in the parent's flow where the bare field stood: a full
// width inline block whose baseline is its bottom edge, as the field's was,
// holding the field as a block so no line box adds a gap under it; in a flex
// column it is blockified like the field was. A styled root is a block of
// its own, because a margin the parent put on it has to land on something.
function rootClass(all) {
  return all.class || all.style ? cn('ref-textarea', all.class) : 'ref-textarea is-bare'
}
const activeId = computed(() => (open.value && rows.value.length
  ? `${listId}-opt-${activeIndex.value}` : undefined))

function anchorFor(el) {
  const box = el.getBoundingClientRect()
  const line = caretPoint(el, el.selectionEnd)
  const within = (y) => Math.min(Math.max(y, box.top), box.bottom)
  return {
    left: caretPoint(el, tokenStart.value ?? el.selectionEnd).left,
    lineTop: within(line.top),
    lineBottom: within(line.top + line.height),
    boxLeft: box.left,
    boxWidth: box.width,
  }
}

function syncMenu() {
  const el = textarea.value
  if (!el) return
  menu.sync(el.value, el.selectionStart, el.selectionEnd)
  if (open.value) anchor.value = anchorFor(el)
}

function onInput(event) {
  if (composing || event.isComposing) return
  syncMenu()
}

// The caret moved without an edit: re-read an open menu only, so it closes
// once the caret leaves the token. A closed menu never reopens here, or the
// keyup after Escape would bring it straight back. Typing is what opens it.
function onCaretMove() {
  if (open.value && !composing) syncMenu()
}

function onCompositionStart() {
  composing = true
}

// Nothing is searched mid-composition. The committed text is read once,
// here, so `@新建` searches 新建 rather than the pinyin on the way to it.
function onCompositionEnd() {
  composing = false
  syncMenu()
}

function execEdit(text) {
  try {
    return text
      ? document.execCommand('insertText', false, text)
      : document.execCommand('delete', false)
  } catch {
    return false
  }
}

// The browser's own insert, so the edit is one undo step. A browser that
// refuses it gets the same text without the history, and the input event
// v-model and the menu both listen for.
function replaceRange(start, end, text) {
  const el = textarea.value
  if (!el) return
  el.setSelectionRange(start, end)
  if (document.activeElement === el && execEdit(text)) return
  el.setRangeText(text, start, end, 'end')
  el.dispatchEvent(new Event('input', { bubbles: true }))
}

function accept(row) {
  const edit = menu.editFor(row)
  const el = textarea.value
  if (!edit || !el) return
  if (el.value.slice(edit.start, edit.end) === edit.text) return
  replaceRange(edit.start, edit.end, edit.text)
}

// Backspace right after a reference's `)` takes the whole reference.
function removeReference(event) {
  const el = event.target
  if (el.selectionStart !== el.selectionEnd) return
  const found = refEndingAt(el.value, el.selectionStart)
  if (!found) return
  event.preventDefault()
  replaceRange(found.start, found.end, '')
}

function onKeydown(event) {
  if (composing || event.isComposing || event.keyCode === 229) return
  const res = menu.handleKeydown(event)
  if (res.handled) {
    // Immediate, not just propagation: a host's own keydown listener sits on
    // this same textarea, and a key the menu used must not also submit.
    event.preventDefault()
    event.stopImmediatePropagation()
    if (res.row) accept(res.row)
    return
  }
  if (event.key === 'Backspace' && !event.metaKey && !event.ctrlKey && !event.altKey) {
    removeReference(event)
  }
}

async function resolveRef(ref) {
  try {
    const res = await api.get(`/refs/resolve?ref=${encodeURIComponent(ref)}`)
    return res?.refs?.[ref] ?? null
  } catch {
    return null
  }
}

// A pasted URL of a feature, task, defect or session becomes its
// reference. One that resolves to nothing, or to a session this viewer may
// not read, is pasted as it was.
async function onPaste(event) {
  if (event.defaultPrevented || imageFilesFrom(event.clipboardData).length) return
  const text = event.clipboardData?.getData('text/plain') ?? ''
  const ref = refForUrl(text, window.location.origin)
  if (!ref) return
  event.preventDefault()
  const el = event.target
  const at = { start: el.selectionStart, end: el.selectionEnd, value: el.value }
  const item = await resolveRef(ref)
  const insert = item && !item.restricted ? `[${item.label}](${item.ref}) ` : text
  // Typed over while the lookup was out: the remembered range no longer
  // names the paste's place, the caret does.
  const moved = el.value !== at.value
  replaceRange(moved ? el.selectionStart : at.start, moved ? el.selectionEnd : at.end, insert)
}

function onViewportShift(event) {
  if (event?.target instanceof Element && event.target.closest('[data-ref-menu]')) return
  const el = textarea.value
  if (el && open.value) anchor.value = anchorFor(el)
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
  track(isOpen)
  if (!isOpen) preview.reset()
})
onBeforeUnmount(() => {
  track(false)
  menu.close()
})
</script>

<template>
  <div :class="rootClass($attrs)" :style="$attrs.style">
    <Textarea
      ref="field"
      @keydown="onKeydown"
      @input="onInput"
      @keyup="onCaretMove"
      @click="onCaretMove"
      @compositionstart="onCompositionStart"
      @compositionend="onCompositionEnd"
      @paste="onPaste"
      @blur="menu.close()"
      v-bind="fieldAttrs($attrs)"
      :model-value="modelValue"
      :error="error"
      role="combobox"
      aria-autocomplete="list"
      :aria-expanded="open ? 'true' : 'false'"
      :aria-controls="open ? listId : undefined"
      :aria-activedescendant="activeId"
      @update:model-value="emit('update:modelValue', $event)"
    />
    <RefHighlight :text="modelValue" :field="textarea" />
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
    </Teleport>
  </div>
</template>

<style scoped>
.ref-textarea { position: relative; }
.ref-textarea.is-bare {
    display: inline-block;
    width: 100%;
    vertical-align: baseline;
}
.ref-textarea.is-bare > textarea { display: block; }
</style>
