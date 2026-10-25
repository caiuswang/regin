<script setup>
// A long body's headings (`editor/outline.js`), as a rail beside the focus
// column or as a head button's list. The host says where each heading is drawn
// (`elementFor`) and, when a stuck head covers the top of the pane, where the
// reading line sits (`edge`, a viewport y); without one the heading's own
// `scroll-margin-top` sets it.
//
// "Where am I" comes from IntersectionObserver callbacks on a one-pixel band
// at the reading line, as in `FeatureSectionNav` — no scroll listener, no
// poll. A picked heading holds the mark until the reader moves the page
// themselves, since one near the foot can never climb to the line.
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import Popover from '../ui/Popover.vue'

const props = defineProps({
  // OutlineEntry[]: { id, level: 1–4, text }
  entries: { type: Array, required: true },
  variant: { type: String, default: 'rail' },
  elementFor: { type: Function, required: true },
  edge: { type: Function, default: null },
  // Changes whenever `edge` would answer differently (the stuck head's
  // height), so the reading-line band is laid again.
  edgeKey: { type: Number, default: 0 },
})
const emit = defineEmits(['pick'])

const SCROLLER = '.content-scroll'
// The band sits this far under the reading line, so a heading just landed on
// the line already counts as crossed.
const LINE_BELOW = 8
const RELEASE_ON = ['wheel', 'touchstart', 'keydown', 'pointerdown']

const el = ref(null)
const panel = ref(null)
const open = ref(false)
const current = ref('')
const items = computed(() => props.entries.map((e) => ({ ...e, active: e.id === current.value })))

let observer = null
let scroller = null
let pinned = ''
let picked = false

function lineY(target) {
  if (props.edge) return props.edge()
  const top = scroller ? scroller.getBoundingClientRect().top : 0
  return top + (target ? parseFloat(getComputedStyle(target).scrollMarginTop) || 0 : 0)
}

function settle() {
  if (pinned) {
    current.value = pinned
    return
  }
  let hit = props.entries[0]?.id ?? ''
  for (const entry of props.entries) {
    const target = props.elementFor(entry)
    if (target && target.getBoundingClientRect().top <= lineY(target) + LINE_BELOW) hit = entry.id
  }
  current.value = hit
}

function release() {
  pinned = ''
  settle()
}

function disconnect() {
  observer?.disconnect()
  observer = null
  for (const kind of RELEASE_ON) scroller?.removeEventListener(kind, release)
}

function observe() {
  disconnect()
  scroller = el.value?.closest(SCROLLER) ?? null
  const targets = props.entries.map((e) => props.elementFor(e)).filter(Boolean)
  if (!targets.length) {
    current.value = ''
    return
  }
  const top = scroller ? scroller.getBoundingClientRect().top : 0
  const line = Math.max(0, Math.round(lineY(targets[0]) - top + LINE_BELOW))
  const height = scroller?.clientHeight ?? window.innerHeight
  observer = new IntersectionObserver(settle, {
    root: scroller, rootMargin: `-${line}px 0px -${Math.max(0, height - line - 1)}px 0px`,
  })
  for (const target of targets) observer.observe(target)
  for (const kind of RELEASE_ON) scroller?.addEventListener(kind, release, { passive: true })
}

onMounted(() => nextTick(observe))
watch(() => [props.entries, props.edgeKey], () => nextTick(observe))
onBeforeUnmount(disconnect)

// The pane's own scroller, not `scrollIntoView`: that would also scroll every
// ancestor, and lands the heading under the stuck head rather than below it.
function scrollTo(target) {
  if (!scroller) return target.scrollIntoView({ block: 'start' })
  scroller.scrollTop += target.getBoundingClientRect().top - lineY(target)
}

function jump(entry) {
  picked = true
  emit('pick', entry)
  const target = props.elementFor(entry)
  if (target) scrollTo(target)
  pinned = entry.id
  current.value = entry.id
  open.value = false
}

// Reka hands the focus back to the trigger on close, scrolling the page back
// up to it. After a pick the focus stays where the pick put it (the rich
// face, at the heading), or goes to the trigger without moving the page.
function onClosed(event) {
  if (!picked) return
  picked = false
  event.preventDefault()
  const active = document.activeElement
  if (!active || active === document.body || panel.value?.contains(active)) {
    el.value?.querySelector('button')?.focus({ preventScroll: true })
  }
}
</script>

<template>
  <nav v-if="variant === 'rail'" ref="el" class="ol-rail" aria-label="Outline" data-testid="outline-rail">
    <p class="ol-caption">Outline</p>
    <p v-if="!items.length" class="ol-empty">No headings</p>
    <ul v-else class="ol-list">
      <li v-for="e in items" :key="e.id">
        <Button
          variant="ghost" size="sm" :class="['ol-item', `ol-l${e.level}`]" :title="e.text"
          :aria-current="e.active ? 'true' : undefined" data-testid="outline-item"
          @click="jump(e)"
        ><span class="ol-text">{{ e.text || 'Untitled' }}</span></Button>
      </li>
    </ul>
  </nav>
  <span v-else ref="el" class="ol-drop">
    <Popover v-model:open="open" side="bottom" align="start" @close-auto-focus="onClosed">
      <template #trigger>
        <Button variant="ghost" size="sm" data-testid="outline-toggle" title="Outline" @mousedown.prevent>
          <Icon name="list" :size="13" />
          <span>Outline</span>
        </Button>
      </template>
      <nav ref="panel" class="ol-panel" aria-label="Outline" data-testid="outline-list">
        <ul class="ol-list">
          <li v-for="e in items" :key="e.id">
            <Button
              variant="ghost" size="sm" :class="['ol-item', `ol-l${e.level}`]" :title="e.text"
              :aria-current="e.active ? 'true' : undefined" data-testid="outline-item"
              @click="jump(e)"
            ><span class="ol-text">{{ e.text || 'Untitled' }}</span></Button>
          </li>
        </ul>
      </nav>
    </Popover>
  </span>
</template>

<!-- Not scoped: the dropdown's list is portalled to <body>, beyond the scope id. -->
<style>
.ol-rail { font-size: 0.75rem; }
.ol-caption {
    margin-bottom: 0.25rem; padding-left: 0.5rem;
    font-size: 0.6875rem; font-weight: 600; color: var(--color-fg-subtle);
    text-transform: uppercase; letter-spacing: 0.04em;
}
.ol-empty { padding-left: 0.5rem; color: var(--color-fg-faint); }
.ol-drop { display: inline-flex; }
.ol-panel { max-height: 60vh; overflow-y: auto; min-width: 12rem; font-size: 0.75rem; }
.ol-list { list-style: none; margin: 0; padding: 0; }
.ol-item.ol-item {
    display: flex; justify-content: flex-start; width: 100%; min-width: 0;
    height: auto; padding: 0.1875rem 0.5rem; font-weight: 400;
}
.ol-text { min-width: 0; overflow: hidden; text-overflow: ellipsis; }
.ol-item[aria-current="true"] { color: var(--color-fg); font-weight: 600; background: var(--color-surface-2); }
.ol-l2 { padding-left: 1.25rem; }
.ol-l3 { padding-left: 2rem; }
.ol-l4 { padding-left: 2.75rem; }
</style>
