<script setup>
// The feature page's one jump surface: every drawn section, in page order,
// with the count that section itself draws.
//
// A jump REPLACES the hash rather than pushing it, so Back leaves the page
// instead of replaying every in-page jump the reader made on it.
//
// "Where am I" is driven by IntersectionObserver callbacks against the scroll
// pane, never by a scroll listener or a poll.
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { reveal } from '../../utils/featureLinks'

const props = defineProps({
  // [{ key, anchor, label, count }] — only the sections the page draws.
  entries: { type: Array, required: true },
  // The id of an element at the very foot of the page. Its visibility is how
  // the index knows the pane cannot scroll any further.
  endId: { type: String, required: true },
})

const router = useRouter()
const nav = ref(null)
const current = ref('')
let lineObserver = null
let endObserver = null
let root = null
let line = 0
let atEnd = false
// The entry just clicked. A short section near the foot of the page can never
// reach the reading line, so without the pin the scroll the click started
// would re-mark whatever section it passed. It holds while that scroll is in
// flight (`travelling`, ended by `scrollend`), then only for as long as the
// section stays on screen — however the reader moves the page afterwards,
// scrollbar drags included.
let pinned = ''
let travelling = false

// The reading line sits this far under the stuck strip; a jump lands a
// section's top above it (`scroll-margin-top` on the ledger).
const LINE_BELOW_NAV = 48

// The last section whose top has crossed the line; above the first section,
// the first entry.
function underLine() {
  const edge = (root ? root.getBoundingClientRect().top : 0) + line
  let hit = props.entries[0]?.anchor || ''
  for (const e of props.entries) {
    const top = document.getElementById(e.anchor)?.getBoundingClientRect().top
    if (top != null && top <= edge) hit = e.anchor
  }
  return hit
}

function onScreen(anchor) {
  const r = document.getElementById(anchor)?.getBoundingClientRect()
  if (!r) return false
  const pane = root ? root.getBoundingClientRect() : { top: 0, bottom: window.innerHeight }
  return r.bottom > pane.top + line && r.top < pane.bottom
}

function settle() {
  if (pinned && !travelling && !onScreen(pinned)) pinned = ''
  const read = underLine()
  if (pinned) current.value = pinned
  // At the foot the later sections are all on screen and none can climb to
  // the line; the reader has scrolled as far as the page goes, to its end. A
  // page short enough to show its foot unscrolled is read from the top.
  else if (atEnd && root?.scrollTop > 0) current.value = props.entries.at(-1)?.anchor || read
  else current.value = read
}

// `scrollend` is an event, not a clock. Where a browser lacks it the pin
// simply holds until the reader's own input below releases it.
function landed() {
  travelling = false
  settle()
}

// Wheel, touch, key or pointer (a scrollbar press) on the pane: the page is
// the reader's again. A click on an entry re-pins after this fires.
function release() {
  pinned = ''
  travelling = false
  settle()
}

const RELEASE_ON = ['wheel', 'touchstart', 'keydown', 'pointerdown']

function disconnect() {
  lineObserver?.disconnect()
  endObserver?.disconnect()
  for (const kind of RELEASE_ON) root?.removeEventListener(kind, release)
  root?.removeEventListener('scrollend', landed)
}

// A one-pixel band on the reading line: the observer calls back exactly when
// some section's edge crosses it, and `settle` reads the order from there.
function observe() {
  disconnect()
  root = nav.value?.closest('.content-scroll') || null
  line = (nav.value?.offsetHeight || 0) + LINE_BELOW_NAV
  const height = root?.clientHeight || window.innerHeight
  lineObserver = new IntersectionObserver(settle, {
    root, rootMargin: `-${line}px 0px -${Math.max(0, height - line - 1)}px 0px`,
  })
  for (const e of props.entries) {
    const el = document.getElementById(e.anchor)
    if (el) lineObserver.observe(el)
  }
  endObserver = new IntersectionObserver((records) => {
    atEnd = records.at(-1).isIntersecting
    settle()
  }, { root })
  const foot = document.getElementById(props.endId)
  if (foot) endObserver.observe(foot)
  for (const kind of RELEASE_ON) root?.addEventListener(kind, release, { passive: true })
  root?.addEventListener('scrollend', landed)
}

onMounted(() => nextTick(observe))
watch(() => props.entries.map(e => e.anchor).join(), () => nextTick(observe))
onBeforeUnmount(disconnect)

function jump(entry) {
  pinned = entry.anchor
  travelling = !onScreen(entry.anchor)
  current.value = entry.anchor
  reveal(entry.anchor)
  router.replace({ hash: `#${entry.anchor}` })
}
</script>

<template>
  <nav ref="nav" class="fsn" aria-label="Sections of this feature" data-testid="feature-section-nav">
    <ul class="fsn-list">
      <li v-for="e in entries" :key="e.key">
        <a
          :href="`#${e.anchor}`"
          class="fsn-link focus-visible:outline-2 focus-visible:outline-blue-500"
          :aria-current="current === e.anchor ? 'location' : undefined"
          :data-testid="`section-nav-${e.key}`"
          @click.prevent="jump(e)"
        >{{ e.label }}<span v-if="e.count" class="fsn-count">{{ e.count }}</span></a>
      </li>
    </ul>
    <!-- Outside the list, so the actions never scroll away with the links and
         are never counted as a section. -->
    <div v-if="$slots.default" class="fsn-actions" data-testid="section-nav-actions">
      <slot />
    </div>
  </nav>
</template>

<style scoped>
/* Pinned by the `.content-scroll` padding (1.5rem, 1rem on phones) above the
   content edge, so the strip covers that band instead of letting the page
   scroll through the gap over it. */
.fsn {
    position: sticky; top: -1.5rem; z-index: 20;
    display: flex; align-items: center; gap: 0.5rem;
    min-width: 0;
    background: var(--color-surface);
    border-bottom: 1px solid var(--color-border);
}
.fsn-list {
    flex: 1 1 auto; min-width: 0;
    display: flex; gap: 0.25rem;
    overflow-x: auto; white-space: nowrap;
    padding: 0.35rem 0;
    scrollbar-width: none;
}
.fsn-list::-webkit-scrollbar { display: none; }
.fsn-actions { flex: none; display: flex; align-items: center; gap: 0.25rem; }
@media (max-width: 767px) {
    .fsn { top: -1rem; }
}
.fsn-link {
    display: inline-flex; align-items: baseline; gap: 0.35rem;
    padding: 0.25rem 0.55rem; border-radius: 0.375rem;
    font-size: 0.8125rem; font-weight: 500;
    color: var(--color-fg-muted); text-decoration: none;
}
.fsn-link:hover { color: var(--color-fg); background: var(--color-surface-2); }
.fsn-link:focus-visible { outline: 2px solid var(--color-blue-500); outline-offset: -2px; }
.fsn-link[aria-current="location"] {
    color: var(--color-fg); background: var(--color-surface-2);
}
.fsn-count {
    font-size: 11.5px; color: var(--color-fg-subtle);
    font-variant-numeric: tabular-nums;
}
</style>
