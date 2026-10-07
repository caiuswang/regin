<script setup>
// The Features tab's triage rail (design 1c): reports nobody has routed, and
// A / ⇧A / D / X / S to route them. The chords are live only while focus is inside
// the rail and not in a text field, so they never fire from the table beside it.
import { computed, nextTick, onActivated, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import TriageCard from './TriageCard.vue'
import DefectRefusal from '../defects/DefectRefusal.vue'
import { chordOf, useDefectTriage } from '../../composables/useDefectTriage'
import { invalidatePipelineCounts } from '../../composables/usePipelineCounts'
import { isProjectScope } from '../../composables/usePipelineScope'

const props = defineProps({
  scope: { type: String, default: '' },
  // A defect id to put the cursor on once the queue is read.
  focusId: { type: [Number, String], default: null },
  // Folded, the rail is a 52px strip that still counts the queue; the parent
  // owns the flag because it also owns the column width.
  folded: { type: Boolean, default: false },
})
const emit = defineEmits(['changed', 'update:folded'])

const triage = useDefectTriage()
const allProjects = computed(() => isProjectScope(props.scope))
const {
  items, total, loading, busy, error, refusal, notice, cursor, pending, blamed,
  load, reach, move, focus, begin, submit, cancel, remedy,
} = triage

const cards = ref([])
const rail = ref(null)

// A partial Accept (attributed, fix task refused) still moved a row out of
// the queue, so it refreshes the counts too.
async function after(message) {
  if (!message && !notice.value) return
  invalidatePipelineCounts()
  emit('changed', message || '')
}

async function verb(chord) {
  after(await begin(chord))
}

async function onSubmit(chord, value) {
  after(await submit(chord, value))
}

async function onRemedy(r) {
  after(await remedy(r))
}

async function onNoticeRemedy(r) {
  after(await remedy(r, notice))
}

function isTyping(el) {
  const tag = (el?.tagName || '').toLowerCase()
  return tag === 'input' || tag === 'textarea' || tag === 'select' || el?.isContentEditable
}

function onKeydown(event) {
  if (props.folded) return
  if (event.metaKey || event.ctrlKey || event.altKey || isTyping(event.target)) return
  const chord = chordOf(event)
  const key = event.key.length === 1 ? event.key.toUpperCase() : event.key
  if (chord) {
    event.preventDefault()
    verb(chord)
  } else if (key === 'J' || key === 'ArrowDown') {
    event.preventDefault()
    move(1)
    cards.value[cursor.value]?.focus()
  } else if (key === 'K' || key === 'ArrowUp') {
    event.preventDefault()
    move(-1)
    cards.value[cursor.value]?.focus()
  } else if (key === 'Escape') {
    cancel()
  }
}

// Arriving at `?triage=<id>` puts the cursor AND keyboard focus on that card,
// so A / D / X / S work without a click first.
async function focusRequested() {
  if (props.focusId == null || props.folded) return
  const index = await reach(props.focusId)
  if (index < 0) return
  focus(index)
  await nextTick()
  cards.value[index]?.focus()
  rail.value?.querySelector(`[data-testid="triage-card-${props.focusId}"]`)
    ?.scrollIntoView?.({ block: 'nearest' })
}

// Held out of the queue by a snooze no write has ended — counted by the
// server, never by subtracting one loaded page from another.
const snoozed = ref(0)
async function loadSnoozed() {
  try {
    const body = await api.get('/defects?triage=snoozed&size=1')
    snoozed.value = body.pagination?.total ?? 0
  } catch {
    snoozed.value = 0
  }
}
async function reload() {
  await Promise.all([load(), loadSnoozed()])
}

const stripCaption = computed(() => {
  if (loading.value) return ''
  return error.value ? 'unavailable' : `${total.value} waiting`
})
const foldButton = ref(null)
const unfoldButton = ref(null)
// The control that was clicked is replaced by its opposite; hand focus across
// so a keyboard user is not dropped onto <body>. A fold from `]` elsewhere on
// the page leaves focus where it was.
watch(() => props.folded, (folded) => {
  // A prompt left open on a hidden card would resurface on unfold, stale.
  if (folded) cancel()
  if (!rail.value?.contains(document.activeElement)) return
  nextTick(() => (folded ? unfoldButton.value : foldButton.value)?.$el?.focus())
})

watch(() => props.focusId, focusRequested)
// Inside a `<KeepAlive>` Vue fires `mounted` and THEN `activated` on the first
// arrival, so the guard is armed by the activation itself.
let arrived = false
onMounted(async () => {
  // Bound on the element, not the window: a chord only counts while focus is
  // inside the rail.
  rail.value?.addEventListener('keydown', onKeydown)
  await reload()
  focusRequested()
})
onBeforeUnmount(() => rail.value?.removeEventListener('keydown', onKeydown))
// Coming back to the same `?triage=<id>` fires no watcher, so the arrival
// focus is re-applied here.
onActivated(async () => {
  if (arrived) {
    await reload()
    await focusRequested()
  }
  arrived = true
})

defineExpose({ load: reload })
</script>

<template>
  <section
    ref="rail"
    class="tr"
    :class="{ 'is-folded': folded }"
    data-testid="triage-rail"
    aria-label="Triage"
  >
    <Button
      v-if="folded"
      ref="unfoldButton"
      variant="ghost"
      class="tr-strip"
      data-pl-part="strip"
      data-testid="triage-unfold"
      aria-expanded="false"
      :aria-label="stripCaption ? `Show triage, ${stripCaption}` : 'Show triage'"
      title="Show triage ( ] )"
      @click="emit('update:folded', false)"
    >
      <span class="tr-strip__icon" data-pl-part="strip-key"><Icon name="panel-right-open" :size="15" /></span>
      <span v-if="!loading && total" class="tr-strip__badge" data-testid="triage-fold-count" data-pl-part="badge">{{ total }}</span>
      <span class="tr-strip__label" data-pl-part="strip-label">Triage</span>
      <span v-if="stripCaption" class="tr-strip__caption" data-pl-part="strip-caption">{{ stripCaption }}</span>
      <kbd class="tr-strip__kbd" data-pl-part="strip-kbd">]</kbd>
    </Button>
    <template v-else>
    <header class="tr-head" data-pl-part="panel-head">
      <h2 class="tr-title" data-pl-part="panel-title">Triage</h2>
      <span class="tr-caption" data-pl-part="panel-meta">
        <span data-testid="triage-count">{{ total }}</span> waiting ·
        <span v-if="allProjects" data-testid="triage-all-projects">all projects</span>
        <template v-else>no feature is preselected</template>
      </span>
      <Button
        ref="foldButton"
        variant="ghost"
        size="icon"
        class="tr-fold"
        data-pl-part="key"
        data-pl-fold
        data-testid="triage-fold"
        aria-expanded="true"
        aria-label="Fold triage to the side"
        title="Fold to sidebar ( ] )"
        @click="emit('update:folded', true)"
      >
        <Icon name="panel-right-close" :size="15" />
      </Button>
    </header>

    <DefectRefusal v-if="notice" :refusal="notice" :busy="busy"
                   testid="triage-notice" @remedy="onNoticeRemedy" />
    <p v-if="loading" class="tr-muted" data-pl-part="note">Loading…</p>
    <p v-else-if="error" class="tr-refusal" role="alert" data-pl-part="alert">{{ error }}</p>
    <p v-else-if="!items.length" class="tr-empty" data-testid="triage-empty" data-pl-part="note">
      Nothing waiting on a routing decision.
    </p>
    <ul v-else class="tr-list" :class="{ 'is-busy': busy }">
      <TriageCard
        v-for="(defect, i) in items"
        :key="defect.id"
        :ref="el => { cards[i] = el }"
        v-model:blamed="blamed"
        :defect="defect"
        :focused="i === cursor"
        :pending="i === cursor ? pending : ''"
        :refusal="i === cursor ? refusal : null"
        :busy="busy"
        @focus="focus(i)"
        @verb="verb"
        @submit="onSubmit"
        @cancel="cancel"
        @remedy="onRemedy"
      />
    </ul>

    <router-link
      v-if="snoozed" to="/defects?triage=snoozed"
      class="tr-snoozed focus-visible:outline-2 focus-visible:outline-blue-500"
      data-testid="triage-snoozed"
      data-pl-part="panel-meta"
    ><Icon name="pause" :size="12" /> {{ snoozed }} snoozed</router-link>
    <router-link to="/defects" class="tr-all focus-visible:outline-2 focus-visible:outline-blue-500"
                 data-testid="triage-all-defects" data-pl-part="panel-link">All defects →</router-link>
    </template>
  </section>
