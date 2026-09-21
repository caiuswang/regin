<script setup>
// The triage queue: the defects nobody has routed yet, and four keys that
// route them.
//
// A queue an operator routes FROM, never a classifier that assigns a parent.
// Every verb here is the operator's assertion — `1` collects a feature id they
// type, and nothing on this surface reads a suspect path, a reporting session
// or a cost to propose one. A wrong parent inflates one feature's rounds and
// exonerates another, which is corrupted data that looks fine.
//
// The count beside the heading is `pagination.total` off the very read that
// produced the rows, so the badge and the queue cannot disagree: they are one
// statement in one session, not two questions asked separately.
import { onActivated, onMounted } from 'vue'
import DefectTriageCard from './DefectTriageCard.vue'
import { useDefectTriage, VERBS } from '../../composables/useDefectTriage'
import { useTaskShortcuts } from '../../composables/useTaskShortcuts'

const emit = defineEmits(['changed'])

// Destructured so the template auto-unwraps the refs: reached through an
// object they stay `.value` in markup, which is the kind of difference that
// renders `[object Object]` in one place and the number in another.
const {
  items, total, loading, busy, error, refusal, cursor, pending,
  load, move, focus, begin, submit, cancel,
} = useDefectTriage()

// The legend, derived from the verb table rather than retyped beside it — a
// second spelling of a chord is how a key stops matching the label that
// claims it.
const LEGEND = Object.entries(VERBS).map(([chord, verb]) => ({
  chord, label: verb.label,
}))

async function run(chord) {
  const message = await begin(chord)
  if (message) emit('changed', message)
}

async function onSubmit(chord, value) {
  const message = await submit(chord, value)
  if (message) emit('changed', message)
}

useTaskShortcuts({
  J: () => move(1),
  K: () => move(-1),
  j: () => move(1),
  k: () => move(-1),
  Escape: () => cancel(),
  ...Object.fromEntries(
    Object.keys(VERBS).map(chord => [chord, () => run(chord)])),
})

// Inside a `<KeepAlive>` Vue fires `mounted` and THEN `activated` on the first
// arrival, so a flag set in `onMounted` is already true by the time the
// activation guard reads it — which is how this read the queue twice for one
// arrival. The flag is therefore set by the activation itself: the first one
// is the mount's and is skipped, every later one is a return and reloads.
let arrived = false
onMounted(load)
onActivated(() => { if (arrived) load(); arrived = true })
</script>

<template>
  <section class="queue" data-testid="defect-triage">
    <header class="q-head">
      <h2 class="q-title">
        Triage
        <span class="q-count" data-testid="triage-count">{{ total }}</span>
      </h2>
      <p class="q-lead">
        Unrouted reports, oldest first. Every exit is the operator's
        assertion — regin proposes no feature for any of them.
      </p>
      <ul class="legend" data-testid="triage-legend">
        <li v-for="item in LEGEND" :key="item.chord">
          <kbd>{{ item.chord }}</kbd> {{ item.label }}
        </li>
        <li><kbd>J</kbd>/<kbd>K</kbd> move</li>
      </ul>
    </header>

    <p v-if="loading" class="muted">Loading…</p>
    <p v-else-if="error" class="refusal" role="alert">{{ error }}</p>

    <div v-else-if="!items.length" class="empty" data-testid="triage-empty">
      <strong>the queue is clear</strong>
      <p>
        Every unattributed defect has been accepted, merged, declined or
        snoozed. Nothing is waiting on a routing decision.
      </p>
    </div>

    <ul v-else class="q-list" :class="{ 'is-busy': busy }">
      <DefectTriageCard
        v-for="(defect, i) in items"
        :key="defect.id"
        :defect="defect"
        :focused="i === cursor"
        :pending="i === cursor ? pending : ''"
        :refusal="refusal"
        :busy="busy"
        @focus="focus(i)"
        @submit="onSubmit"
        @cancel="cancel"
      />
    </ul>
  </section>
</template>

<style scoped>
.queue { display: flex; flex-direction: column; gap: 0.7rem; }
.q-head { display: flex; flex-direction: column; gap: 0.3rem; }
.q-title {
    display: flex; align-items: center; gap: 0.4rem;
    font-size: 13px; font-weight: 600; color: var(--color-fg);
}
.q-count {
    border-radius: var(--radius-lg); background: var(--color-surface-3);
    padding: 0 0.4rem; font-size: 11px; color: var(--color-fg-muted);
    font-variant-numeric: tabular-nums;
}
.q-lead { font-size: 11.5px; color: var(--color-fg-muted); max-width: 56rem; }
.legend {
    display: flex; flex-wrap: wrap; gap: 0.6rem;
    font-size: 11px; color: var(--color-fg-faint);
}
.legend kbd {
    border: 1px solid var(--color-border); border-radius: var(--radius-md);
    background: var(--color-surface-3); padding: 0 0.25rem;
    font-family: var(--font-mono, monospace); font-size: 10.5px;
    color: var(--color-fg-muted);
}
.q-list { display: flex; flex-direction: column; gap: 0.6rem; }
/* A reload in flight dims the rows it is about to replace rather than
   removing them, so the operator keeps their place. */
.q-list.is-busy { opacity: 0.6; }
.muted { font-size: 12px; color: var(--color-fg-faint); }
.empty {
    border: 1px dashed var(--color-border); border-radius: var(--radius-xl);
    padding: 1.2rem; text-align: center; color: var(--color-fg-muted);
}
.empty strong { display: block; font-size: 13px; color: var(--color-fg); }
.empty p { margin-top: 0.25rem; font-size: 11.5px; }
.refusal {
    border-radius: var(--radius-lg); background: var(--color-red-50);
    padding: 0.35rem 0.6rem; font-size: 11.5px; color: var(--color-red-700);
}
</style>
