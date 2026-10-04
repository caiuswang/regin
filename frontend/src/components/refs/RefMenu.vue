<script setup>
// The `@` reference menu: groups of rows under one listbox, placed under the
// caret's line.
//
// It never takes focus. The textarea keeps it, and with it the caret and the
// combobox semantics, so every mousedown here is prevented and a click only
// picks. Placement is written straight onto the element: it depends on the
// menu's own measured height, and routing it through reactive state would
// re-render the menu to measure the menu.
import { nextTick, onMounted, ref, watch } from 'vue'
import Icon from '../ui/Icon.vue'
import { KIND_LABELS } from '../../composables/useRefMentions.js'
import { REF_GLYPHS as GLYPHS, shortKey } from './refDisplay.js'

const props = defineProps({
  listId: { type: String, required: true },
  sections: { type: Array, default: () => [] },
  activeIndex: { type: Number, default: 0 },
  stale: { type: Boolean, default: false },
  loading: { type: Boolean, default: false },
  note: { type: Object, default: null },
  // The selected project's id: rows from any other project name theirs.
  project: { type: Number, default: null },
  // `{ left, lineTop, lineBottom, boxLeft, boxWidth }`, viewport pixels.
  anchor: { type: Object, default: null },
})
defineEmits(['pick', 'hover', 'drill'])

const GAP = 4
const EDGE = 8
const NARROW = 640

const root = ref(null)

const optionId = (index) => `${props.listId}-opt-${index}`
const headId = (section) => `${props.listId}-head-${section.key}`

function foreign(item) {
  return item.project && item.project_id !== props.project
}

function place() {
  const el = root.value
  const at = props.anchor
  if (!el || !at) return
  const vw = document.documentElement.clientWidth
  const vh = window.innerHeight
  const rem = Number.parseFloat(getComputedStyle(document.documentElement).fontSize) || 16
  const narrow = vw < NARROW
  const side = narrow ? 0 : rem
  const width = narrow ? Math.min(at.boxWidth, vw) : Math.min(28 * rem, vw - 2 * rem)
  const wanted = narrow ? at.boxLeft : at.left
  const left = Math.max(side, Math.min(wanted, vw - side - width))
  const natural = Math.min(20 * rem, el.scrollHeight)
  const below = vh - at.lineBottom - GAP - EDGE
  const above = at.lineTop - GAP - EDGE
  const flip = natural > below && above > below
  el.style.left = `${left}px`
  el.style.width = `${width}px`
  el.style.maxHeight = `${Math.max(0, Math.min(20 * rem, flip ? above : below))}px`
  el.style.top = flip ? '' : `${at.lineBottom + GAP}px`
  el.style.bottom = flip ? `${vh - at.lineTop + GAP}px` : ''
}

// Scrolls this list only. scrollIntoView would also scroll the dialog or the
// page under it, moving the very textarea the menu is anchored to.
function reveal() {
  const box = root.value
  const row = box && document.getElementById(optionId(props.activeIndex))
  if (!row) return
  if (props.activeIndex === 0) {
    box.scrollTop = 0
    return
  }
  const head = row.previousElementSibling?.matches('.ref-menu-head') ? row.previousElementSibling : row
  const bottom = row.offsetTop + row.offsetHeight
  if (head.offsetTop < box.scrollTop) box.scrollTop = head.offsetTop
  else if (bottom > box.scrollTop + box.clientHeight) box.scrollTop = bottom - box.clientHeight
}

// Bound here rather than in the template: the root is no clickable surface,
// it only keeps a press anywhere on it, a scrollbar or a header included,
// from blurring the textarea and closing the menu mid-click.
function keepFocus(event) {
  event.preventDefault()
}

onMounted(() => {
  root.value?.addEventListener('mousedown', keepFocus)
  place()
})
watch(() => [props.anchor, props.sections, props.note], place, { flush: 'post' })
watch(() => props.activeIndex, () => nextTick(reveal))
</script>

