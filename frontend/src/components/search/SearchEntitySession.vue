<script setup>
// A session's preview body (R5): the read-only waiting card when it is parked
// on a human, its latest prompts (each opens the trace at its turn), and the
// tail of what it last did.
import { computed } from 'vue'
import SearchWaitingCard from './SearchWaitingCard.vue'
import { ago } from '../../utils/pipelineFormat.js'

const props = defineProps({
  node: { type: Object, required: true },
})
defineEmits(['go'])

const TAIL_ROWS = 6

const prompts = computed(() => (props.node.data?.prompts || []).map(p => ({
  key: p.span_id,
  text: p.text,
  when: ago(p.ts),
  href: `/trace/sessions/${props.node.id}?span=${encodeURIComponent(p.span_id)}`,
})))

const tail = computed(() => (props.node.session?.tail || []).slice(-TAIL_ROWS).map((t, i) => ({
  key: `${t.ts}-${i}`,
  who: (t.kind || t.name || '').replace(/^tool\./, ''),
  text: t.text,
})))
</script>

<template>
  <div class="ent-session">
    <SearchWaitingCard v-if="node.data?.waiting" :waiting="node.data.waiting" />

    <section aria-label="Latest prompts">
      <h3 class="ent-kicker">Latest prompts <span>{{ prompts.length }}</span></h3>
      <p v-if="!prompts.length" class="ent-empty">No prompts yet</p>
      <a
        v-for="p in prompts"
        :key="p.key"
        :href="p.href"
        class="ent-prompt focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        @click.prevent="$emit('go', p.href)"
      >
        <span class="ent-prompt-text">{{ p.text }}</span>
        <span class="ent-prompt-when">{{ p.when }} ↗</span>
      </a>
    </section>

    <section v-if="tail.length" aria-label="Tail" class="ent-tail-wrap">
      <h3 class="ent-kicker">Tail</h3>
      <div class="ent-tail">
        <p v-for="t in tail" :key="t.key"><b>{{ t.who }} ▸</b> {{ t.text }}</p>
      </div>
    </section>
  </div>
</template>

<style scoped>
.ent-kicker {
  display: flex;
  align-items: center;
  gap: 0.4375rem;
  margin: 0 0 0.375rem;
  font-size: var(--sp-t-micro);
  line-height: var(--sp-lh-micro);
  font-weight: 700;
  letter-spacing: 0.085em;
  text-transform: uppercase;
  color: var(--color-fg-muted);
}

.ent-kicker span { font-family: var(--font-mono, ui-monospace, monospace); font-weight: 600; }

.ent-kicker::after { content: ''; flex: 1; height: 1px; background: var(--color-border); }

.ent-empty { margin: 0 0 0.875rem; font-size: var(--sp-t-meta); color: var(--color-fg-faint); }

.ent-prompt {
  display: flex;
  align-items: center;
  gap: 0.625rem;
  margin-bottom: 0.375rem;
  padding: 0.5rem 0.6875rem;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: var(--color-surface);
  font-size: var(--sp-t-body);
  color: var(--color-fg);
  text-decoration: none;
}

.ent-prompt:hover { border-color: var(--color-border-strong); }

.ent-prompt-text { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.ent-prompt-when {
  flex-shrink: 0;
  font-family: var(--font-mono, ui-monospace, monospace);
  font-size: var(--sp-t-micro);
  color: var(--color-fg-subtle);
}

.ent-tail-wrap { margin-top: 0.875rem; }

.ent-tail {
  padding: 0.5rem 0.625rem;
  border-radius: 8px;
  background: var(--color-surface-2);
  font-family: var(--font-mono, ui-monospace, monospace);
  font-size: var(--sp-t-micro);
  line-height: var(--sp-lh-micro);
  color: var(--color-fg-muted);
}

.ent-tail p { margin: 0 0 0.25rem; overflow-wrap: anywhere; }

.ent-tail b { font-weight: 600; color: var(--color-fg-subtle); }
</style>