</template>

<style scoped>
.tr { display: flex; flex-direction: column; gap: 0.75rem; min-width: 0; }
.tr-head { display: flex; align-items: baseline; gap: 0.6rem; flex-wrap: wrap; }
.tr-fold { align-self: center; margin-left: auto; width: 28px; height: 28px; flex-shrink: 0; }
.tr.is-folded { height: 100%; }
.tr-strip {
    display: flex; flex-direction: column; justify-content: flex-start; gap: 12px;
    width: 100%; height: 100%; min-height: 22rem; padding: 14px 0; border-radius: 0;
    color: var(--color-fg);
}
.tr-strip__icon {
    width: 32px; height: 32px; display: flex; align-items: center; justify-content: center;
    border: 1px solid var(--color-border); border-radius: var(--radius-lg);
    background: var(--color-surface); color: var(--color-fg-muted);
}
.tr-strip__badge {
    min-width: 22px; height: 22px; padding: 0 6px; box-sizing: border-box;
    display: flex; align-items: center; justify-content: center; border-radius: 99px;
    background: var(--color-warning-soft); color: var(--color-warning-strong);
    font-size: 11.5px; font-weight: 700;
}
.tr-strip__label { writing-mode: vertical-rl; font-size: 13px; font-weight: 600; letter-spacing: 0.02em; }
.tr-strip__caption { writing-mode: vertical-rl; font-size: 11.5px; color: var(--color-fg-muted); }
.tr-strip__kbd {
    margin-top: auto; padding: 1px 5px; font: 10.5px var(--font-mono, ui-monospace, monospace);
    border: 1px solid var(--color-border); border-radius: var(--radius-sm, 4px); color: var(--color-fg-muted);
}
/* Stacked under the table there is no column to fold into: the strip lies flat. */
@media (max-width: 1023.98px) {
    .tr-strip { flex-direction: row; min-height: 0; padding: 0.5rem 0.75rem; }
    .tr-strip__label, .tr-strip__caption { writing-mode: horizontal-tb; }
    .tr-strip__kbd { margin-top: 0; margin-left: auto; }
}
.tr-title { font-size: 14px; font-weight: 700; color: var(--color-fg); }
.tr-caption { font-size: 12px; color: var(--color-fg-muted); }
.tr-list { display: flex; flex-direction: column; gap: 0.6rem; }
.tr-list.is-busy { opacity: 0.6; }
.tr-muted, .tr-empty { font-size: 12.5px; color: var(--color-fg-muted); }
.tr-empty {
    border: 1px dashed var(--color-border); border-radius: var(--radius-xl);
    padding: 1rem; text-align: center;
}
.tr-refusal {
    border-radius: var(--radius-lg); background: var(--color-danger-soft);
    padding: 0.35rem 0.6rem; font-size: 11.5px; color: var(--color-danger);
}
.tr-all { align-self: flex-start; font-size: 12px; color: var(--color-primary); text-decoration: none; }
.tr-all:hover { text-decoration: underline; }
.tr-snoozed {
    display: flex; align-items: center; gap: 0.4rem; align-self: flex-start;
    font-size: 12px; color: var(--color-fg-muted); text-decoration: none;
}
.tr-snoozed:hover { color: var(--color-fg); text-decoration: underline; }
</style>
