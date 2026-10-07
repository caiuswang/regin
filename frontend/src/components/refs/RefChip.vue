<script setup>
// One reference drawn as a chip: in a rendered body, where `MarkdownContent`
// mounts one over each reference, and in a feature's Referenced by list.
//
// Every string here reaches the DOM as text, never as markup (I6): a title is
// whatever somebody typed, and a chip is the one place it is drawn inside a
// `v-html` body. Navigation is the container's delegated click
// (`followChip`), because a chip mounted into a body has no router of its own.
import { computed } from 'vue'
import Icon from '../ui/Icon.vue'
import { REF_GLYPHS, shortKey } from './refDisplay.js'

const props = defineProps({
  // `kind:key`, exactly the href written in the body.
  target: { type: String, required: true },
  // The stored label, unescaped: what the chip reads until `item` lands.
  label: { type: String, default: '' },
  // The live `RefItem`; null when the target does not exist, absent while the
  // answer is in flight.
  item: { type: Object, default: undefined },
})

const TONE = {
  building: 'info', shipped: 'success', escaped: 'warning',
  armed: 'info', queued: 'info', running: 'info', done: 'success', failed: 'danger',
  open: 'warning', closed: 'success',
}

const kind = computed(() => props.target.slice(0, props.target.indexOf(':')))

const state = computed(() => {
  const item = props.item
  if (item === undefined) return 'pending'
  if (item === null) return 'missing'
  if (item.restricted) return 'restricted'
  return item.exists === false ? 'gone' : 'live'
})

const live = computed(() => state.value === 'live')

// Only an in-app path: the url is the server's, but it lands in an href, and
// nothing a body says should get to pick a scheme there.
const href = computed(() => {
  const url = live.value ? props.item.url : null
  return typeof url === 'string' && url.startsWith('/') && !url.startsWith('//') ? url : null
})

const text = computed(() => {
  if (state.value === 'gone') return props.item.title || props.label
  return props.label || props.target
})

const tip = computed(() => {
  if (state.value === 'missing') return `${props.target} — not found`
  if (state.value === 'restricted') return 'session — admin only'
  if (state.value === 'gone') return `${text.value} — not in the repo`
  if (!live.value) return undefined
  if (kind.value === 'file') return props.item.title
  const parent = props.item.feature_title ? ` · F${props.item.feature_id} ${props.item.feature_title}` : ''
  return `${shortKey(props.item)} ${props.item.title}${parent}`
})
</script>

<template>
  <component
    :is="href ? 'a' : 'span'"
    :href="href || undefined"
    class="ref-chip"
    :class="`is-${state}`"
    :title="tip"
    :data-ref="target"
    :data-kind="kind"
    :data-state="state"
    :data-status="live ? item.status || undefined : undefined"
    data-testid="ref-chip"
  >
    <Icon :name="REF_GLYPHS[kind] || 'file'" :size="12" class="ref-chip-glyph" />
    <template v-if="live">
      <span class="ref-chip-key" :class="{ 'is-path': kind === 'file' }">{{ shortKey(item) }}</span>
      <template v-if="kind !== 'file' && item.title">
        {{ ' ' }}<span class="ref-chip-title">{{ item.title }}</span>
      </template>
      <span
        v-if="item.status"
        class="ref-chip-dot"
        :data-tone="TONE[item.status] || 'idle'"
        role="img"
        :aria-label="item.status"
        :title="item.status"
      />
    </template>
    <span v-else class="ref-chip-label">{{ text }}</span>
  </component>
</template>

<style scoped>
.ref-chip {
    display: inline-flex;
    align-items: center;
    gap: 0.25rem;
    max-width: 100%;
    padding: 0 0.375rem;
    border: 1px solid var(--color-border);
    border-radius: var(--radius-md);
    background: var(--color-surface);
    color: var(--color-fg);
    font-size: 0.95em;
    line-height: 1.5;
    vertical-align: baseline;
    white-space: nowrap;
    overflow: hidden;
    text-decoration: none;
}
a.ref-chip:hover { border-color: var(--color-primary); }
a.ref-chip:focus-visible { outline: 2px solid var(--color-ring); outline-offset: 1px; }
.ref-chip-glyph { flex: none; color: var(--color-fg-subtle); }
.ref-chip-key {
    flex: none;
    font-family: var(--font-mono, ui-monospace, monospace);
    font-size: 0.9em;
    color: var(--color-fg-muted);
}
.ref-chip-key.is-path { flex: 0 1 auto; color: var(--color-fg); }
.ref-chip-key.is-path,
.ref-chip-title,
.ref-chip-label {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
}
.ref-chip-dot {
    flex: none;
    width: 0.4375rem;
    height: 0.4375rem;
    border-radius: 999px;
    background: var(--color-fg-faint);
}
.ref-chip-dot[data-tone="info"] { background: var(--color-info); }
.ref-chip-dot[data-tone="success"] { background: var(--color-success); }
.ref-chip-dot[data-tone="warning"] { background: var(--color-warning); }
.ref-chip-dot[data-tone="danger"] { background: var(--color-danger); }
.ref-chip.is-pending,
.ref-chip.is-restricted { color: var(--color-fg-muted); }
.ref-chip.is-missing,
.ref-chip.is-gone {
    color: var(--color-fg-faint);
    border-style: dashed;
    text-decoration: line-through;
}
</style>