<template>
  <div
    ref="root"
    class="ref-menu"
    :class="{ 'is-stale': stale }"
    data-ref-menu
    data-testid="ref-menu"
  >
    <p
      v-if="note"
      class="ref-menu-note"
      :data-note="note.kind"
      data-testid="ref-menu-note"
      role="status"
    >
      <span v-if="note.kind === 'busy' || loading" class="ref-menu-spinner" aria-hidden="true" />
      {{ note.text }}
    </p>
    <div :id="listId" role="listbox" aria-label="References" class="ref-menu-list">
      <div
        v-for="(section, s) in sections"
        :key="section.key"
        role="group"
        :aria-labelledby="headId(section)"
        :data-testid="`ref-menu-group-${section.key}`"
      >
        <div
          v-if="section.kind"
          :id="headId(section)"
          class="ref-menu-head cursor-pointer hover:text-fg"
          :title="`Search ${KIND_LABELS[section.kind].toLowerCase()}s only`"
          data-testid="ref-menu-head"
          @click="$emit('drill', section.kind)"
        >
          <span>{{ section.label }}</span>
          <span v-if="s === 0 && loading" class="ref-menu-spinner" aria-hidden="true" />
        </div>
        <div v-else :id="headId(section)" class="ref-menu-head" data-testid="ref-menu-head">
          <span>{{ section.label }}</span>
          <span v-if="s === 0 && loading" class="ref-menu-spinner" aria-hidden="true" />
        </div>
        <p v-if="section.hint" class="ref-menu-hint" data-testid="ref-menu-hint">{{ section.hint }}</p>
        <div
          v-for="row in section.rows"
          :id="optionId(row.index)"
          :key="row.id"
          role="option"
          :aria-selected="row.index === activeIndex"
          class="ref-menu-row cursor-pointer hover:bg-surface-selected"
          :class="{ 'is-active': row.index === activeIndex }"
          :data-row-type="row.type"
          :data-kind="row.kind"
          :data-ref="row.item?.ref"
          data-testid="ref-menu-row"
          @mousemove="row.index !== activeIndex && $emit('hover', row.index)"
          @click="$emit('pick', row)"
        >
          <Icon :name="GLYPHS[row.kind]" :size="13" class="ref-menu-glyph" />
          <template v-if="row.type === 'item'">
            <span
              class="ref-menu-key"
              :class="{ 'is-path': row.kind === 'file' }"
              :title="row.kind === 'file' ? shortKey(row.item) : undefined"
            >{{ shortKey(row.item) }}</span>
            <span v-if="row.kind !== 'file'" class="ref-menu-title">{{ row.item.title }}</span>
            <span v-if="row.item.status" class="ref-menu-status">{{ row.item.status }}</span>
            <span v-if="foreign(row.item)" class="ref-menu-project">{{ row.item.project }}</span>
          </template>
          <template v-else>
            <span class="ref-menu-title">{{ KIND_LABELS[row.kind] }}</span>
            <span class="ref-menu-prefix">@{{ row.kind }}:</span>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* Above --z-modal: the menu is teleported to <body> from inside dialogs, and
   reka parks pointer-events:none on <body> while one is open. */
.ref-menu {
    position: fixed;
    z-index: var(--z-popover);
    pointer-events: auto;
    width: min(28rem, calc(100vw - 2rem));
    max-height: 20rem;
    overflow-y: auto;
    overscroll-behavior: contain;
    padding: 0.25rem;
    background: var(--color-surface);
    color: var(--color-fg);
    border: 1px solid var(--color-border);
    border-radius: var(--radius-lg);
    box-shadow: var(--ds-shadow-lg);
    font-size: 0.8125rem;
}
.ref-menu.is-stale .ref-menu-list,
.ref-menu.is-stale .ref-menu-note:not([data-note="busy"]) { opacity: 0.55; }
.ref-menu-note,
.ref-menu-hint {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.5rem;
    font-size: 0.75rem;
    color: var(--color-fg-muted);
}
.ref-menu-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.5rem;
    padding: 0.375rem 0.5rem 0.25rem;
    font-size: 0.6875rem;
    font-weight: 600;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    color: var(--color-fg-subtle);
}
.ref-menu-row {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    min-width: 0;
    padding: 0.375rem 0.5rem;
    border-radius: var(--radius-md);
}
.ref-menu-row.is-active { background: var(--color-surface-selected); }
.ref-menu-glyph { flex: none; color: var(--color-fg-subtle); }
.ref-menu-key {
    flex: none;
    font-family: var(--font-mono, ui-monospace, monospace);
    font-size: 0.75rem;
    color: var(--color-fg-muted);
}
.ref-menu-key.is-path {
    flex: 1 1 auto;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    color: var(--color-fg);
}
.ref-menu-title {
    flex: 1 1 auto;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}
.ref-menu-status {
    flex: none;
    padding: 0 0.375rem;
    border: 1px solid var(--color-border);
    border-radius: 999px;
    font-size: 0.6875rem;
    color: var(--color-fg-muted);
    white-space: nowrap;
}
.ref-menu-project {
    flex: 0 1 auto;
    min-width: 0;
    max-width: 8rem;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 0.6875rem;
    color: var(--color-fg-subtle);
}
.ref-menu-prefix {
    flex: none;
    font-family: var(--font-mono, ui-monospace, monospace);
    font-size: 0.6875rem;
    color: var(--color-fg-subtle);
}
.ref-menu-spinner {
    flex: none;
    width: 0.75rem;
    height: 0.75rem;
    border: 2px solid currentColor;
    border-right-color: transparent;
    border-radius: 50%;
    animation: ref-menu-spin 0.7s linear infinite;
}
@keyframes ref-menu-spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) {
    .ref-menu-spinner { animation-duration: 2s; }
}
</style>
