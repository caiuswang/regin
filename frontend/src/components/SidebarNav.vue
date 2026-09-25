<script setup>
import { computed, onMounted, onUnmounted } from 'vue'
import NavIcon from './NavIcon.vue'
import Button from './ui/Button.vue'

const props = defineProps({
  navGroups: { type: Array, required: true },
  isActiveLink: { type: Function, required: true },
  iconOnly: { type: Boolean, default: false },
})

const emit = defineEmits(['navigate', 'preview'])

// The rail hides link text when collapsed, so the accessible name has to carry
// both the label and any badge count that is no longer rendered as a number.
function linkName(link) {
  const count = link.badge?.()
  return count ? `${link.label} (${count})` : link.label
}

// Flat address space for the delegated preview handlers below: one listener on
// the nav beats four per row, and the row only has to carry its index.
const flatLinks = computed(() => props.navGroups.flatMap(
  g => g.links.map(link => ({ link, group: g.label }))))

const indexedGroups = computed(() => {
  let i = 0
  return props.navGroups.map(g => ({
    label: g.label,
    links: g.links.map(link => ({ link, index: i++ })),
  }))
})

// The row it is anchored to, so a scroll of the nav can re-anchor the open
// card instead of stranding it at the coordinates the hover captured.
let anchoredEl = null

function describe(row, el) {
  const box = el.getBoundingClientRect()
  // Anchored off the nav, not the row: the rail pads its rows inward, so a
  // row-relative left edge would put the card back on top of the rail.
  const rail = el.closest('.sb-nav').getBoundingClientRect()
  return {
    group: row.group,
    label: row.link.label,
    icon: row.link.icon,
    badge: row.link.badge?.() || null,
    tone: row.link.tone?.() || 'info',
    active: props.isActiveLink(row.link),
    anchor: { left: Math.round(rail.right + 10), top: Math.round(box.top + box.height / 2) },
  }
}

function previewFor(el) {
  const row = el && flatLinks.value[Number(el.dataset.navIndex)]
  if (!row) return
  anchoredEl = el
  emit('preview', describe(row, el))
}

// Crossing the 2px gutter between two rows must not destroy the card — it has
// to survive to slide. Only leaving the nav entirely closes it.
//
// Touch/pen never fire a real hover-out: a tap synthesizes `pointerover` with
// no matching `pointerleave` until some *later, unrelated* tap happens to
// cross the nav's edge, which read as "the tooltip won't go away until you
// click something". Opening only for `pointerType === 'mouse'` keeps the
// preview a mouse-hover affordance; tap already activates the row directly.
function openPreview(e) {
  if (!props.iconOnly) return
  if (e.pointerType && e.pointerType !== 'mouse') return
  previewFor(e.target.closest?.('[data-nav-index]'))
}

// A tap focuses its button the same way a keyboard Tab would, so `focusin`
// alone can't tell them apart. Remember the pointer type behind the most
// recent focus and let a real key press (Tab included) reclaim it — that's
// the same "what is the user driving with right now" signal a hybrid
// touch+keyboard device needs, without a timer standing in for it.
let focusFromTouch = false

function openPreviewFromFocus(e) {
  if (focusFromTouch) return
  openPreview(e)
}

function closePreview() {
  anchoredEl = null
  emit('preview', null)
}

function onDocumentKeydown() {
  focusFromTouch = false
}

onMounted(() => document.addEventListener('keydown', onDocumentKeydown))
onUnmounted(() => document.removeEventListener('keydown', onDocumentKeydown))

// `previewHandlers` is bound as one v-on object: `pointerover`/`focusin` bubble
// (pointerenter/focus do not), so the nav can own every row's preview.
const previewHandlers = {
  pointerover: openPreview,
  pointerdown: (e) => { focusFromTouch = e.pointerType !== 'mouse' },
  focusin: openPreviewFromFocus,
  pointerleave: closePreview,
  focusout: (e) => {
    if (!e.currentTarget.contains(e.relatedTarget)) closePreview()
  },
  scroll: () => { if (anchoredEl) previewFor(anchoredEl) },
}
</script>

<template>
  <nav class="sb-nav" :class="{ 'is-rail': iconOnly }" v-on="previewHandlers">
    <div
      v-for="group in indexedGroups"
      :key="group.label"
      class="sb-group"
      role="group"
      :aria-label="group.label"
    >
      <div v-if="!iconOnly" class="sb-section-label">{{ group.label }}</div>
      <div v-else class="sb-section-rule" aria-hidden="true" />
      <Button
        v-for="row in group.links"
        :key="row.link.to"
        variant="ghost"
        size="md"
        class="sb-item sb-focus focus-visible:outline-2 focus-visible:outline-blue-500"
        :class="{ 'is-active': isActiveLink(row.link) }"
        :data-nav-index="row.index"
        :aria-label="linkName(row.link)"
        :aria-current="isActiveLink(row.link) ? 'page' : undefined"
        @click="emit('navigate', row.link)"
      >
        <NavIcon :name="row.link.icon" />
        <span v-if="!iconOnly" class="sb-item-label">{{ row.link.label }}</span>
        <span
          v-if="!iconOnly && row.link.badge?.()"
          class="sb-badge"
          :class="`sb-badge-${row.link.tone?.() || 'info'}`"
        >{{ row.link.badge() }}</span>
        <span
          v-else-if="iconOnly && row.link.badge?.()"
          class="sb-badge-dot"
          :class="`sb-badge-dot-${row.link.tone?.() || 'info'}`"
          aria-hidden="true"
        />
      </Button>
    </div>
  </nav>
