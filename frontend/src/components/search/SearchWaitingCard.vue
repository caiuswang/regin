<script setup>
// What a parked session is asking, as text to read. The palette answers
// nothing (Non-goals): there is no approve, deny or reply here, only the
// sentence saying where those happen.
import { computed } from 'vue'
import { ago } from '../../utils/pipelineFormat.js'

const props = defineProps({
  waiting: { type: Object, required: true },
})

const kicker = computed(() => {
  const label = props.waiting.kind === 'question' ? 'Asking you' : 'Wants approval'
  const since = props.waiting.since ? ago(props.waiting.since) : ''
  return since && since !== '—' ? `${label} · ${since}` : label
})
</script>

<template>
  <section
    class="waiting-card"
    :class="{ 'is-destructive': waiting.destructive }"
    data-testid="waiting-card"
    aria-label="Waiting on you"
  >
    <p class="waiting-kicker">{{ kicker }}</p>
    <p class="waiting-text">{{ waiting.text }}</p>
    <p class="waiting-note">Read-only here. ↵ attaches; reply in the trace.</p>
  </section>
</template>

<style scoped>
.waiting-card {
  margin-bottom: 0.875rem;
  padding: 0.75rem 0.875rem;
  border: 1px solid color-mix(in srgb, var(--color-warning) 35%, transparent);
  border-radius: 10px;
  background: var(--color-warning-soft);
}

.waiting-card.is-destructive {
  border: 1.5px solid var(--color-danger);
  background: var(--color-danger-soft);
}

.waiting-kicker {
  margin: 0 0 0.3125rem;
  font-size: var(--sp-t-micro);
  line-height: var(--sp-lh-micro);
  font-weight: 700;
  letter-spacing: 0.085em;
  text-transform: uppercase;
  color: var(--color-warning-strong);
}

.is-destructive .waiting-kicker { color: var(--color-danger-strong); }

.waiting-text {
  margin: 0;
  font-size: var(--sp-t-body);
  line-height: var(--sp-lh-body);
  font-weight: 600;
  color: var(--color-fg);
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}

.waiting-note {
  margin: 0.4375rem 0 0;
  font-size: var(--sp-t-micro);
  color: var(--color-fg-muted);
}
</style>
