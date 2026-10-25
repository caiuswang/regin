<script setup>
// The `/` menu's list: the items `editor/slash.js` offers for the query,
// grouped, placed under the caret's line (over it when the viewport has no
// room below). Drawn like `RefMenu` and, like it, never takes focus: every
// mousedown is prevented so the editor keeps the caret and a click only picks.
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import Icon from '../ui/Icon.vue'
import { SLASH_GROUPS } from '../../editor/slash.js'

const props = defineProps({
  items: { type: Array, default: () => [] },
  activeIndex: { type: Number, default: 0 },
  // `{ left, lineTop, lineBottom }`, viewport pixels.
  anchor: { type: Object, default: null },
})
defineEmits(['pick', 'hover'])

const GAP = 4
const EDGE = 8

const root = ref(null)
const list = ref(null)

const groups = computed(() => SLASH_GROUPS
  .map((group) => ({
    group,
    rows: props.items
      .map((item, index) => ({ item, index }))
      .filter(({ item }) => item.group === group),
  }))
  .filter((g) => g.rows.length))

function place() {
  const el = root.value
  const at = props.anchor
  if (!el || !at) return
  const vw = document.documentElement.clientWidth
  const vh = window.innerHeight
  const width = el.offsetWidth
  const natural = el.scrollHeight
  const below = vh - at.lineBottom - GAP - EDGE
  const above = at.lineTop - GAP - EDGE
  const flip = natural > below && above > below
  el.style.left = `${Math.max(EDGE, Math.min(at.left, vw - EDGE - width))}px`
  el.style.maxHeight = `${Math.max(0, flip ? above : below)}px`
  el.style.top = flip ? '' : `${at.lineBottom + GAP}px`
  el.style.bottom = flip ? `${vh - at.lineTop + GAP}px` : ''
}

// Scrolls this list only, never the page the editor sits in.
function reveal() {
  const box = list.value
  const row = box?.querySelector(`[data-index="${props.activeIndex}"]`)
  if (!row) return
  const bottom = row.offsetTop + row.offsetHeight
  if (row.offsetTop < box.scrollTop) box.scrollTop = row.offsetTop
  else if (bottom > box.scrollTop + box.clientHeight) box.scrollTop = bottom - box.clientHeight
}

onMounted(() => {
  root.value?.addEventListener('mousedown', (event) => event.preventDefault())
  place()
})
watch(() => [props.anchor, props.items], place, { flush: 'post' })
watch(() => props.activeIndex, () => nextTick(reveal))
</script>

<template>
  <div ref="root" class="slash-menu" data-slash-menu data-testid="slash-menu">
    <div ref="list" class="slash-menu-list" role="listbox" aria-label="Insert block">
      <p v-if="!items.length" class="slash-menu-note" data-testid="slash-menu-empty">No matching blocks</p>
      <div
        v-for="g in groups" :key="g.group"
        role="group" :aria-label="g.group"
        :data-testid="`slash-menu-group-${g.group}`"
      >
        <div class="slash-menu-head">{{ g.group }}</div>
        <div
          v-for="{ item, index } in g.rows" :key="item.id"
          role="option" :aria-selected="index === activeIndex"
          class="slash-menu-row cursor-pointer hover:bg-surface-selected"
          :class="{ 'is-active': index === activeIndex }"
          :data-index="index" :data-id="item.id"
          data-testid="slash-menu-item"
          @mousemove="index !== activeIndex && $emit('hover', index)"
          @click="$emit('pick', item)"
        >
          <Icon :name="item.icon" :size="13" class="slash-menu-glyph" />
          <span class="slash-menu-label">{{ item.label }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* `RefMenu`'s surface: the same layer, border, radius, shadow and row. */
.slash-menu {
    position: fixed;
    z-index: var(--z-popover);
    pointer-events: auto;
    display: flex;
    width: 15rem;
    max-height: 20rem;
    overflow: hidden;
    background: var(--color-surface);
    color: var(--color-fg);
    border: 1px solid var(--color-border);
    border-radius: var(--radius-lg);
    box-shadow: var(--ds-shadow-lg);
    font-size: 0.8125rem;
}
.slash-menu-list {
    flex: 1 1 auto;
    min-height: 0;
    overflow-y: auto;
    overscroll-behavior: contain;
    padding: 0.25rem;
}
.slash-menu-note {
    padding: 0.5rem;
    font-size: 0.75rem;
    color: var(--color-fg-muted);
}
.slash-menu-head {
    padding: 0.375rem 0.5rem 0.25rem;
    font-size: 0.6875rem;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    color: var(--color-fg-subtle);
}
.slash-menu-row {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    min-width: 0;
    padding: 0.375rem 0.5rem;
    border-radius: var(--radius-md);
}
.slash-menu-row.is-active { background: var(--color-surface-selected); }
.slash-menu-glyph { flex: none; color: var(--color-fg-subtle); }
.slash-menu-label {
    flex: 1 1 auto;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}
</style>
