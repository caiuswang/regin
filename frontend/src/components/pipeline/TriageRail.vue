<script setup>
// The Features tab's triage rail (design 1c): reports nobody has routed, and
// A / D / X / S to route them. The chords are live only while focus is inside
// the rail and not in a text field, so they never fire from the table beside it.
import { computed, nextTick, onActivated, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import TriageCard from './TriageCard.vue'
import { useDefectTriage, VERBS } from '../../composables/useDefectTriage'
import { invalidatePipelineCounts } from '../../composables/usePipelineCounts'
import { defectRef } from '../../utils/pipelineFormat'

const props = defineProps({
  scope: { type: String, default: '' },
  unattributed: { type: Number, default: 0 },
  // A defect id to put the cursor on once the queue is read.
  focusId: { type: [Number, String], default: null },
  // Folded, the rail is a 52px strip that still counts the queue; the parent
  // owns the flag because it also owns the column width.
  folded: { type: Boolean, default: false },
})
const emit = defineEmits(['changed', 'update:folded'])

const triage = useDefectTriage(() => props.scope)
const {
  items, total, loading, busy, error, refusal, cursor, pending, blamed,
  load, move, focus, begin, submit, cancel,
} = triage

const cards = ref([])
const rail = ref(null)

async function after(message) {
  if (!message) return
  invalidatePipelineCounts()
  emit('changed', message)
}

async function verb(chord) {
  after(await begin(chord))
}

async function onSubmit(chord, value) {
  after(await submit(chord, value))
}

function isTyping(el) {
  const tag = (el?.tagName || '').toLowerCase()
  return tag === 'input' || tag === 'textarea' || tag === 'select' || el?.isContentEditable
}

function onKeydown(event) {
  if (props.folded) return
  if (event.metaKey || event.ctrlKey || event.altKey || isTyping(event.target)) return
  const key = event.key.length === 1 ? event.key.toUpperCase() : event.key
  if (VERBS[key]) {
    event.preventDefault()
    verb(key)
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

function focusRequested() {
  if (props.focusId == null) return
  const index = items.value.findIndex(d => String(d.id) === String(props.focusId))
  if (index >= 0) focus(index)
}

// Everything open and unattributed that is not in the queue — a snoozed report, say.
const older = computed(() => Math.max(0, props.unattributed - total.value))
const showOlder = ref(false)
const olderRows = ref([])
async function toggleOlder() {
  showOlder.value = !showOlder.value
  if (!showOlder.value) return
  const body = await api.get('/defects?scope=unattributed&status=open&sort=opened:desc&size=50')
  const queued = new Set(items.value.map(d => d.id))
  olderRows.value = (body.items || []).filter(d => !queued.has(d.id))
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

watch(() => props.scope, load)
watch(() => props.focusId, focusRequested)
// Inside a `<KeepAlive>` Vue fires `mounted` and THEN `activated` on the first
// arrival, so the guard is armed by the activation itself.
let arrived = false
onMounted(async () => {
  // Bound on the element, not the window: a chord only counts while focus is
  // inside the rail.
  rail.value?.addEventListener('keydown', onKeydown)
  await load()
  focusRequested()
})
onBeforeUnmount(() => rail.value?.removeEventListener('keydown', onKeydown))
onActivated(() => { if (arrived) load(); arrived = true })

defineExpose({ load })
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
      data-testid="triage-unfold"
      aria-expanded="false"
      :aria-label="stripCaption ? `Show triage, ${stripCaption}` : 'Show triage'"
      title="Show triage ( ] )"
      @click="emit('update:folded', false)"
    >
      <span class="tr-strip__icon"><Icon name="panel-right-open" :size="15" /></span>
      <span v-if="!loading && total" class="tr-strip__badge" data-testid="triage-fold-count">{{ total }}</span>
      <span class="tr-strip__label">Triage</span>
      <span v-if="stripCaption" class="tr-strip__caption">{{ stripCaption }}</span>
      <kbd class="tr-strip__kbd">]</kbd>
    </Button>
    <template v-else>
    <header class="tr-head">
      <h2 class="tr-title">Triage</h2>
      <span class="tr-caption">
        <span data-testid="triage-count">{{ total }}</span> waiting · no feature is preselected
      </span>
      <Button
        ref="foldButton"
        variant="ghost"
        size="icon"
        class="tr-fold"
        data-testid="triage-fold"
        aria-expanded="true"
        aria-label="Fold triage to the side"
        title="Fold to sidebar ( ] )"
        @click="emit('update:folded', true)"
      >
        <Icon name="panel-right-close" :size="15" />
      </Button>
    </header>

    <p v-if="loading" class="tr-muted">Loading…</p>
    <p v-else-if="error" class="tr-refusal" role="alert">{{ error }}</p>
    <p v-else-if="!items.length" class="tr-empty" data-testid="triage-empty">
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
        :refusal="i === cursor ? refusal : ''"
        :busy="busy"
        @focus="focus(i)"
        @verb="verb"
        @submit="onSubmit"
        @cancel="cancel"
      />
    </ul>

    <div v-if="older" class="tr-older" data-testid="triage-older">
      <Icon name="alert-triangle" :size="12" />
      <span>{{ older }} older defect{{ older === 1 ? '' : 's' }} still unattributed</span>
      <Button
        variant="link"
        size="sm"
        class="tr-older__open"
        :aria-expanded="showOlder"
        @click="toggleOlder"
      >{{ showOlder ? 'Hide' : 'Open →' }}</Button>
    </div>
    <ul v-if="showOlder" class="tr-older-list">
      <li v-for="d in olderRows" :key="d.id">
        <router-link :to="`/defects/${d.id}`" class="tr-older-link focus-visible:outline-2 focus-visible:outline-blue-500">
          <b>{{ defectRef(d.id) }}</b> {{ d.symptom }}
        </router-link>
      </li>
    </ul>
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
.tr-older { display: flex; align-items: center; gap: 0.4rem; font-size: 12px; color: var(--color-fg-muted); }
.tr-older .tr-older__open { margin-left: auto; font-size: 12px; }
.tr-older-list { display: flex; flex-direction: column; gap: 0.3rem; font-size: 12px; }
.tr-older-link {
    display: block; color: var(--color-fg-muted); text-decoration: none;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.tr-older-link b { font-family: var(--font-mono, monospace); color: var(--color-fg); }
</style>
