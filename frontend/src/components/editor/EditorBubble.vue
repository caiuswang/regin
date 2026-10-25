<script setup>
// The bar over a text selection (`editor/bubble.js` says when it shows and
// what each item does). Tiptap's `BubbleMenu` places it above the selection
// and flips it under when the scroller has no room above. It stays inside the
// editor's own box rather than going to <body>: Tiptap reads a blur toward
// anything in the bar's parent as staying in the editor, so on <body> the bar
// would outlive the focus moving into a popover.
//
// The page's scroller is the room it has, less the editor's head where that
// sticks over the top of it: above a first line under the head the bar would
// be drawn behind the head, so there it flips under the selection instead.
//
// `updateDelay: 0` moves the bar on the selection's own transaction: the
// default debounces it.
//
// Buttons keep the focus in the face (`mousedown.prevent`), as the toolbar's
// do. Link is the head's link popover, the one ⌘K opens.
//
// Tiptap lets the first blur after any mousedown on the bar go by, expecting
// the press to have moved the focus into the bar. Here it never does, so that
// blur is the focus leaving for a popover, and the bar is hidden on it.
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { PluginKey } from '@tiptap/pm/state'
import { BubbleMenu } from '@tiptap/vue-3/menus'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import { BUBBLE_ITEMS, bubbleShows, insertReference, runBubbleItem } from '../../editor/bubble.js'

const props = defineProps({
  editor: { type: Object, required: true },
})
const emit = defineEmits(['link'])

const visible = ref(false)
const pluginKey = new PluginKey('editorBubble')

function onBlur() {
  const ed = props.editor
  if (visible.value && !ed.isDestroyed) ed.view.dispatch(ed.state.tr.setMeta(pluginKey, 'hide'))
}
onMounted(() => props.editor.on('blur', onBlur))
onBeforeUnmount(() => props.editor.off('blur', onBlur))
// Re-read on every transaction the bar sees, so the pressed state follows
// the selection.
const active = ref({})

function syncActive() {
  active.value = Object.fromEntries(BUBBLE_ITEMS
    .filter((item) => item.mark)
    .map((item) => [item.id, props.editor.isActive(item.mark)]))
}

function shouldShow({ editor, element, state }) {
  const focused = editor.view.hasFocus() || element.contains(document.activeElement)
  const show = bubbleShows(state, { focused, editable: editor.isEditable })
  if (show) syncActive()
  return show
}

function headOverlap() {
  const host = props.editor.view.dom
  const head = host.closest('[data-testid="markdown-editor"]')?.querySelector('[data-testid="editor-head"]')
  const scroller = host.closest('.content-scroll')
  if (!head) return 0
  const top = scroller ? scroller.getBoundingClientRect().top : 0
  return Math.max(0, head.getBoundingClientRect().bottom - top)
}

const options = {
  placement: 'top',
  offset: 6,
  flip: () => ({ padding: { top: headOverlap() + 4 } }),
  shift: { padding: 8 },
  onShow: () => { visible.value = true },
  onHide: () => { visible.value = false },
}

function press(item) {
  if (item.id === 'link') return emit('link')
  if (item.id === 'reference') return insertReference(props.editor)
  runBubbleItem(props.editor, item)
  syncActive()
}

defineExpose({ visible })
</script>

<template>
  <BubbleMenu
    :editor="editor" :plugin-key="pluginKey" :should-show="shouldShow" :update-delay="0" :options="options"
    class="editor-bubble" role="toolbar" aria-label="Selection formatting"
    data-testid="bubble-menu" data-bubble-menu=""
  >
    <Button
      v-for="item in BUBBLE_ITEMS" :key="item.id"
      variant="ghost" size="sm" class="eb-btn"
      :class="{ 'is-active': active[item.id] }"
      :aria-label="item.label" :title="item.label"
      :aria-pressed="item.mark && item.id !== 'link' ? String(!!active[item.id]) : undefined"
      :data-testid="`bubble-${item.id}`"
      @mousedown.prevent @click="press(item)"
    >
      <Icon :name="item.icon" :size="14" />
    </Button>
  </BubbleMenu>
</template>

<style scoped>
.editor-bubble {
    z-index: 20;
    display: flex; align-items: center; gap: 0.125rem;
    padding: 0.125rem;
    background: var(--color-surface);
    color: var(--color-fg);
    border: 1px solid var(--color-border);
    border-radius: var(--radius-md);
    box-shadow: var(--ds-shadow-lg);
}
.eb-btn { padding-inline: 0.375rem; }
.eb-btn.is-active { background: var(--color-surface-selected); color: var(--color-fg); }
</style>