</template>

<style scoped>
/* The rail's controls borrow the Button primitive for its disabled handling and
   keyboard semantics, but the rail's own geometry has to win over the variant's
   height/justify/gap/radius utilities — scoped rules are unlayered, so those are
   pinned explicitly below. The variant's focus RING is the one thing scoped
   geometry can't override by pinning geometry, since it paints via box-shadow:
   `.sb-focus` resets that so the rail shows a single outline, not outline+ring.
   `.sb-item.is-active` already carries its own box-shadow and so never rang. */
.sb-focus:focus-visible {
  outline: 2px solid var(--color-blue-500);
  outline-offset: 2px;
  box-shadow: none;
}

.sb-nav {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 0.25rem 0.75rem 0.5rem;
}

.sb-nav.is-rail { padding: 0.25rem 1rem 0.5rem; }

.sb-section-label {
  font-size: 0.59375rem;
  text-transform: uppercase;
  letter-spacing: 0.12em;
  color: var(--color-slate-400);
  font-weight: 700;
  padding: 0 0.75rem;
  margin: 1rem 0 0.375rem;
  white-space: nowrap;
}

.sb-group:first-child .sb-section-label { margin-top: 0; }

/* The resting rail has no room for a group heading; a hairline keeps the
   grouping visible while role="group" + aria-label keeps it announced. */
.sb-section-rule {
  height: 1px;
  background: var(--color-slate-200);
  margin: 0.6875rem 0.5rem 0.5625rem;
}

.sb-group:first-child .sb-section-rule { margin-top: 0.125rem; }

.sb-item {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: flex-start;
  gap: 0.6875rem;
  width: 100%;
  height: auto;
  min-height: 2.375rem;
  padding: 0.5rem 0.625rem;
  margin-bottom: 2px;
  border: 0;
  border-radius: 0.625rem;
  font-size: 0.8125rem;
  font-weight: 500;
  color: var(--color-slate-600);
  background: transparent;
  cursor: pointer;
  transition: background-color 150ms, color 150ms, box-shadow 150ms;
  text-align: left;
}

.sb-nav.is-rail .sb-item { padding: 0.5rem 0; justify-content: center; }

.sb-item:hover { background: var(--color-slate-100); color: var(--color-slate-900); }

/* The glyph sits behind its label until the row is active or hovered, so a
   column of icons doesn't compete with the one row that matters. */
.sb-item :deep(.nav-icon) { opacity: 0.72; }
.sb-item:hover :deep(.nav-icon),
.sb-item.is-active :deep(.nav-icon) { opacity: 1; }

.sb-item.is-active {
  background: linear-gradient(135deg, var(--color-blue-800), var(--color-blue-500));
  color: #fff;
  font-weight: 600;
  box-shadow: 0 4px 12px rgba(30, 64, 175, 0.25);
}

.sb-item.is-active:hover {
  background: linear-gradient(135deg, var(--color-blue-800), var(--color-blue-500));
  color: #fff;
}

.sb-item-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* Tone, not decoration: red only while an agent is actually parked, so the
   colour stays worth reacting to. */
.sb-badge {
  flex-shrink: 0;
  margin-left: auto;
  background: var(--color-blue-100);
  color: var(--color-blue-800);
  font-size: 0.625rem;
  font-weight: 600;
  padding: 0.0625rem 0.375rem;
  border-radius: 0.625rem;
  min-width: 1.125rem;
  text-align: center;
  font-variant-numeric: tabular-nums;
}

.sb-badge-warning { background: var(--color-amber-100); color: var(--color-amber-800); }
.sb-badge-danger { background: var(--color-red-100); color: var(--color-red-800); }

.sb-item.is-active .sb-badge { background: rgba(255, 255, 255, 0.25); color: #fff; }

.sb-badge-dot {
  position: absolute;
  top: 5px;
  right: 5px;
  width: 8px;
  height: 8px;
  border-radius: 9999px;
  background: var(--color-blue-500);
  border: 2px solid var(--color-surface);
}

.sb-badge-dot-warning { background: var(--color-amber-500); }
.sb-badge-dot-danger { background: var(--color-red-500); }

.sb-nav::-webkit-scrollbar { width: 8px; }
.sb-nav::-webkit-scrollbar-thumb { background: var(--color-slate-300); border-radius: 4px; }
.sb-nav::-webkit-scrollbar-thumb:hover { background: var(--color-slate-400); }
</style>
