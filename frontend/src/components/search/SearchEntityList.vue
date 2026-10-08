<script setup>
// One of the entity preview's lists (R5): at most five rows, each a node ⌥→
// can follow, and an "All N →" node when the server holds rows back. An empty
// list is one muted line, never a header over nothing.
import { computed } from 'vue'
import Button from '../ui/Button.vue'

const props = defineProps({
  list: { type: Object, required: true },
  index: { type: Number, required: true },
  pickKey: { type: String, default: null },
})
defineEmits(['follow'])

const EMPTY = {
  Tasks: 'No tasks yet',
  'Open defects': 'No open defects',
  'Recent sessions': 'No sessions yet',
  Runs: 'No runs yet',
  'Defects it fixes': 'Fixes no defects',
  'Defects reported here': 'No defects reported here',
}

const rows = computed(() => props.list.items.map((row, i) => ({
  row, key: `list:${props.index}:${i}`,
})))
const allKey = computed(() => `all:${props.index}`)
const more = computed(() => props.list.total > props.list.items.length)
const noun = computed(() => props.list.label.toLowerCase())
</script>

<template>
  <section class="ent-list" :aria-label="list.label">
    <p v-if="!list.total" class="ent-empty">{{ EMPTY[list.label] || `No ${noun}` }}</p>
    <template v-else>
      <h3 class="ent-kicker">{{ list.label }} <span>{{ list.total }}</span></h3>
      <Button
        v-for="r in rows"
        :key="r.key"
        variant="ghost"
        size="sm"
        class="ent-item"
        :class="{ 'is-picked': r.key === pickKey }"
        :data-pick="r.key"
        @click="$emit('follow', r.key)"
      >
        <span class="ent-dot" :data-bucket="r.row.status?.bucket" aria-hidden="true" />
        <span class="ent-ref">{{ r.row.ref }}</span>
        <span class="ent-title">{{ r.row.title }}</span>
        <span class="ent-word">{{ r.row.aside || r.row.status?.word }}</span>
        <kbd v-if="r.key === pickKey">⌥→</kbd>
      </Button>
      <Button
        v-if="more"
        variant="ghost"
        size="sm"
        class="ent-item ent-all"
        :class="{ 'is-picked': allKey === pickKey }"
        :data-pick="allKey"
        @click="$emit('follow', allKey)"
      >All {{ list.total }} {{ noun }} →</Button>
    </template>
  </section>
</template>

<style scoped>
.ent-list { margin-bottom: 0.875rem; }

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

.ent-empty {
  margin: 0 0 0.875rem;
  font-size: var(--sp-t-meta);
  color: var(--color-fg-faint);
}

.ent-item {
  display: flex;
  align-items: center;
  justify-content: flex-start;
  height: auto;
  gap: 0.5rem;
  width: 100%;
  min-width: 0;
  padding: 0.4375rem 0.5rem;
  border: 1px solid transparent;
  border-radius: 8px;
  background: transparent;
  text-align: left;
  font-size: var(--sp-t-body);
  line-height: 1.38;
  color: var(--color-fg);
  cursor: pointer;
}

.ent-item:hover { background: var(--color-surface-2); }

.ent-item.is-picked {
  border-color: var(--color-border-strong);
  background: var(--color-surface);
  box-shadow: 0 1px 2px rgb(0 0 0 / 0.06);
}

.ent-dot {
  flex-shrink: 0;
  width: 7px;
  height: 7px;
  border-radius: 999px;
  background: var(--color-fg-faint);
}

.ent-dot[data-bucket='needs_you'] { background: var(--color-warning); }
.ent-dot[data-bucket='active'] { background: var(--color-success); }
.ent-dot[data-bucket='done'] { background: var(--color-info); }

.ent-ref {
  flex-shrink: 0;
  font-family: var(--font-mono, ui-monospace, monospace);
  font-size: 0.93em;
  font-weight: 600;
  color: var(--color-fg-muted);
}

.ent-title { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.ent-word {
  flex-shrink: 0;
  font-size: var(--sp-t-micro);
  color: var(--color-fg-subtle);
}

.ent-all { font-size: var(--sp-t-meta); font-weight: 600; color: var(--color-fg-muted); }

kbd {
  flex-shrink: 0;
  font-family: var(--font-mono, ui-monospace, monospace);
  font-size: var(--sp-t-kbd);
  background: var(--color-surface-2);
  border: 1px solid var(--color-border);
  color: var(--color-fg-muted);
  padding: 1px 5px;
  border-radius: 4px;
}
</style>
